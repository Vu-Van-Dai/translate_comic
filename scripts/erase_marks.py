"""Erase site watermarks and other groups' logos from a published chapter.

    python scripts/erase_marks.py --series S -c N            # dry run: list + sheet
    python scripts/erase_marks.py --series S -c N --apply    # erase in library/

The operator (2026-10-28): logos and watermarks are removed without the
Photoshop pass, and for chapters already published, without re-rendering --
the library page itself is cleaned in place, so no translated line is set
again. New chapters mark these regions ERASE at ocr_review instead.

Two kinds of mark. The site line ("免费看漫mw777.me", "免费漫蛙 mwmwsh.cc")
sits in a bottom corner of every page and the detector misses it on most, so
it is found by template matching (workspace/_tools/wm_templates/). Other
groups' logos are regions review dropped (re-derived from the mask, same ids)
whose OCR reading at review matched a known group name. ``--ids`` adds more, ``--skip``
removes false hits. Held and excluded pages are never touched. Each erased
region is also written into the page's analysis as kind ``erase``, so a later
render erases it too, and running this again finds nothing left to do.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comic.console import setup_console  # noqa: E402
from comic.paths import ChapterPaths  # noqa: E402

MARKS = re.compile(r"mw777|mwmw|\.me\b|漫蛙|免费|kirifuki|yayoi|霧吹|雾吹|弥生|jmc|萌木|汉化", re.I)
#: Held pages with cleanup allowed and no library file yet: target -> original.
pending_copies: dict = {}

TEMPLATES = ROOT / "workspace" / "_tools" / "wm_templates"
AUTO, CHECK = 0.6, 0.3  # site-line scores: taken / shown on the sheet for a look
MIN_CONTRAST = 20       # outline minus fill: true marks measured 36-56, a blank grey patch -1


def _parts(t):
    """The template's grey fill and white outline, each where it touches the other."""
    import cv2
    import numpy as np

    grey, white = (t > 120) & (t < 225), t > 238
    k = np.ones((3, 3), np.uint8)
    near_white = cv2.dilate(white.astype(np.uint8), k).astype(bool)
    near_grey = cv2.dilate(grey.astype(np.uint8), k).astype(bool)
    return grey & near_white, white & near_grey


def site_line(gray):
    """(score, box) of the best site-watermark match in a bottom corner.

    The line only ever sits in the bottom-left or bottom-right corner, so only
    those are tried, a few pixels either way. The art behind it differs on
    every page and grey values shift over dark art, so the test is shape, not
    grey level: the template's outline pixels must be white and its fill
    pixels grey. Score 0-1; AUTO and above is taken, CHECK-AUTO goes on the
    sheet for a look (``--pages`` takes those after checking).
    """
    import cv2
    import numpy as np

    return max(site_lines(gray), key=lambda m: m[0], default=(0.0, None))


def site_lines(gray):
    """The best match of every template: a page can carry two marks at once
    (Kawaii: a "jmcomic" logo in one bottom corner, the site line in the other)."""
    import cv2
    import numpy as np

    h, w = gray.shape
    matches = []
    for tpl in sorted(TEMPLATES.glob("*.png")):
        best = (0.0, None)
        t = cv2.imread(str(tpl), cv2.IMREAD_GRAYSCALE)
        if t is None:
            continue
        fill, ring = _parts(t)
        th, tw = t.shape
        for x0 in list(range(0, 24, 2)) + list(range(w - tw - 24, w - tw + 1, 2)):
            for y0 in range(h - th - 16, h - th + 1):
                if x0 < 0 or y0 < 0 or x0 + tw > w:
                    continue
                patch = gray[y0:y0 + th, x0:x0 + tw]
                # The letters are clearly darker than their outline; a blank
                # light-grey area passes the two tests above but not this one.
                contrast = float(patch[ring].mean()) - float(patch[fill].mean())
                score = 0.0 if contrast < MIN_CONTRAST else min(
                    float((patch[ring] > 210).mean()),
                    float(((patch[fill] > 100) & (patch[fill] < 232)).mean()))
                if score > best[0]:
                    best = (score, (x0, y0, x0 + tw, min(h, y0 + th)))
        matches.append(best)
    return matches

