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
#: Binds two words so ``wrap`` never breaks between them; drawn as a space.
NBSP = "\u00a0"


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
    #: Leading for this face, in ems; see ``leading_em``.
    line_em: float = LINE_HEIGHT_EM

    @property
    def line_height(self) -> int:
        return max(1, int(round(self.font_size * self.line_em)))


@lru_cache(maxsize=256)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


#: Clear room between the lowest dot below and the next line's highest accent.
LEADING_GAP_EM = 0.06


def line_em(path: str, auto: bool) -> float:
    """The leading a layout uses: per face when ``auto``, else the fixed one."""
    return leading_em(path) if auto else LINE_HEIGHT_EM


@lru_cache(maxsize=64)
def leading_em(path: str) -> float:
    """Line spacing for one face: ``LINE_HEIGHT_EM``, or more if it needs it.

    Piekos (ch. 4): set lines so they "never collide, then add just a bit of
    extra room" -- leading belongs to the typeface. Vietnamese makes that
    measurable: the tallest stacked accent (Ỗ) over the lowest dot below
    (Ộ). Measured: AnimeAce3 spans 1.05 em and keeps 1.22; expressive comic
    faces span 1.28-1.59 em (Marvin 1.45, ImaginaryFriend 1.59, Bangers 1.60)
    and set at 1.22 their accents ran into the line above.

    Opt-in per series (``fonts.yaml: leading: auto``): it would otherwise
    re-leade every published series -- Be Vietnam Pro measures 1.44 em.
    """
    font = load_font(path, 100)
    top = font.getbbox("ỖẤỄỮẪ")[1]
    bottom = font.getbbox("ỘẠỰẬỊ")[3]
    span = (bottom - top) / 100
    return max(LINE_HEIGHT_EM, round(span + LEADING_GAP_EM, 2))


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
    # Split on ordinary spaces only: a no-break space (U+00A0) binds the
    # syllables of one word -- "hôm nay", "bây giờ" -- so a line never breaks
    # inside it. Vietnamese writes every syllable apart, and a break between
    # two halves of a word reads as two words (the editmanga typesetting rules:
    # break lines where the meaning breaks). It is drawn as a plain space.
    words = [w.replace(NBSP, " ") for w in text.replace("\n", " ").split(" ") if w.strip()]
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


def _padded(area: np.ndarray, size: int, pad_em: float | None) -> tuple[np.ndarray, int]:
    """The area a block may occupy at ``size``, and the per-line side padding.

    Default (``pad_em`` None): the whole area, with ``PADDING_EM`` taken off
    each side of every line -- and nothing above or below the block.

    With ``pad_em`` the margin is kept on **every** side, by eroding the area
    by that many ems: "the negative space inside a balloon is typically about
    the size of an uppercase O" (Piekos, *The Essential Guide to Comic Book
    Lettering*, ch. 6). Without it a full bubble set its top and bottom lines
    on the outline. The margin is per size, so a smaller size gives up less
    room, and the binary search over size stays monotone.
    """
    if pad_em is None:
        return area, int(round(size * PADDING_EM))
    px = int(round(size * pad_em))
    if px < 1:
        return area, 0
    import cv2

    kernel = np.ones((2 * px + 1, 2 * px + 1), np.uint8)
    return cv2.erode(area.astype(np.uint8), kernel).astype(bool), 0


#: A bubble measured larger than this along an axis (multiples of the source
#: box, plus pixels) has leaked into the page; centre on the source there.
LEAK_RATIO = 3.0
LEAK_PX = 120
#: Rows (columns) narrower than this share of the bubble's widest are its
#: tail, and do not count toward its centre.
TAIL_SHARE = 0.5


def _run_through(row: np.ndarray, at: int) -> int:
    """Length of the solid run in ``row`` that contains index ``at``."""
    if not row[at]:
        return 0
    left = row[:at][::-1]
    right = row[at:]
    lo = int(np.argmin(left)) if not left.all() else left.size
    hi = int(np.argmin(right)) if not right.all() else right.size
    return lo + hi


