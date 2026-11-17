"""Compose fan-page post images: a grid of panels, each with a bold caption.

The Facebook meme/collage format (2x2, 2x3, 1x2 ...): every cell is one panel
cropped to the cell, with a short Vietnamese line along its bottom edge.
Distinct from the videos, which never carry text on every shot.

    python scripts/fb_post.py SPEC.json

SPEC = {
  "out": "posts/<series>/<name>/01.jpg",
  "cols": 2, "cell": [540, 600],          # cell size in px (output = cols x cell)
  "style": "outline" | "red" | "yellow",  # caption look
  "panels": [
    {"src": "workspace/.../original/012.webp", "crop": [x1, y1, x2, y2],
     "caption": "Ngày nhỏ anh cõng bé", "focus": 0.3}, ...
  ]
}
`crop` is in source pixels (optional: whole page); the crop is then
cover-fitted to the cell, `focus` (0 top .. 1 bottom, default 0.35) choosing
which band survives when the crop is taller than the cell. Each spec may also
hold several images: {"images": [SPEC, SPEC, ...]}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
FACE = "BeVietnamPro-ExtraBold.ttf"
GAP = 6


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / FACE), size)


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Greedy wrap on spaces; never splits a Vietnamese word."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def balance(text: str, f: ImageFont.FreeTypeFont) -> list[str]:
    """Two lines of near-equal width, so no one-word orphan on the second."""
    words = text.split()
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        w = max(f.getlength(a), f.getlength(b))
        if best is None or w < best[0]:
            best = (w, [a, b])
    return best[1] if best else [text]


def fit_caption(text: str, width: int, max_lines: int = 2) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size in range(38, 17, -2):
        f = font(size)
        lines = wrap(text, f, width)
        if len(lines) == 2:
            lines = balance(text, f)
        if len(lines) <= max_lines and all(f.getlength(l) <= width for l in lines):
            return f, lines
    f = font(18)
    return f, wrap(text, f, width)


def cover(im: Image.Image, w: int, h: int, focus: float) -> Image.Image:
    scale = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    x = (im.width - w) // 2
    y = round((im.height - h) * focus)
    return im.crop((x, y, x + w, y + h))


def caption(cell: Image.Image, text: str, style: str) -> None:
    if not text:
        return
    d = ImageDraw.Draw(cell)
    pad = 18
    f, lines = fit_caption(text.upper() if style == "red" else text, cell.width - 2 * pad - (24 if style == "red" else 0))
    lh = round(f.size * 1.22)
    block = lh * len(lines)
    y = cell.height - block - pad - (8 if style == "red" else 0)
    if style == "red":
        bw = max(f.getlength(l) for l in lines) + 28
        x0 = (cell.width - bw) / 2
        d.rounded_rectangle((x0, y - 8, x0 + bw, y + block + 6), radius=10, fill=(214, 30, 38))
    for i, line in enumerate(lines):
        tw = f.getlength(line)
        x = (cell.width - tw) / 2
        if style == "yellow":
            d.text((x, y + i * lh), line, font=f, fill=(255, 226, 60), stroke_width=max(2, f.size // 9), stroke_fill=(20, 20, 20))
        elif style == "red":
            d.text((x, y + i * lh), line, font=f, fill="white")
        else:
            d.text((x, y + i * lh), line, font=f, fill="white", stroke_width=max(2, f.size // 9), stroke_fill=(15, 15, 15))


def compose(spec: dict) -> Path:
    cols = spec.get("cols", 2)
    cw, ch = spec.get("cell", [540, 600])
    panels = spec["panels"]
    rows = -(-len(panels) // cols)
    W, H = cols * cw + (cols - 1) * GAP, rows * ch + (rows - 1) * GAP
    page = Image.new("RGB", (W, H), "white")
    for k, p in enumerate(panels):
        src = Image.open(ROOT / p["src"]).convert("RGB")
        if p.get("crop"):
            src = src.crop(tuple(p["crop"]))
        cell = cover(src, cw, ch, p.get("focus", 0.35))
        caption(cell, p.get("caption", ""), spec.get("style", "outline"))
        r, c = divmod(k, cols)
        page.paste(cell, (c * (cw + GAP), r * (ch + GAP)))
    tag = spec.get("tag", "Rồng Nhỏ")
    if tag:
        d = ImageDraw.Draw(page)
        f = font(22)
        d.text((W - f.getlength(tag) - 14, 10), tag, font=f, fill=(255, 255, 255), stroke_width=2, stroke_fill=(40, 40, 40))
    out = ROOT / spec["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    page.save(out, quality=92)
    return out


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for s in spec.get("images", [spec]):
        print(compose(s))


if __name__ == "__main__":
    main()
