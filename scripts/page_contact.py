"""Thumbnail contact sheets of a chapter's original pages, for picking video shots.

    python scripts/page_contact.py --series "Super Lovers" -c 3 -c 5 --out DIR

20 pages to a sheet, each labelled with chapter and page. Reads the original
pages (allowed in videos), or ``--source library`` for a series whose
video.yaml allows its translated pages; writes nothing into the workspace.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from comic.paths import ChapterPaths, project_root


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("-c", "--chapter", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per", type=int, default=20)
    ap.add_argument("--source", choices=["original", "library"], default="original")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(project_root() / "assets/fonts/BeVietnamPro-Bold.ttf"), 22)
    tw, th = 300, 440
    for ch in args.chapter:
        paths = ChapterPaths(args.series, ch)
        folder = paths.library if args.source == "library" else paths.original
        pages = sorted(f for f in folder.iterdir() if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
        for k in range(0, len(pages), args.per):
            group = pages[k:k + args.per]
            cols = 5
            rows = (len(group) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * (tw + 6), rows * (th + 30)), (50, 50, 50))
            d = ImageDraw.Draw(sheet)
            for i, f in enumerate(group):
                im = Image.open(f).convert("RGB")
                im.thumbnail((tw, th))
                x, y = (i % cols) * (tw + 6), (i // cols) * (th + 30)
                sheet.paste(im, (x, y + 28))
                d.text((x + 4, y + 2), f"ch{paths.chapter.split('_')[-1]} p{f.stem}", fill=(255, 220, 0), font=font)
            sheet.save(out / f"{paths.chapter}_{k // args.per + 1}.jpg", quality=85)
            print(out / f"{paths.chapter}_{k // args.per + 1}.jpg")


if __name__ == "__main__":
    main()
