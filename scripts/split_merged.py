"""Split regions that hold two balloons' lettering, in an already-analysed chapter.

New chapters are split at detection (``vision/regions.py: split_merged``);
this applies the same rule to chapters detected before it existed.

    python scripts/split_merged.py --series NAME -c N            # list only
    python scripts/split_merged.py --series NAME -c N --apply    # split
    python scripts/split_merged.py --series NAME -c N --answers FILE.json
    python scripts/split_merged.py --series NAME -c N --sheet hits.png [--skip p004_r06,...]

``--sheet`` draws every hit with its proposed pieces boxed -- the manual
check: a region whose pieces are one balloon (a caption in two columns, a
logo) goes in ``--skip``. An SFX lettered inside a balloon is split off on
purpose: the piece is then answered on its own (``""`` keeps the original
lettering) instead of being typeset into the dialogue.

``--apply`` replaces each merged region with one region per balloon (new ids
after the page's last one, inserted at the original's place in reading
order), drops the original's translation, redraws the page overlays, and
logs the original reading and translation to ``split_log.json`` in the
chapter. The pieces have no reading yet: at ``ocr_review`` they are answered
like any region; in a chapter already past it, write them with
``--answers`` -- ``{"p011_r23": {"source": "...", "vi": "...", "speaker":
"...", "addressee": "..."}, ...}`` (an empty "source" drops the piece) --
then ``comic render`` re-renders the pages whose text changed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comic.paths import ChapterPaths  # noqa: E402
from comic.schemas import (  # noqa: E402
    ChapterTranslation, PageAnalysis, ReadingDirection, TranslationEntry, region_id,
)
from comic.stages import analysis_for, vertical_lettering, write_page_overlays  # noqa: E402
from comic.state.store import ChapterStore, read_json, write_json  # noqa: E402
from comic.vision.analyze import load_mask  # noqa: E402
from comic.vision.detector import load_page  # noqa: E402
from comic.vision.regions import RegionCandidate, order_regions, split_merged  # noqa: E402


def split_page(paths: ChapterPaths, analysis: PageAnalysis, vertical: bool, skip=frozenset()):
    """The page's regions with merged ones replaced, and {old_id: [new ids]}."""
    mask = load_mask(paths.mask_file(analysis.page))
    if mask is None:
        return None, {}
    image = load_page(paths.page_file(analysis.page))
    used = [int(m.group(1)) for r in analysis.regions if (m := re.search(r"_r(\d+)$", r.id))]
    next_n = max(used, default=0) + 1
    out, splits = [], {}
    for region in sorted(analysis.regions, key=lambda r: r.reading_order):
        candidate = RegionCandidate(
            bbox=region.bbox, kind=region.kind, density=0.0,
            is_vertical=region.is_vertical, in_bubble=True,
        )
        pieces = [] if region.id in skip else split_merged(image, mask, candidate, vertical)
        if len(pieces) < 2:
            out.append(region)
            continue
        pieces = order_regions(pieces, right_to_left=analysis.reading_direction is ReadingDirection.RTL)
        ids = []
        for piece in pieces:
            rid = region_id(analysis.page, next_n)
            next_n += 1
            ids.append(rid)
            out.append(region.model_copy(update={
                "id": rid, "bbox": piece.bbox, "kind": piece.kind,
                "is_vertical": piece.is_vertical, "polygon": [], "bubble_polygon": [],
                "source_text": "", "ocr_confidence": 0.0, "ocr_provider": None,
                "ocr_unread": None, "ocr_reviewed": False,
            }))
        splits[region.id] = ids
    for order, region in enumerate(out, start=1):
        region.reading_order = order
    return analysis.model_copy(update={"regions": out}), splits


