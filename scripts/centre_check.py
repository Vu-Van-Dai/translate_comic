"""Find typeset lines that sit off-centre in their bubble.

The operator's first rule (2026-10-15): "the dialogue must be centred in its
speech bubble -- off-centre text is crucial to avoid". For each rendered page
the new ink is where the typeset page differs from the cleaned one; each new
ink pixel belongs to the nearest translated region. The bubble's centre is
measured the way the fitter measures it (``fit._bubble_centre``: a cross
through the source box, tails ignored, leaks guarded), and the block's ink
box is compared against it. Prints the regions off by more than ``--tol`` of
the bubble's size (and at least ``--px`` pixels), worst first.

    python scripts/centre_check.py --series NAME -c N [--tol 0.08] [--px 6]
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
from comic.console import setup_console  # noqa: E402


def main() -> None:
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", required=True)
    ap.add_argument("--tol", type=float, default=0.08, help="allowed offset, share of bubble size")
    ap.add_argument("--px", type=int, default=6, help="allowed offset in pixels regardless")
    args = ap.parse_args()
    throttle()
    from comic.image_processing.mask import _inverted, bubble_interior, fit_areas
    from comic.paths import ChapterPaths
    from comic.qa.visual import analysis_for, load_mask, load_page
    from comic.typesetting.fit import _bubble_centre

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
        regions = [r for r in analysis.regions if text.get(r.id, "").strip()]
        if not regions:
            continue
        source = next(paths.source_pages.glob(f"{page:03d}.*"))
        image, mask = load_page(source), load_mask(paths.mask_file(page))
        rendered = load_page(rendered_file).astype(np.int16)
        cleaned = load_page(cleaned_file).astype(np.int16)
        ink = np.abs(rendered - cleaned).max(axis=-1) > 60
        areas = fit_areas(image, mask, analysis.regions)  # as the renderer splits them
        h, w = ink.shape
        dist = []
        for r in regions:
            seed = np.ones((h, w), np.uint8)
            seed[r.bbox.y1:r.bbox.y2, r.bbox.x1:r.bbox.x2] = 0
            dist.append(cv2.distanceTransform(seed, cv2.DIST_L2, 3))
        owner = np.argmin(np.stack(dist), axis=0)
        for i, r in enumerate(regions):
            mine = ink & (owner == i) & (areas[r.id] > 0)
            if mine.sum() < 30:
                continue
            bubble = None if _inverted(image, mask, r.bbox) else bubble_interior(image, mask, r.bbox)
            room = areas[r.id] > 0 if bubble is None else (bubble & (areas[r.id] > 0))
            if not room.any():
                continue
            box = (r.bbox.x1, r.bbox.y1, r.bbox.x2, r.bbox.y2)
            cx, cy = _bubble_centre(room, box)[:2]
            ys, xs = np.nonzero(mine)
            ix, iy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
            ry, rx = np.nonzero(room)
            bw, bh = rx.max() - rx.min() + 1, ry.max() - ry.min() + 1
            dx, dy = ix - cx, iy - cy
            score = max(abs(dx) / bw, abs(dy) / bh)
            if (abs(dx) > max(args.px, args.tol * bw)) or (abs(dy) > max(args.px, args.tol * bh)):
                found.append((score, r.id, dx, dy, bw, bh, text[r.id][:40]))
    for score, rid, dx, dy, bw, bh, t in sorted(found, reverse=True):
        print(f"{rid} off dx{dx:+.0f} dy{dy:+.0f} (bubble {bw}x{bh}, {score:.0%})  {t!r}")
    print(f"{len(found)} off-centre region(s)")


if __name__ == "__main__":
    main()
