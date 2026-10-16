"""Find typeset lines that spill outside their bubble.

For each rendered page, the new ink is where the typeset page differs from the
cleaned page. Each new-ink pixel belongs to the nearest translated region, and
any of it lying outside that region's own *bubble* (the light-pixel interior,
not the fit area, which may have leaked) is a spill. Prints the worst regions.

    python scripts/spill_check.py --series NAME -c N [--min 25]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.cpu import throttle  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", required=True)
    ap.add_argument("--min", type=int, default=25, help="spilled pixels to report")
    args = ap.parse_args()
    throttle()
    from comic.image_processing.mask import _inverted, bubble_interior, dilate, fit_areas
    from comic.paths import ChapterPaths
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
        regions = [r for r in analysis.regions if text.get(r.id, "").strip()]
        if not regions:
            continue
        source = next(paths.source_pages.glob(f"{page:03d}.*"))
        image, mask = load_page(source), load_mask(paths.mask_file(page))
        rendered = load_page(rendered_file).astype(np.int16)
        cleaned = load_page(cleaned_file).astype(np.int16)
        ink = (np.abs(rendered - cleaned).max(axis=-1) > 60).astype(np.uint8)
        areas = fit_areas(image, mask, regions)
        h, w = ink.shape
        dist = []
        for r in regions:
            seed = np.ones((h, w), np.uint8)
            seed[r.bbox.y1:r.bbox.y2, r.bbox.x1:r.bbox.x2] = 0
            dist.append(cv2.distanceTransform(seed, cv2.DIST_L2, 3))
        owner = np.argmin(np.stack(dist), axis=0)
        for i, r in enumerate(regions):
            mine = (ink > 0) & (owner == i)
            if not mine.any():
                continue
            bubble = None if _inverted(image, mask, r.bbox) else bubble_interior(image, mask, r.bbox)
            room = areas[r.id] if bubble is None else (bubble & areas[r.id])
            out = mine & (dilate(room, 2) == 0)
            n = int(out.sum())
            if n >= args.min:
                ys, xs = np.nonzero(out)
                print(f"{r.id} spill {n:5d}px at x{xs.min()}-{xs.max()} y{ys.min()}-{ys.max()}  {text[r.id][:40]!r}", flush=True)


if __name__ == "__main__":
    main()