def _thumb(paths: ChapterPaths, page: int, region, pieces):
    """The merged region on its page, each proposed piece boxed in its own colour."""
    from PIL import Image, ImageDraw

    im = Image.open(paths.page_file(page)).convert("RGB")
    b, pad = region.bbox, 30
    x0, y0 = max(0, b.x1 - pad), max(0, b.y1 - pad)
    crop = im.crop((x0, y0, min(im.width, b.x2 + pad), min(im.height, b.y2 + pad)))
    draw = ImageDraw.Draw(crop)
    for k, p in enumerate(pieces):
        colour = [(230, 0, 0), (0, 60, 230), (0, 150, 0)][k % 3]
        draw.rectangle((p.bbox.x1 - x0, p.bbox.y1 - y0, p.bbox.x2 - x0, p.bbox.y2 - y0), outline=colour, width=3)
    crop.thumbnail((320, 320))
    tile = Image.new("RGB", (320, 342), "white")
    tile.paste(crop, (0, 22))
    ImageDraw.Draw(tile).text((3, 4), f"{region.id} -> {len(pieces)}", fill=(0, 0, 0))
    return tile


def _sheet(tiles, path: Path) -> None:
    from PIL import Image

    cols = 5
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 320, rows * 342), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * 320, (i // cols) * 342))
    sheet.save(path)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--answers", type=Path)
    ap.add_argument("--skip", default="", help="region ids judged one balloon, comma separated")
    ap.add_argument("--sheet", type=Path, help="write a contact sheet of the hits (the manual check)")
    args = ap.parse_args()
    paths = ChapterPaths(args.series, args.chapter)
    state = ChapterStore(paths).load()
    log_path = paths.workspace / "split_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}

    if args.answers:
        answers = json.loads(args.answers.read_text(encoding="utf-8"))
        tr = read_json(paths.translation_file, ChapterTranslation) if paths.translation_file.exists() else None
        for record in state.pages:
            analysis = analysis_for(paths, record.page)
            if analysis is None:
                continue
            keep, changed = [], False
            for region in analysis.regions:
                a = answers.get(region.id)
                if a is None:
                    keep.append(region)
                    continue
                changed = True
                if not a.get("source", "").strip():
                    continue                       # not lettering: drop the piece
                region.source_text = a["source"].strip()
                region.ocr_reviewed, region.ocr_confidence, region.ocr_provider = True, 1.0, "agent"
                if a.get("kind"):
                    region.kind = a["kind"]
                keep.append(region)
                if tr is not None and a.get("vi"):
                    tr.entries = [e for e in tr.entries if e.region_id != region.id]
                    tr.entries.append(TranslationEntry(
                        region_id=region.id, source_text=region.source_text, target_text=a["vi"],
                        speaker=a.get("speaker"), addressee=a.get("addressee"),
                    ))
            if changed:
                analysis.regions = keep
                write_json(paths.analysis_file(record.page), analysis)
        if tr is not None:
            write_json(paths.translation_file, tr)
        print(f"{len(answers)} piece(s) answered")
        return

    tr = read_json(paths.translation_file, ChapterTranslation) if paths.translation_file.exists() else None
    by_id = tr.by_region() if tr else {}
    vertical = vertical_lettering(state)
    skip = frozenset(s.strip() for s in args.skip.split(",") if s.strip())
    touched, thumbs = [], []
    for record in state.pages:
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        new, splits = split_page(paths, analysis, vertical, skip)
        if not splits:
            continue
        old = {r.id: r for r in analysis.regions}
        for rid, pieces in splits.items():
            entry = by_id.get(rid)
            print(f"{rid} -> {', '.join(pieces)}  {old[rid].source_text[:60]!r}"
                  + (f"  vi={entry.target_text[:50]!r}" if entry else ""))
            log[rid] = {"pieces": pieces, "source": old[rid].source_text,
                        "vi": entry.target_text if entry else None,
                        "speaker": entry.speaker if entry else None,
                        "addressee": entry.addressee if entry else None}
            if args.sheet:
                thumbs.append(_thumb(paths, record.page, old[rid], [r for r in new.regions if r.id in pieces]))
        if args.apply:
            write_json(paths.analysis_file(record.page), new)
            touched.append(record.page)
            if tr is not None:
                tr.entries = [e for e in tr.entries if e.region_id not in splits]
    if args.sheet and thumbs:
        _sheet(thumbs, args.sheet)
        print(f"sheet -> {args.sheet}")
    if args.apply and touched:
        if tr is not None:
            write_json(paths.translation_file, tr)
        write_page_overlays(paths, touched)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"split on {len(touched)} page(s); log -> {log_path}")
    elif not touched and not args.apply:
        print("listed only (--apply to split)" if log else "nothing to split")


if __name__ == "__main__":
    main()
