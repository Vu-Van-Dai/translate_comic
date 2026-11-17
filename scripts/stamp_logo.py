"""Cover another group's watermark with our logo, on a Photoshop-route page.

For a watermark that cannot be erased without destroying the art under it
(the translucent rainbow + eggplant over Kimigazer's linework: LaMa wiped
the hands and bags beneath it), the operator's fallback is to cover it with
the Rồng Nhỏ logo (2026-10-22). The stamp is a white rounded box exactly
covering the watermark with the logo fitted inside, composited onto
``photoshop/NNN.png`` -- the file ``comic ps publish`` reads. A rebuild of
the page overwrites that file, so run this again after ``comic ps build``.

    python scripts/stamp_logo.py --series NAME -c N      # reads photoshop/logo_stamps.json

logo_stamps.json: [{"page": 12, "bbox": [x1, y1, x2, y2]}, ...]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "rongnho_logo_web.png"
PAD = 6


def stamp(page: Image.Image, box: list[int], logo: Image.Image) -> Image.Image:
    x1, y1, x2, y2 = box
    out = page.convert("RGBA")
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((x1, y1, x2, y2), radius=10, fill=(255, 255, 255, 255))
    w, h = x2 - x1 - 2 * PAD, y2 - y1 - 2 * PAD
    scale = min(w / logo.width, h / logo.height)
    lg = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.LANCZOS)
    layer.alpha_composite(lg, (x1 + (x2 - x1 - lg.width) // 2, y1 + (y2 - y1 - lg.height) // 2))
    return Image.alpha_composite(out, layer).convert("RGB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", type=int, required=True)
    a = ap.parse_args()
    d = ROOT / "workspace" / a.series / f"Chapter_{a.chapter:03d}" / "photoshop"
    logo = Image.open(LOGO).convert("RGBA")
    for s in json.loads((d / "logo_stamps.json").read_text(encoding="utf-8")):
        f = d / f"{s['page']:03d}.png"
        stamp(Image.open(f), s["bbox"], logo).save(f)
        print("stamped", f.name, s["bbox"])


if __name__ == "__main__":
    main()
