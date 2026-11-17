"""Image kit for a CapCut template: 9:16 shots, numbered in slot order.

    python scripts/template_kit.py PLAN.json

A CapCut template (capcut.com/vi-vn/template, or the editor's "Mẫu" panel)
takes N images and lays its own music, cuts and effects over them; filling it
needs the CapCut app. This writes what the operator selects there:
``videos/<series>/<name>/01.jpg ...`` at 1080x1920, plus ``README.txt`` with
the template link, slot count, order and the post caption.

Plan JSON::

    {"series": "Kimigazer", "name": "Template_MuoiNam",
     "template": {"id": "7691928914477780231", "title": "...", "slots": 5},
     "caption": "...",
     "shots": [{"chapter": 6, "page": 2, "mode": "fill", "cx": 0.5, "cy": 0.5},
               {"chapter": 6, "page": 18, "mode": "fit"}]}

``fill`` crops the page to 9:16 around (cx, cy), page fractions (``zoom`` > 1
crops tighter, e.g. to drop a "to be continued" footer); ``fit``
sets the whole page on white. Every image goes through
``video.source.check_allowed``: never our Vietnamese translation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.paths import ChapterPaths, project_root  # noqa: E402
from comic.video.source import ArtSource, art_file, check_allowed  # noqa: E402

W, H = 1080, 1920


def shot(img: Image.Image, mode: str, cx: float, cy: float, zoom: float = 1.0) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    if mode == "fit":
        s = min(W / w, H / h)
        page = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
        out = Image.new("RGB", (W, H), "white")
        out.paste(page, ((W - page.width) // 2, (H - page.height) // 2))
        return out
    if w / h > W / H:
        cw, ch = round(h * W / H), h
    else:
        cw, ch = w, round(w * H / W)
    cw, ch = round(cw / zoom), round(ch / zoom)
    x = min(max(0, round(cx * w - cw / 2)), w - cw)
    y = min(max(0, round(cy * h - ch / 2)), h - ch)
    return img.crop((x, y, x + cw, y + ch)).resize((W, H), Image.LANCZOS)


def main() -> None:
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    series = plan["series"]
    out = project_root() / "videos" / series / plan["name"]
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, s in enumerate(plan["shots"], 1):
        paths = ChapterPaths(series, str(s["chapter"]))
        chapter = paths.chapter
        src, _ = art_file(paths, int(s["page"]), ArtSource.ORIGINAL)
        check_allowed(src)
        shot(Image.open(src), s.get("mode", "fill"), s.get("cx", 0.5), s.get("cy", 0.5),
             s.get("zoom", 1.0)).save(
            out / f"{i:02d}.jpg", quality=95)
        lines.append(f"{i:02d}.jpg  <- {chapter} p.{int(s['page']):03d}  {s.get('note', '')}".rstrip())
    t = plan["template"]
    slots = t.get("slots")
    warn = "" if slots in (None, len(lines)) else f"\n!! template has {slots} slots, kit has {len(lines)} images\n"
    readme = (
        f"Template: {t['title']}  ({slots} slots)\n"
        f"https://www.capcut.com/vi-vn/template-detail/{t['id']}\n{warn}\n"
        "In CapCut: open the template -> Use template -> select the images below in this order -> export here.\n\n"
        + "\n".join(lines)
        + f"\n\nPost caption:\n{plan.get('caption', '')}\n")
    (out / "README.txt").write_text(readme, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