def library_file(paths: ChapterPaths, page: int) -> Path | None:
    for ext in ("webp", "png"):
        f = paths.library / f"{page:03d}.{ext}"
        if f.exists():
            return f
    return None


def find_marks(paths: ChapterPaths, extra: set[str], skip: set[str], skip_pages: set[int],
               also: set[int], unsure: list, boxes: dict[int, list] | None = None):
    from comic.photoshop.sfx import dropped_regions
    from comic.state.store import ChapterStore

    state = ChapterStore(paths).load()
    named: set[str] = set()
    request = paths.workspace / "handoff" / "ocr_review_request.json"
    if request.exists():
        for item in json.loads(request.read_text(encoding="utf-8")).get("items", []):
            if MARKS.search(item.get("engine_text", "")):
                named.add(item["region_id"])
    import numpy as np
    from PIL import Image

    from comic.photoshop.sfx import SfxCandidate
    from comic.schemas import BBox

    from comic.stages import analysis_for

    def overlaps(a, b) -> bool:
        ix = max(0, min(a.x2, b.x2) - max(a.x1, b.x1))
        iy = max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
        inter = ix * iy
        union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
        return union > 0 and inter / union > 0.5

    found = []
    pending_copies.clear()
    for record in state.pages:
        analysis = analysis_for(paths, record.page)
        done = [r.bbox for r in (analysis.regions if analysis else []) if r.kind.value == "erase"]
        taken = {r.id for r in (analysis.regions if analysis else [])}
        cleanup_only = record.held and record.hold_cleanup
        if (record.excluded and not cleanup_only) or record.page in skip_pages:
            continue                             # inserts, and pages held without cleanup
        target = library_file(paths, record.page)
        if target is None and cleanup_only:
            # A held page not yet in the library: its number takes the original,
            # cleaned of marks and nothing else, in the chapter's format.
            fmt = "webp" if any(paths.library.glob("*.webp")) else "png"
            source = paths.source_pages / record.filename
            if not source.exists():
                continue
            target = paths.library / f"{record.page:03d}.{fmt}"
            pending_copies[target] = source
        if target is None:
            continue
        dropped = dropped_regions(paths, record.page, record.filename)
        taken |= {c.region_id for c in dropped}

        def fresh_id() -> str:
            # Above the detector's numbering, so a mark never takes a real region's id.
            n = 90
            while f"p{record.page:03d}_r{n}" in taken:
                n += 1
            taken.add(f"p{record.page:03d}_r{n}")
            return f"p{record.page:03d}_r{n}"

        def add(bbox, site=True, rid=None):
            if any(overlaps(bbox, d) for d in done):
                return                           # already erased and recorded: idempotent
            found.append((record, target, SfxCandidate(rid or fresh_id(), record.page, bbox), site))
            done.append(bbox)

        # Detect on the original: the library copy may already be clean.
        source = paths.source_pages / record.filename
        for score, box in site_lines(np.asarray(Image.open(source).convert("L"))):
            if box is not None and (score >= AUTO or record.page in also):
                add(BBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3]))
            elif box is not None and score >= CHECK:
                unsure.append((record.page, round(score, 2), source, box))
        for x1, y1, x2, y2 in (boxes or {}).get(record.page, []):
            add(BBox(x1=x1, y1=y1, x2=x2, y2=y2))
        for cand in dropped:
            rid = cand.region_id
            if rid in skip:
                continue
            if rid in extra or rid in named:
                add(cand.bbox, site=False, rid=rid)
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", required=True)
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--pages", nargs="*", type=int, default=[],
                    help="take the site line on these pages although it scored below AUTO")
    ap.add_argument("--skip-pages", nargs="*", type=int, default=[],
                    help="publisher ad pages and the like: leave as they are")
    ap.add_argument("--box", action="append", default=[], metavar="PAGE:X1,Y1,X2,Y2",
                    help="a mark the detector never saw (a group's picture logo, a credits block)")
    ap.add_argument("--sheet", default=None, help="write the crops here for a look")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    setup_console()

    import numpy as np
    from PIL import Image

    from comic.image_processing.clean import clean_page
    from comic.image_processing.mask import fit_areas, with_erase_boxes
    from comic.photoshop.publish import save_page
    from comic.schemas import PageAnalysis, TextKind, TextRegion
    from comic.stages import analysis_for
    from comic.state.store import write_json
    from comic.vision.analyze import load_mask

    paths = ChapterPaths(args.series, args.chapter)
    unsure: list = []
    boxes: dict[int, list] = {}
    for spec in args.box:
        page, _, coords = spec.partition(":")
        boxes.setdefault(int(page), []).append(tuple(int(v) for v in coords.split(",")))
    marks = find_marks(paths, set(args.ids), set(args.skip), set(args.skip_pages),
                       set(args.pages), unsure, boxes)
    if unsure:
        print("unsure (check the sheet, pass with --pages):",
              " ".join(f"{p}({sc})" for p, sc, _, _ in unsure))
    print(f"{len(marks)} mark(s) on {len({m[0].page for m in marks})} page(s)")
    for record, _, cand, _ in marks:
        b = cand.bbox
        print(f"  {cand.region_id}  [{b.x1},{b.y1},{b.x2},{b.y2}]")

    if args.apply is False and pending_copies:
        print("held pages (cleanup only) that would be written from the original:",
              " ".join(t.name for t in pending_copies))
    if args.sheet and (marks or unsure):
        crops = []
        for _, target, cand, _ in marks:
            b = cand.bbox
            im = Image.open(pending_copies.get(target, target)).convert("RGB").crop((max(0, b.x1 - 8), max(0, b.y1 - 8), b.x2 + 8, b.y2 + 8))
            crops.append(im)
        for page, score, target, b in unsure:
            im = Image.open(pending_copies.get(target, target)).convert("RGB").crop((max(0, b[0] - 8), max(0, b[1] - 8), b[2] + 8, b[3] + 8))
            crops.append(im)
        w = max(c.width for c in crops)
        sheet = Image.new("RGB", (w, sum(c.height + 6 for c in crops)), "red")
        y = 0
        for c in crops:
            sheet.paste(c, (0, y))
            y += c.height + 6
        sheet.save(args.sheet)
        print(f"sheet -> {args.sheet}")

    if not args.apply:
        return
    model = ROOT / "models" / "inpainting_lama.onnx"
    by_page: dict[int, list] = {}
    for record, target, cand, site in marks:
        by_page.setdefault(record.page, []).append((record, target, cand, site))
    for page, items in sorted(by_page.items()):
        record, target, _, _ = items[0]
        regions = [TextRegion(id=c.region_id, page=page, bbox=c.bbox, kind=TextKind.ERASE,
                              source_text="[erase]") for _, _, c, _ in items]
        image = np.asarray(Image.open(pending_copies.get(target, target)).convert("RGB")).copy()
        page_mask = with_erase_boxes(load_mask(paths.mask_file(page)), regions)
        if page_mask.shape[:2] != image.shape[:2]:
            print(f"  page {page}: library size differs from the mask -- skipped")
            continue
        areas = fit_areas(image, page_mask, regions)
        cleaned, _, _ = clean_page(image, page_mask, regions, areas,
                                   model=model if model.exists() else None)
        tmp = target.with_name(target.stem + ".erase.png")
        Image.fromarray(cleaned).save(tmp)
        fmt = target.suffix.lstrip(".")
        save_page(tmp, target, fmt, 95)
        tmp.unlink()
        analysis = analysis_for(paths, page)
        if analysis is not None:
            known = {r.id for r in analysis.regions}
            # Recorded, so every later render erases the same marks and a second
            # run of this script finds them done.
            analysis.regions += [r for r in regions if r.id not in known]
            write_json(paths.analysis_file(page), analysis)
        print(f"  page {page:03d}: {len(regions)} erased -> {target.name}")


if __name__ == "__main__":
    main()
