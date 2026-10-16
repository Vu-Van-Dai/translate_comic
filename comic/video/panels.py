"""Cut manga pages into panels, and draw the sheets the agent picks from.

A paged manga separates its panels with white gutters that run to the page
edge. So: everything bright and connected to the border is gutter; what is
left falls apart into panels. Art that bleeds to the edge only joins its
neighbours where they touch, and a page where nothing separates becomes one
panel -- the whole page, which is always offered as ``k0`` anyway.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from comic.paths import ChapterPaths, project_root
from comic.video.source import ArtSource, art_file

#: Brighter than this (0-255) and connected to the border counts as gutter.
GUTTER_LEVEL = 225
#: A panel smaller than this share of the page is a speck, not a panel.
MIN_PANEL_SHARE = 0.025
#: Boxes overlapping more than this (of the smaller) merge into one panel.
MERGE_OVERLAP = 0.6


@dataclass
class Panel:
    id: str
    page: int
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2, self.y2

    @property
    def area(self) -> int:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


def detect_boxes(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Panel boxes on one page, in no particular order."""
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bright = (gray >= GUTTER_LEVEL).astype(np.uint8)
    # Gutter = bright pixels flood-filled from the border.
    padded = np.pad(bright, 1, constant_values=1)
    count, labels = cv2.connectedComponents(padded, connectivity=4)
    border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
    gutter = np.isin(labels, [l for l in border if l != 0])[1:-1, 1:-1]
    art = (~gutter).astype(np.uint8)
    art = cv2.morphologyEx(art, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(art, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw * bh >= MIN_PANEL_SHARE * w * h and bw > 40 and bh > 40:
            boxes.append((int(x), int(y), int(x + bw), int(y + bh)))
    return _merge(boxes)


def _merge(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
                iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
                small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if ix * iy > MERGE_OVERLAP * small:
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return boxes


def reading_order(boxes: list[tuple[int, int, int, int]], rtl: bool = True) -> list[tuple[int, int, int, int]]:
    """Rows top to bottom; within a row right to left (manga) or left to right."""
    rows: list[list[tuple[int, int, int, int]]] = []
    for b in sorted(boxes, key=lambda b: b[1]):
        for row in rows:
            top, bottom = min(r[1] for r in row), max(r[3] for r in row)
            overlap = min(bottom, b[3]) - max(top, b[1])
            if overlap > 0.5 * min(b[3] - b[1], bottom - top):
                row.append(b)
                break
        else:
            rows.append([b])
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda b: -b[2] if rtl else b[0]))
    return out


def page_panels(rgb: np.ndarray, page: int, rtl: bool = True) -> list[Panel]:
    h, w = rgb.shape[:2]
    panels = [Panel(f"p{page:03d}_k0", page, 0, 0, w, h)]
    boxes = reading_order(detect_boxes(rgb), rtl)
    if len(boxes) == 1 and (boxes[0][2] - boxes[0][0]) * (boxes[0][3] - boxes[0][1]) > 0.85 * w * h:
        boxes = []
    for i, b in enumerate(boxes, 1):
        panels.append(Panel(f"p{page:03d}_k{i}", page, *b))
    return panels


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def chapter_panels(paths: ChapterPaths, pages: list[int], prefer: ArtSource, rtl: bool = True) -> dict:
    """Detect panels on every page; returns the ``panels.json`` payload."""
    out = {"prefer": prefer.value, "pages": {}}
    for page in pages:
        try:
            src, kind = art_file(paths, page, prefer)
        except FileNotFoundError:
            continue
        rgb = load_rgb(src)
        out["pages"][str(page)] = {
            "source": str(src),
            "kind": kind.value,
            "size": [int(rgb.shape[1]), int(rgb.shape[0])],
            "panels": [asdict(p) for p in page_panels(rgb, page, rtl)],
        }
    return out


def _font(size: int) -> ImageFont.FreeTypeFont:
    f = project_root() / "assets" / "fonts" / "BeVietnamPro-Bold.ttf"
    return ImageFont.truetype(str(f), size) if f.exists() else ImageFont.load_default()


def draw_sheets(payload: dict, out_dir: Path, per_sheet: int = 6, page_h: int = 760) -> list[Path]:
    """Pages with their numbered panel boxes, ``per_sheet`` to an image."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tiles = []
    for page, info in payload["pages"].items():
        im = Image.open(info["source"]).convert("RGB")
        scale = page_h / im.height
        im = im.resize((round(im.width * scale), page_h), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        # A light grid every 100 page pixels, so a shot can name a crop on a
        # page whose panels did not separate (slanted or bleeding panels).
        for v in range(100, max(info["size"]), 100):
            gx, gy = round(v * scale), round(v * scale)
            tick = (255, 0, 255) if v % 500 == 0 else (255, 150, 255)
            if v < info["size"][0]:
                d.line([(gx, 0), (gx, im.height)], fill=tick, width=1)
                d.text((gx + 2, im.height - 16), str(v), fill=tick, font=_font(12))
            if v < info["size"][1]:
                d.line([(0, gy), (im.width, gy)], fill=tick, width=1)
                d.text((2, gy + 1), str(v), fill=tick, font=_font(12))
        colours =[(230, 30, 30), (20, 120, 230), (20, 170, 60), (220, 120, 0), (160, 40, 200), (0, 170, 170)]
        for j, p in enumerate(info["panels"][1:]):
            c = colours[j % len(colours)]
            box = [round(v * scale) for v in (p["x1"], p["y1"], p["x2"], p["y2"])]
            d.rectangle(box, outline=c, width=3)
            label = p["id"].split("_")[1]
            d.rectangle([box[0] + 3, box[1] + 3, box[0] + 44, box[1] + 30], fill=c)
            d.text((box[0] + 7, box[1] + 4), label, fill="white", font=_font(20))
        head = Image.new("RGB", (im.width, 34), "black")
        ImageDraw.Draw(head).text((6, 4), f"p{int(page):03d}  ({info['kind']})", fill="white", font=_font(22))
        tile = Image.new("RGB", (im.width, im.height + 34), "black")
        tile.paste(head, (0, 0))
        tile.paste(im, (0, 34))
        tiles.append(tile)
    sheets = []
    for k in range(0, len(tiles), per_sheet):
        group = tiles[k:k + per_sheet]
        cols = 3
        rows = (len(group) + cols - 1) // cols
        tw = max(t.width for t in group)
        th = max(t.height for t in group)
        sheet = Image.new("RGB", (cols * (tw + 8), rows * (th + 8)), (60, 60, 60))
        for i, t in enumerate(group):
            sheet.paste(t, ((i % cols) * (tw + 8), (i // cols) * (th + 8)))
        path = out_dir / f"panels_{k // per_sheet + 1:02d}.png"
        sheet.save(path)
        sheets.append(path)
    return sheets


def save_panels(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def find_panel(payload: dict, panel_id: str) -> tuple[dict, dict]:
    page = str(int(panel_id[1:4]))
    info = payload["pages"].get(page)
    if info is None:
        raise KeyError(f"{panel_id}: page {page} has no panels")
    for p in info["panels"]:
        if p["id"] == panel_id:
            return info, p
    raise KeyError(f"{panel_id}: no such panel")
