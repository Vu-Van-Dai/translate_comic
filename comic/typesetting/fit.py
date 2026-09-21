"""Fitting a Vietnamese line into the space a bubble actually has.

Two things make this harder than measuring a string:

* **The space is not a rectangle.** Bubbles are round, oval, cloud-shaped or
  jagged, and a block sized to the bounding box overflows at the corners. The
  area handed in here is a mask, and a line is only placed where the mask is
  solid all the way across.
* **Vietnamese must not be broken anywhere.** A word split across lines can
  orphan a diacritic onto its own line, which reads as a different word or as
  no word at all. So wrapping happens at spaces only, and a word that cannot
  fit on a line of its own means the font is too big -- not that the word
  should be cut.

The search is over font size: the largest size whose wrapped block fits. That
is monotone (bigger type never fits where smaller type did not), so a binary
search is exact rather than approximate.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import ImageFont

#: Never render below this; past it the diacritics stop resolving.
MIN_FONT_PX = 8
#: No dialogue needs to be bigger than this on a page of this size.
MAX_FONT_PX = 64
#: Breathing room inside the bubble, as a fraction of the font size.
PADDING_EM = 0.35
#: Line spacing. Vietnamese stacks diacritics, so lines cannot sit tight.
LINE_HEIGHT_EM = 1.22


@dataclass(frozen=True)
class Layout:
    """A concrete placement: which lines, what size, where."""

    lines: tuple[str, ...]
    font_size: int
    #: Top-left of the text block, in page coordinates.
    x: int
    y: int
    width: int
    height: int
    #: False when even MIN_FONT_PX overflowed and this is the least-bad option.
    fits: bool

    @property
    def line_height(self) -> int:
        return max(1, int(round(self.font_size * LINE_HEIGHT_EM)))


@lru_cache(maxsize=256)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=20000)
def _measure_cached(path: str, size: int, text: str) -> tuple[int, int]:
    left, top, right, bottom = load_font(path, size).getbbox(text)
    return int(right - left), int(bottom - top)


def _measure(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    """Width and height of ``text``, memoised.

    The fitter measures the same words repeatedly -- once per wrap attempt,
    once per candidate size, once per placement offset -- and measuring is the
    single most expensive thing it does. Keyed by face and size rather than by
    the font object, which is itself already cached.
    """
    return _measure_cached(font.path, font.size, text)


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str] | None:
    """Break ``text`` into lines no wider than ``width``.

    Returns None when some single word is wider than the line, which is the
    signal to try a smaller font rather than to break the word.
    """
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        if _measure(font, word)[0] > width:
            return None
        candidate = f"{current} {word}".strip()
        if _measure(font, candidate)[0] <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def widest_run(row: np.ndarray) -> tuple[int, int]:
    """The longest solid run in a boolean row, as (start, length).

    A bubble's row can be interrupted -- by its own tail, or by a neighbour's
    area removed from this one -- and text may only sit in an unbroken run.
    """
    if not row.any():
        return 0, 0
    padded = np.concatenate(([0], row.view(np.int8), [0]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[::2], edges[1::2]
    lengths = ends - starts
    best = int(lengths.argmax())
    return int(starts[best]), int(lengths[best])


def _block_fits(
    area: np.ndarray, lines: list[str], font: ImageFont.FreeTypeFont, size: int
) -> tuple[int, int, int, int] | None:
    """Place a wrapped block in the area, or None if it will not go.

    Tries every vertical offset rather than only the centre: a tail or a
    neighbour can make the middle of a bubble the wrong place to start.
    """
    if not lines:
        return None
    rows = np.flatnonzero(area.any(axis=1))
    if rows.size == 0:
        return None

    line_height = max(1, int(round(size * LINE_HEIGHT_EM)))
    block_height = line_height * len(lines)
    padding = int(round(size * PADDING_EM))
    needed = [_measure(font, line)[0] for line in lines]

    top_limit = int(rows[0])
    bottom_limit = int(rows[-1]) + 1
    if block_height > bottom_limit - top_limit:
        return None

    best: tuple[int, int, int, int] | None = None
    best_slack = -1
    for top in range(top_limit, bottom_limit - block_height + 1):
        lefts: list[int] = []
        rights: list[int] = []
        ok = True
        for index in range(len(lines)):
            band_top = top + index * line_height
            band = area[band_top : band_top + line_height]
            if band.shape[0] < line_height:
                ok = False
                break
            solid = band.all(axis=0)
            start, run = widest_run(solid)
            if run - 2 * padding < needed[index]:
                ok = False
                break
            centre = start + run / 2
            left = int(round(centre - needed[index] / 2))
            lefts.append(left)
            rights.append(left + needed[index])
        if not ok:
            continue
        slack = min(
            widest_run(
                area[top + i * line_height : top + (i + 1) * line_height].all(axis=0)
            )[1]
            - needed[i]
            for i in range(len(lines))
        )
        if slack > best_slack:
            best_slack = slack
            best = (min(lefts), top, max(rights) - min(lefts), block_height)
    return best


#: Wrap widths to try, as fractions of the bubble's widest point.
#:
#: Wrapping to the widest point and then placing the block is not enough: in a
#: round bubble only the middle row is that wide, so a line sized to it cannot
#: sit anywhere. Narrowing the wrap trades width for lines, which is exactly
#: the trade a letterer makes by eye, and it is what lets the type stay large.
WRAP_ATTEMPTS = (1.0, 0.88, 0.76, 0.64, 0.52, 0.42)


def _try_size(
    solid: np.ndarray, text: str, font_path: str, size: int
) -> Layout | None:
    """Lay ``text`` out at exactly ``size``, or None if it will not go."""
    font = load_font(font_path, size)
    padding = int(round(size * PADDING_EM))
    widest = max(
        (widest_run(row)[1] for row in solid if row.any()), default=0
    )
    if widest <= 0:
        return None

    for fraction in WRAP_ATTEMPTS:
        width = int(widest * fraction) - 2 * padding
        if width < 1:
            continue
        lines = wrap(text, font, width)
        if not lines:
            continue
        placed = _block_fits(solid, lines, font, size)
        if placed:
            x, y, block_width, height = placed
            return Layout(tuple(lines), size, x, y, block_width, height, True)
    return None


def fit_text(
    text: str,
    area: np.ndarray,
    font_path: Path | str,
    max_size: int = MAX_FONT_PX,
    min_size: int = MIN_FONT_PX,
    forced_lines: list[str] | None = None,
    font_delta: int = 0,
) -> Layout:
    """The largest font size at which ``text`` fits inside ``area``.

    Binary search is exact here because fitting is monotone in size. When
    nothing fits even at ``min_size`` the layout comes back with ``fits=False``
    rather than raising: the page still renders, and `visual_qa` is where a
    human eye decides whether to shorten the line or grow the bubble.
    """
    path = str(font_path)
    solid = area > 0
    if not solid.any() or not text.strip():
        return Layout((), min_size, 0, 0, 0, 0, False)

    if forced_lines:
        return _fit_forced(solid, forced_lines, path, max_size, min_size, font_delta)

    low, high = min_size, max(min_size, max_size)
    found: Layout | None = None
    while low <= high:
        size = (low + high) // 2
        attempt = _try_size(solid, text, path, size)
        if attempt is not None:
            found = attempt
            low = size + 1
        else:
            high = size - 1

    if found is not None:
        return _resize(found, solid, path, font_delta, min_size, max_size)

    # Nothing fitted. Lay it out at the floor so the page still renders and the
    # overflow is visible to the eye that has to judge it.
    font = load_font(path, min_size)
    columns = max(1, int(solid.any(axis=0).sum()))
    lines = wrap(text, font, columns) or [text]
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    height = max(1, int(round(min_size * LINE_HEIGHT_EM))) * len(lines)
    return Layout(
        tuple(lines), min_size, int(cols[0]), int(rows[0]),
        int(cols[-1] - cols[0]) + 1, height, False,
    )


def _resize(
    layout: Layout,
    solid: np.ndarray,
    path: str,
    font_delta: int,
    min_size: int,
    max_size: int,
) -> Layout:
    """Apply a `font_delta` fix on top of the fitted size.

    The delta is honoured even when it overflows: the agent asked for it after
    looking at the page, and a fix that silently does nothing is worse than one
    whose effect is visible and can be reverted.
    """
    if not font_delta:
        return layout
    size = max(min_size, min(max_size, layout.font_size + font_delta))
    if size == layout.font_size:
        return layout
    attempt = _try_size(solid, " ".join(layout.lines), path, size)
    if attempt is not None:
        return attempt
    font = load_font(path, size)
    lines = wrap(" ".join(layout.lines), font, max(1, layout.width)) or list(layout.lines)
    height = max(1, int(round(size * LINE_HEIGHT_EM))) * len(lines)
    return Layout(tuple(lines), size, layout.x, layout.y, layout.width, height, False)


def _fit_forced(
    solid: np.ndarray,
    lines: list[str],
    path: str,
    max_size: int,
    min_size: int,
    font_delta: int,
) -> Layout:
    """Place lines the agent broke by hand, at the largest size they allow."""
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    best: Layout | None = None
    for size in range(max_size, min_size - 1, -1):
        font = load_font(path, size)
        placed = _block_fits(solid, lines, font, size)
        if placed:
            x, y, width, height = placed
            best = Layout(tuple(lines), size, x, y, width, height, True)
            break
    if best is None:
        height = max(1, int(round(min_size * LINE_HEIGHT_EM))) * len(lines)
        best = Layout(
            tuple(lines), min_size, int(cols[0]), int(rows[0]),
            int(cols[-1] - cols[0]) + 1, height, False,
        )
    return _resize(best, solid, path, font_delta, min_size, max_size)
