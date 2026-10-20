"""Find typeset lines that spill outside their bubble.

For each rendered page, the new ink is where the typeset page differs from the
cleaned page. Each new-ink pixel belongs to the nearest translated region, and
any of it lying outside that region's own *bubble* (the light-pixel interior,
not the fit area, which may have leaked) is a spill. Prints the worst regions.

``comic render`` now runs this same check on every page it renders and lists
the findings in the visual_qa request (``comic/qa/placement.py``), so this
script is only for a standalone re-check.

    python scripts/spill_check.py --series NAME -c N [--min 25]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.cpu import throttle  # noqa: E402
from comic.console import setup_console  # noqa: E402
from comic.qa.placement import SPILL_MIN  # noqa: E402


def main() -> None:
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", required=True)
    ap.add_argument("--min", type=int, default=SPILL_MIN, help="spilled pixels to report")
    args = ap.parse_args()
    throttle()
    from comic.paths import ChapterPaths
    from comic.qa.placement import check_page
    from comic.qa.visual import analysis_for, load_mask, load_page

    paths = ChapterPaths(args.series, args.chapter)
    text = {e["region_id"]: e["target_text"] for e in
            json.loads(paths.translation_file.read_text(encoding="utf-8"))["entries"]}
    root = paths.translation_file.parent
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
        _, spills = check_page(
            image, mask, load_page(rendered_file), load_page(cleaned_file),
            analysis.regions, text, centre=False, minimum=args.min,
        )
        for item in spills:
            print(item.line(), flush=True)


if __name__ == "__main__":
    main()