def _centre_along(area: np.ndarray, x: int, y: int) -> tuple[float, int] | None:
    """The bubble's vertical centre through column ``x``, and its height.

    Walks the rows above and below ``y`` that hold a run through ``x``, and
    keeps those at least ``TAIL_SHARE`` as wide as the widest -- a tail is
    narrow, and counting it pulls the centre towards it.
    """
    if not area[y, x]:
        return None
    widths = {}
    for step in (-1, 1):
        r = y if step == 1 else y - 1
        while 0 <= r < area.shape[0]:
            w = _run_through(area[r], x)
            if w == 0:
                break
            widths[r] = w
            r += step
    cut = TAIL_SHARE * max(widths.values())
    body = [r for r, w in widths.items() if w >= cut]
    return (min(body) + max(body) + 1) / 2, max(body) + 1 - min(body)


def _bubble_centre(
    area: np.ndarray, anchor: tuple[int, int, int, int]
) -> tuple[float, float, float, float, float, float]:
    """Where an anchored block centres: (cx, cy, x1, y1, x2, y2).

    The centre is the bubble's, not the source lettering's -- a scanlation
    rarely set its English dead centre, and copying that offset put our
    lines visibly off-centre (the operator, 2026-10-15: "avoiding off-center
    text is crucial"). The bubble is measured along a cross through the
    source box; along an axis where it measures far larger than the box,
    the area has leaked through a dashed outline into the page, and the
    source's centre is kept. The block's centre may move anywhere between
    the source box and the bubble's centre.
    """
    x1, y1, x2, y2 = anchor
    solid = area > 0
    h, w = solid.shape
    ax = min(max(int((x1 + x2) / 2), 0), w - 1)
    ay = min(max(int((y1 + y2) / 2), 0), h - 1)
    if not solid[ay, ax]:
        box = solid[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        hits = np.argwhere(box)
        if hits.size:
            dy, dx = hits[np.argmin(np.abs(hits - [ay - max(0, y1), ax - max(0, x1)]).sum(axis=1))]
            ay, ax = int(max(0, y1) + dy), int(max(0, x1) + dx)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    vertical = _centre_along(solid, ax, ay)
    if vertical and vertical[1] <= LEAK_RATIO * (y2 - y1) + LEAK_PX:
        cy = vertical[0]
    horizontal = _centre_along(solid.T, ay, ax)
    if horizontal and horizontal[1] <= LEAK_RATIO * (x2 - x1) + LEAK_PX:
        cx = horizontal[0]
    return cx, cy, min(x1, cx), min(y1, cy), max(x2, cx), max(y2, cy)


def _block_fits(
    area: np.ndarray,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    size: int,
    anchor: tuple[int, int, int, int] | None = None,
    pad_em: float | None = None,
    auto_leading: bool = False,
) -> tuple[int, int, int, int] | None:
    """Place a wrapped block in the area, or None if it will not go.

    Tries every vertical offset rather than only the centre: a tail or a
    neighbour can make the middle of a bubble the wrong place to start.

    With an ``anchor`` -- the source lettering's box, as (x1, y1, x2, y2) --
    the block's centre must fall inside it, the placement nearest its centre
    wins, and each line is centred on it rather than on its row. See
    ``fit_text``.
    """
    if not lines:
        return None
    target = _bubble_centre(area, anchor) if anchor else None
    area, padding = _padded(area, size, pad_em)
    rows = np.flatnonzero(area.any(axis=1))
    if rows.size == 0:
        return None

    line_height = max(1, int(round(size * line_em(font.path, auto_leading))))
    block_height = line_height * len(lines)
    needed = [_measure(font, line)[0] for line in lines]

    top_limit = int(rows[0])
    bottom_limit = int(rows[-1]) + 1
    if block_height > bottom_limit - top_limit:
        return None

    best: tuple[int, int, int, int] | None = None
    best_key: tuple[float, int] = (float("-inf"), -1)
    for top in range(top_limit, bottom_limit - block_height + 1):
        if target and not target[3] <= top + block_height / 2 <= target[5]:
            continue
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
            if anchor:
                low = start + padding + needed[index] / 2
                high = start + run - padding - needed[index] / 2
                centre = min(max(target[0], low), high)
            left = int(round(centre - needed[index] / 2))
            lefts.append(left)
            rights.append(left + needed[index])
        if not ok:
            continue
        if target and not target[2] <= (min(lefts) + max(rights)) / 2 <= target[4]:
            continue
        slack = min(
            widest_run(
                area[top + i * line_height : top + (i + 1) * line_height].all(axis=0)
            )[1]
            - needed[i]
            for i in range(len(lines))
        )
        if anchor:
            # Nearest the bubble's centre; slack only breaks ties.
            off = abs(top + block_height / 2 - target[1])
            if best is None or (-off, slack) > best_key:
                best_key = (-off, slack)
                best = (min(lefts), top, max(rights) - min(lefts), block_height)
        else:
            # Nearest the middle of the room; slack only breaks ties. Taking
            # the slackest placement alone put a block at the *top* of a tall
            # box (the first of equal-slack rows), the off-centre text the
            # operator flagged on 2026-10-15.
            off = abs(top + block_height / 2 - (top_limit + bottom_limit) / 2)
            if best is None or (-off, slack) > best_key:
                best_key = (-off, slack)
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
    solid: np.ndarray,
    text: str,
    font_path: str,
    size: int,
    anchor: tuple[int, int, int, int] | None = None,
    pad_em: float | None = None,
    auto_leading: bool = False,
) -> Layout | None:
    """Lay ``text`` out at exactly ``size``, or None if it will not go."""
    font = load_font(font_path, size)
    measured, padding = _padded(solid, size, pad_em)
    widest = max(
        (widest_run(row)[1] for row in measured if row.any()), default=0
    )
    if widest <= 0:
        return None

    best: Layout | None = None
    best_key: tuple[bool, float, int] | None = None
    tried: set[tuple[str, ...]] = set()
    for fraction in WRAP_ATTEMPTS:
        width = int(widest * fraction) - 2 * padding
        if width < 1:
            continue
        lines = wrap(text, font, width)
        if not lines or tuple(lines) in tried:
            continue
        tried.add(tuple(lines))
        placed = _block_fits(solid, lines, font, size, anchor, pad_em, auto_leading)
        if placed:
            x, y, block_width, height = placed
            layout = Layout(tuple(lines), size, x, y, block_width, height, True,
                            line_em(font_path, auto_leading))
            key = (not _widowed(lines), _evenness(measured, layout, font, padding), -len(lines))
            if best_key is None or key > best_key:
                best, best_key = layout, key
    return best


