"""Find typeset lines that sit off-centre in their bubble.

The operator's first rule (2026-10-15): "the dialogue must be centred in its
speech bubble -- off-centre text is crucial to avoid". For each rendered page
the new ink is where the typeset page differs from the cleaned one; each new
ink pixel belongs to the nearest translated region. The bubble's centre is
measured the way the fitter measures it (``fit._bubble_centre``: a cross
through the source box, tails ignored, leaks guarded), and the block's ink
box is compared against it. Prints the regions off by more than ``--tol`` of
the bubble's size (and at least ``--px`` pixels), worst first.

``comic render`` now runs this same check on every page it renders and lists
the findings in the visual_qa request (``comic/qa/placement.py``), so this
script is only for a standalone re-check.

    python scripts/centre_check.py --series NAME -c N [--tol 0.08] [--px 6]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.cpu import throttle  # noqa: E402
from comic.console import setup_console  # noqa: E402
from comic.qa.placement import CENTRE_PX, CENTRE_TOL  # noqa: E402


def main() -> None:
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", required=True)
    ap.add_argument("--tol", type=float, default=CENTRE_TOL, help="allowed offset, share of bubble size")
    ap.add_argument("--px", type=int, default=CENTRE_PX, help="allowed offset in pixels regardless")
    args = ap.parse_args()
    throttle()
    from comic.paths import ChapterPaths
    from comic.qa.placement import check_page
    from comic.qa.visual import analysis_for, load_mask, load_page

    paths = ChapterPaths(args.series, args.chapter)
    text = {e["region_id"]: e["target_text"] for e in
            json.loads(paths.translation_file.read_text(encoding="utf-8"))["entries"]}
    root = paths.translation_file.parent
    found = []
    for rendered_file in sorted((root / "translated").glob("*.*")):
        page = int(rendered_file.stem)
        analysis = analysis_for(paths, page)
        cleaned_file = next((root / "cleaned").glob(f"{page:03d}.*"), None)
        if analysis is None or cleaned_file is None:
            continue
        if not any(text.get(r.id, "").strip() for r in analysis.regions):
            continue
        source = next(paths.source_pages.glob(f"{page:03d}.*"))
        image, mask = load_page(source), load_mask(paths.mask_file(page))
        off, _ = check_page(
            image, mask, load_page(rendered_file), load_page(cleaned_file),
            analysis.regions, text, spill=False, tol=args.tol, px=args.px,
        )
        found.extend(off)
    found.sort(
        key=lambda f: (f.score, f.region_id, f.dx, f.dy, f.bubble_w, f.bubble_h, f.text),
        reverse=True,
    )
    for item in found:
        print(item.line())
    print(f"{len(found)} off-centre region(s)")


if __name__ == "__main__":
    main()
