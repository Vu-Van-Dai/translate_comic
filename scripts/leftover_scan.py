"""List source-language lettering still visible on finished pages.

Reads every page in a folder in overlapping tiles at 2x (small asides and
"SFX: ..." labels are ~8px tall and the whole-page detector misses them), and
prints each line that does not look Vietnamese. Vietnamese has no f, j, w or z
and almost never English digraphs, so those are what it flags; the output is
a review list for the eye, not a verdict.

    python scripts/leftover_scan.py <folder> [--glob "*.png"]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.cpu import throttle, total_threads  # noqa: E402

TILE, STEP, SCALE = 320, 240, 2
ENGLISH = re.compile(
    r"[fjwz]|sfx|ing\b|ed\b|ck|sh|ee|oo|ea|ou|ow|ll|ss|tt|rr|pp|\b(the|you|and|of|to|it|is|my|me|no|oh|yeah|huh|what|good|grief|mine)\b",
    re.I,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--glob", default="[0-9][0-9][0-9].png")
    args = ap.parse_args()
    throttle()
    from rapidocr_onnxruntime import RapidOCR

    eng = RapidOCR(intra_op_num_threads=total_threads(), inter_op_num_threads=1)
    for f in sorted(Path(args.folder).glob(args.glob)):
        img = np.array(Image.open(f).convert("RGB"))[:, :, ::-1]
        h, w = img.shape[:2]
        seen: set = set()
        for y in range(0, max(1, h - TILE + STEP), STEP):
            for x in range(0, max(1, w - TILE + STEP), STEP):
                tile = cv2.resize(img[y:y + TILE, x:x + TILE], None, fx=SCALE, fy=SCALE,
                                  interpolation=cv2.INTER_CUBIC)
                out, _ = eng(tile)
                for box, txt, conf in out or []:
                    if conf < 0.7 or len(re.sub(r"\W", "", txt)) < 2:
                        continue
                    if not ENGLISH.search(txt) or re.search(r"[À-ỹđĐ]", txt):
                        continue
                    xs = [x + int(p[0] / SCALE) for p in box]
                    ys = [y + int(p[1] / SCALE) for p in box]
                    key = (min(xs) // 25, min(ys) // 25)
                    if key in seen:
                        continue
                    seen.add(key)
                    print(f"{f.stem} {min(xs)},{min(ys)},{max(xs)},{max(ys)} {conf:.2f} {txt}", flush=True)


if __name__ == "__main__":
    main()