def _evenness(area: np.ndarray, layout: Layout, font, padding: int) -> float:
    """How evenly the lines fill the rows they sit in: the emptiest line's share.

    The stack should take the shape of its balloon -- an oval in an oval, a
    rectangle in a caption box -- with "no distracting negative spaces"
    (Piekos, ch. 4: one tiny word on top of a wide stack is the amateur's
    tell). Measuring each line against its own row's width says exactly that
    for any shape, so it needs no idea of what shape the bubble is. Only a
    tiebreak between wraps that fit at the same size: never costs type size.
    Rounded so near-equal stacks fall through to the fewer-lines key.
    """
    worst = 1.0
    for index, line in enumerate(layout.lines):
        top = layout.y + index * layout.line_height
        band = area[top : top + layout.line_height]
        if band.shape[0] == 0:
            continue
        room = widest_run(band.all(axis=0))[1] - 2 * padding
        if room > 0:
            worst = min(worst, _measure(font, line)[0] / room)
    return round(worst, 1)


def _widowed(lines: list[str]) -> bool:
    """Whether the last of several lines is a single word.

    A Vietnamese word is one syllable, so a widow here is "nay." alone under a
    full block -- which a letterer never leaves. It stayed hidden while short
    lines were confined to their text box; once the source-size cap left them
    room to spare, the widest wrap that fitted routinely stranded one.

    Only a tiebreak between wraps that fit at the same size: avoiding a widow
    never costs type size.
    """
    return len(lines) >= 2 and len(lines[-1].split()) == 1


