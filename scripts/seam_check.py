"""Find long-strip slices saved out of order, from the pictures alone.

In a webtoon strip the bottom rows of one slice continue straight into the top
rows of the next. Where two slices were saved swapped (the comix downloader,
2026-10-26: slices numbered in the order they finished loading), the edges do
not meet -- a bubble outline stops dead, a dark panel meets a white gutter.

For every seam this measures how different the last row of slice *i* is from
the first row of slice *i+1*, relative to how much neighbouring rows differ
*inside* those slices (so busy art does not raise false alarms), and reports
seams that break, with the page they fall on and whether swapping the two
slices would make both seams continuous.

    python scripts/seam_check.py "My Insatiable Demon" [--chapters 0-27] [--fix-plan]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.paths import ChapterPaths, SeriesPaths, chapter_sort_key  # noqa: E402

#: A seam breaks when its edge difference is this many times the slices' own
#: row-to-row difference, and at least MIN_JUMP grey levels.
RATIO = 6.0
MIN_JUMP = 18.0
EDGE = 2      # rows averaged at each edge


def _edges(f: Path, width: int | None):
    with Image.open(f) as im:
        im = im.convert("RGB")
        if width and im.width != width:
            im = im.resize((width, max(1, round(im.height * width / im.width))))
        a = np.asarray(im, dtype=np.float32)
    top, bot = a[:EDGE].mean(0), a[-EDGE:].mean(0)
    inner = float(np.abs(np.diff(a[:12], axis=0)).mean() + np.abs(np.diff(a[-12:], axis=0)).mean()) / 2
    return top, bot, inner, a.shape[0]


def seam_cost(bottom, top) -> float:
    return float(np.abs(bottom - top).mean())


def check_chapter(paths: ChapterPaths) -> list[dict]:
    files = sorted(f for f in paths.original.iterdir() if f.is_file() and not f.name.startswith("_"))
    if len(files) < 2:
        return []
    width = Image.open(files[0]).width
    E = [_edges(f, width) for f in files]
    ys, y = [], 0
    for e in E:
        ys.append(y)
        y += e[3]
    page_of = _page_finder(paths)
    out = []
    for i in range(len(files) - 1):
        cost = seam_cost(E[i][1], E[i + 1][0])
        base = max(1.0, (E[i][2] + E[i + 1][2]) / 2)
        if cost >= MIN_JUMP and cost >= RATIO * base:
            # would swapping slices i+1 and i+2 fix it? (the common case)
            swap_ok = None
            if i + 2 < len(files):
                now = cost + seam_cost(E[i + 1][1], E[i + 2][0])
                alt = seam_cost(E[i][1], E[i + 2][0]) + seam_cost(E[i + 2][1], E[i + 1][0])
                swap_ok = alt < now * 0.5
            out.append({"seam": f"{files[i].name}|{files[i + 1].name}", "cost": round(cost, 1),
                        "base": round(base, 1), "page": page_of(ys[i + 1]), "swap_fixes": swap_ok})
    return out


def _page_finder(paths: ChapterPaths):
    pages = sorted(paths.pages.glob("*.png")) if paths.pages.is_dir() else []
    starts, y = [], 0
    for p in pages:
        starts.append((y, int(p.stem)))
        with Image.open(p) as im:
            y += im.height

    def find(strip_y: int):
        page = None
        for s, n in starts:
            if s <= strip_y:
                page = n
        return page
    return find


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    series = sys.argv[1]
    rng = None
    if "--chapters" in sys.argv:
        a, _, b = sys.argv[sys.argv.index("--chapters") + 1].partition("-")
        rng = (float(a), float(b or a))
    total = 0
    for label in sorted(SeriesPaths(series).chapters(), key=chapter_sort_key):
        num = float(label.removeprefix("Chapter_")) if label.removeprefix("Chapter_").replace(".", "").isdigit() else None
        if rng and (num is None or not rng[0] <= num <= rng[1]):
            continue
        paths = ChapterPaths(series, label)
        if not paths.original.is_dir():
            continue
        bad = check_chapter(paths)
        total += len(bad)
        if bad:
            pages = sorted({b["page"] for b in bad if b["page"]})
            print(f"{label}: {len(bad)} broken seam(s), pages {pages}")
            for b in bad:
                print(f"    {b['seam']}  jump {b['cost']} (art {b['base']})  page {b['page']}"
                      f"  {'swap fixes' if b['swap_fixes'] else ''}")
        else:
            print(f"{label}: ok")
    print(f"TOTAL broken seams: {total}")


if __name__ == "__main__":
    main()