#: Closing kernel for the fitting area. See ``_smoothed``.
AREA_CLOSE = 9


def _smoothed(solid: np.ndarray) -> np.ndarray:
    """Close pinholes in the fitting area before measuring it.

    A line may only sit where its whole line-height band is solid all the way
    across, which is what keeps type off a round bubble's corners. That test is
    unforgiving of mask noise: a caption box measured 396px wide on almost
    every row, but three stray rows came back at 200, 206 and 330, and every
    candidate block position straddled one of them. The fitter concluded
    nothing fitted and fell to 15px inside a box that had room for 23px.

    Closing the area first costs nothing where the mask is already clean.
    Measured over 23 regions on six pages: 11 got larger type (18->23, 32->45,
    36->46, 34->43, 25->33), 12 were unchanged, and **none got smaller**.

    Deliberately applied here and not in ``fit_areas``. The same area decides
    what cleaning erases, and widening it there would rub out artwork just
    outside the bubble -- the artwork-preservation guarantee is asserted
    against that mask. Typesetting may look at a tidier shape than cleaning
    acts on; the reverse would be a bug.
    """
    import cv2

    kernel = np.ones((AREA_CLOSE, AREA_CLOSE), np.uint8)
    closed = cv2.morphologyEx(solid.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    return closed.astype(bool)


def _inset(solid: np.ndarray, fraction: float) -> np.ndarray:
    """Pull the fitting area in from its edge on every side.

    ``PADDING_EM`` only narrows each line; nothing kept a block off the
    bubble's top and bottom, so a full bubble set its first and last lines
    against the outline. An operator's reader called it out ("too big, it
    touches the frame"). The margin scales with the bubble -- a fraction of
    its shorter side -- because a letterer leaves more air in a big balloon
    than a small one. Never erodes the area away: a bubble too small to give
    up the margin keeps its full shape.
    """
    if fraction <= 0:
        return solid
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    if rows.size == 0:
        return solid
    side = min(rows[-1] - rows[0] + 1, cols[-1] - cols[0] + 1)
    px = int(round(side * fraction))
    if px < 1:
        return solid
    import cv2

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    eroded = cv2.erode(solid.astype(np.uint8), kernel).astype(bool)
    return eroded if eroded.any() else solid


def fit_text(
    text: str,
    area: np.ndarray,
    font_path: Path | str,
    max_size: int = MAX_FONT_PX,
    min_size: int = MIN_FONT_PX,
    forced_lines: list[str] | None = None,
    font_delta: int = 0,
    anchor: tuple[int, int, int, int] | None = None,
    inset: float = 0.0,
    pad_em: float | None = None,
    auto_leading: bool = False,
) -> Layout:
    """The largest font size at which ``text`` fits inside ``area``.

    Binary search is exact here because fitting is monotone in size. When
    nothing fits even at ``min_size`` the layout comes back with ``fits=False``
    rather than raising: the page still renders, and `visual_qa` is where a
    human eye decides whether to shorten the line or grow the bubble.

    ``anchor`` is the source lettering's box. When there is room to spare --
    which the source-size cap makes the usual case -- the translation goes
    where the original sat, as a letterer would put it. Without that the block
    went wherever the area had most slack, and where the area had leaked out
    of a dashed bubble into the page, that was outside the bubble: measured on
    ch.3, a line set above its own bubble. The anchored layout is kept unless
    it costs more than ``ANCHOR_GIVE`` of the type size; then the free one is.
    """
    path = str(font_path)
    solid = _inset(_smoothed(area > 0), inset)
    if not solid.any() or not text.strip():
        return Layout((), min_size, 0, 0, 0, 0, False)

    if forced_lines:
        return _fit_forced(
            solid, forced_lines, path, max_size, min_size, font_delta, anchor, pad_em,
            auto_leading,
        )

    found = _largest(solid, text, path, min_size, max_size, pad_em=pad_em,
                     auto_leading=auto_leading)
    if anchor and found is not None:
        anchored = _largest(solid, text, path, min_size, max_size, anchor, pad_em,
                            auto_leading)
        if anchored is not None and anchored.font_size >= found.font_size * (1 - ANCHOR_GIVE):
            found = anchored
        else:
            anchor = None

    if found is not None:
        if not font_delta:
            found = _centred(found, solid, text, path, min_size, anchor, pad_em, auto_leading)
        return _resize(found, solid, path, font_delta, min_size, max_size, anchor, pad_em,
                       auto_leading)

    # Nothing fitted. Lay it out at the floor so the page still renders and the
    # overflow is visible to the eye that has to judge it.
    font = load_font(path, min_size)
    columns = max(1, int(solid.any(axis=0).sum()))
    lines = wrap(text, font, columns) or [text.replace(NBSP, " ")]
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    height = max(1, int(round(min_size * line_em(path, auto_leading)))) * len(lines)
    return Layout(
        tuple(lines), min_size, int(cols[0]), int(rows[0]),
        int(cols[-1] - cols[0]) + 1, height, False, line_em(path, auto_leading),
    )


#: How much type size the anchored layout may give up before the free one
#: wins instead. See ``fit_text``.
ANCHOR_GIVE = 0.15

#: How much type size a block may give up to sit on its bubble's centre, and
#: how far off (share of the bubble's height or width, or pixels) still
#: counts as centred. See ``_centred``.
CENTRE_GIVE = 0.12
CENTRE_TOL = 0.05
CENTRE_TOL_PX = 4


def _off_centre(layout: Layout, target: tuple[float, float], span: tuple[int, int]) -> float:
    """How far a block's centre sits from ``target``, as a share of ``span``."""
    dx = abs(layout.x + layout.width / 2 - target[0])
    dy = abs(layout.y + layout.height / 2 - target[1])
    return max(max(0.0, dx - CENTRE_TOL_PX) / span[0], max(0.0, dy - CENTRE_TOL_PX) / span[1])


def _centred(
    layout: Layout,
    solid: np.ndarray,
    text: str,
    path: str,
    min_size: int,
    anchor: tuple[int, int, int, int] | None,
    pad_em: float | None,
    auto_leading: bool,
) -> Layout:
    """The same text a little smaller, if that is what centres it.

    The largest size that fits often fits only off-centre: in an egg-shaped
    balloon, wider at the top, the biggest block can sit only high up. The
    operator ranks centring above size ("avoiding off-center text is
    crucial", 2026-10-15), so up to ``CENTRE_GIVE`` of the size is traded for
    a placement on the bubble's centre -- the smallest step down that gets
    there, else the closest of the sizes tried when it is clearly closer.
    """
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    if anchor:
        cx, cy = _bubble_centre(solid, anchor)[:2]
    else:
        cx, cy = (cols[0] + cols[-1] + 1) / 2, (rows[0] + rows[-1] + 1) / 2
    span = (max(1, int(cols[-1] - cols[0] + 1)), max(1, int(rows[-1] - rows[0] + 1)))
    off = _off_centre(layout, (cx, cy), span)
    if off <= CENTRE_TOL:
        return layout
    best, best_off = layout, off
    floor = max(min_size, int(np.ceil(layout.font_size * (1 - CENTRE_GIVE))))
    for size in range(layout.font_size - 1, floor - 1, -1):
        attempt = _try_size(solid, text, path, size, anchor, pad_em, auto_leading)
        if attempt is None:
            continue
        attempt_off = _off_centre(attempt, (cx, cy), span)
        if attempt_off <= CENTRE_TOL:
            return attempt
        if attempt_off < best_off:
            best, best_off = attempt, attempt_off
    return best if best_off < off * 0.6 else layout


def _largest(
    solid: np.ndarray,
    text: str,
    path: str,
    min_size: int,
    max_size: int,
    anchor: tuple[int, int, int, int] | None = None,
    pad_em: float | None = None,
    auto_leading: bool = False,
) -> Layout | None:
    """Binary search for the largest size that fits, or None."""
    low, high = min_size, max(min_size, max_size)
    found: Layout | None = None
    while low <= high:
        size = (low + high) // 2
        attempt = _try_size(solid, text, path, size, anchor, pad_em, auto_leading)
        if attempt is not None:
            found = attempt
            low = size + 1
        else:
            high = size - 1
    return found


def _resize(
    layout: Layout,
    solid: np.ndarray,
    path: str,
    font_delta: int,
    min_size: int,
    max_size: int,
    anchor: tuple[int, int, int, int] | None = None,
    pad_em: float | None = None,
    auto_leading: bool = False,
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
    text = " ".join(layout.lines)
    attempt = (_try_size(solid, text, path, size, anchor, pad_em, auto_leading)
               or _try_size(solid, text, path, size, pad_em=pad_em,
                             auto_leading=auto_leading))
    if attempt is not None:
        return attempt
    font = load_font(path, size)
    lines = wrap(" ".join(layout.lines), font, max(1, layout.width)) or list(layout.lines)
    height = max(1, int(round(size * line_em(path, auto_leading)))) * len(lines)
    return Layout(tuple(lines), size, layout.x, layout.y, layout.width, height, False,
                  line_em(path, auto_leading))


def _fit_forced(
    solid: np.ndarray,
    lines: list[str],
    path: str,
    max_size: int,
    min_size: int,
    font_delta: int,
    anchor: tuple[int, int, int, int] | None = None,
    pad_em: float | None = None,
    auto_leading: bool = False,
) -> Layout:
    """Place lines the agent broke by hand, at the largest size they allow.

    Anchored like ``fit_text``: the agent chose the breaks, not the position,
    so the block still goes where the source sat unless that costs size.
    """
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))

    def largest(where: tuple[int, int, int, int] | None) -> Layout | None:
        for size in range(max_size, min_size - 1, -1):
            font = load_font(path, size)
            placed = _block_fits(solid, lines, font, size, where, pad_em, auto_leading)
            if placed:
                x, y, width, height = placed
                return Layout(tuple(lines), size, x, y, width, height, True, line_em(path, auto_leading))
        return None

    best = largest(None)
    if anchor and best is not None:
        anchored = largest(anchor)
        if anchored is not None and anchored.font_size >= best.font_size * (1 - ANCHOR_GIVE):
            best = anchored
    if best is None:
        height = max(1, int(round(min_size * line_em(path, auto_leading)))) * len(lines)
        best = Layout(
            tuple(lines), min_size, int(cols[0]), int(rows[0]),
            int(cols[-1] - cols[0]) + 1, height, False, line_em(path, auto_leading),
        )
    if not font_delta:
        return best
    # Keep the agent's breaks: re-place the same lines at the new size rather
    # than handing them to `_resize`, which re-wraps.
    size = max(min_size, min(max_size, best.font_size + font_delta))
    font = load_font(path, size)
    placed = (_block_fits(solid, lines, font, size, anchor, pad_em, auto_leading)
              or _block_fits(solid, lines, font, size, pad_em=pad_em, auto_leading=auto_leading))
    if placed:
        x, y, width, height = placed
        return Layout(tuple(lines), size, x, y, width, height, True, line_em(path, auto_leading))
    height = max(1, int(round(size * line_em(path, auto_leading)))) * len(lines)
    return Layout(tuple(lines), size, best.x, best.y, best.width, height, False,
                  line_em(path, auto_leading))
