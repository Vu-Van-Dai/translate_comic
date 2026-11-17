"""What an original SFX looks like beyond its letters (the operator, 2026-11-09).

The group's typesetting standard: a replacement SFX reproduces about 70-80% of
the original's look -- its stroke(s), drop shadow, glow, and orientation -- not
just its colour. This module reads those from the page, around the lettering
``sfx.measure`` already found, and splits an SFX drawn in separate pieces
("QU" ... "ẪY") so each piece gets its own text layer.

Everything here is a narrow, deterministic measurement. Each detector answers
"not present" unless the evidence is clear: an effect added wrongly is more
work for the operator than one left out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: RGB distance under which a pixel counts as a given colour.
COLOUR_TOLERANCE = 90
#: A ring around the lettering is a stroke when this share of it is one colour
#: that differs from the background further out.
RING_SHARE = 0.6
#: ... and differs from that background by at least this RGB distance.
RING_CONTRAST = 90
#: Widest second stroke / glow looked for, in pixels.
MAX_OUTER = 14
#: Drop-shadow search: offsets tried, in pixels.
SHADOW_OFFSETS = (3, 5, 7, 9, 12)
#: A shadow must cover this share of the shifted rim on its side ...
SHADOW_SHARE = 0.55
#: ... and at most this share on the opposite side.
SHADOW_OPPOSITE = 0.2
#: An SFX is split into pieces only when it has 2..MAX_PIECES clear clusters.
MAX_PIECES = 4
#: A piece must hold at least this share of the lettering.
MIN_PIECE_SHARE = 0.12


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    import cv2

    if r <= 0:
        return mask.astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(mask.astype(np.uint8), k) > 0


def _near(image: np.ndarray, colour, tol: int = COLOUR_TOLERANCE) -> np.ndarray:
    return np.abs(image.astype(np.int32) - np.array(colour, dtype=np.int32)).sum(axis=-1) < tol


def _median(image: np.ndarray, where: np.ndarray) -> tuple[int, int, int] | None:
    px = image[where]
    if px.size == 0:
        return None
    return tuple(int(v) for v in np.median(px, axis=0))


def _dist(a, b) -> int:
    return int(sum(abs(int(x) - int(y)) for x, y in zip(a, b)))


@dataclass
class Effects:
    """Layer effects to reproduce, in page pixels. Zero/None = absent."""

    stroke2_colour: tuple[int, int, int] | None = None
    stroke2_px: int = 0
    glow_colour: tuple[int, int, int] | None = None
    glow_px: int = 0
    shadow_colour: tuple[int, int, int] | None = None
    shadow_dx: int = 0
    shadow_dy: int = 0
    #: The letters' fill running from one colour to another, top to bottom.
    gradient_top: tuple[int, int, int] | None = None
    gradient_bottom: tuple[int, int, int] | None = None
    #: The fill is a texture or pattern (hatching, several colours): not
    #: reproducible by an effect -- the operator finishes it by hand.
    textured: bool = False


#: Top and bottom thirds of a fill this far apart (RGB distance) are a gradient.
GRADIENT_CONTRAST = 80
#: Spread (mean RGB distance from the band's own median) above which a fill is
#: a texture, not a flat colour or a gradient.
TEXTURE_SPREAD = 70


def fill_style(image: np.ndarray, fill: np.ndarray) -> tuple[tuple | None, tuple | None, bool]:
    """(gradient top, gradient bottom, textured) of the letters' fill pixels."""
    ys = np.nonzero(fill)[0]
    if len(ys) < 60:
        return None, None, False
    lo, hi = np.percentile(ys, [33, 67])
    bands = []
    for sel in (fill & (np.arange(fill.shape[0])[:, None] <= lo),
                fill & (np.arange(fill.shape[0])[:, None] >= hi)):
        px = image[sel].astype(np.int32)
        if len(px) < 20:
            return None, None, False
        med = np.median(px, axis=0)
        bands.append((tuple(int(v) for v in med), float(np.abs(px - med).sum(axis=1).mean())))
    (top, s1), (bottom, s2) = bands
    if max(s1, s2) > TEXTURE_SPREAD:
        return None, None, True
    if _dist(top, bottom) >= GRADIENT_CONTRAST:
        return top, bottom, False
    return None, None, False


def shape_of(image: np.ndarray, zone: np.ndarray, ink, edge, outlined: bool) -> np.ndarray:
    """The lettering as drawn: its ink, plus its first outline when it has one.

    With an outline, the shape is everything the outline encloses. That keeps a
    second, outer stroke *out* of it even when it is the ink's own colour --
    black letters, white outline, black outer stroke is the commonest manga
    style, and taking every black pixel near the letters as "ink" swallowed
    the outer stroke whole.
    """
    from scipy.ndimage import binary_fill_holes

    ink_px = _near(image, ink) & zone
    if not outlined:
        return ink_px
    band = _near(image, edge) & _dilate(ink_px, MAX_OUTER)
    shape = binary_fill_holes(band)
    inside = ink_px & shape
    # A broken outline encloses nothing; an outline the page's own colour
    # encloses half the page. Either way, fall back to ink plus outline.
    if inside.sum() < 0.5 * ink_px.sum() or shape.sum() > 8 * max(1, int(ink_px.sum())):
        return ink_px | (band & zone)
    return shape


def effects(image: np.ndarray, zone: np.ndarray, ink, edge, outlined: bool) -> Effects:
    """Second stroke, glow and drop shadow around one SFX.

    ``zone`` is where the lettering is (``measure``'s ``glyphs``: the letters
    with their halo). The shape tested against is the ink plus its first
    outline; what lies just outside it is compared with the background
    further out.
    """
    out = Effects()
    shape = shape_of(image, zone, ink, edge, outlined)
    if shape.sum() < 30:
        return out
    far = _dilate(shape, MAX_OUTER + 10) & ~_dilate(shape, MAX_OUTER + 4)
    bg = _median(image, far)
    if bg is None:
        return out

    # --- second stroke, or a glow: a ring of one colour hugging the shape
    ring = _dilate(shape, 3) & ~shape
    col = _median(image, ring)
    # The ring must differ from the background and from what it hugs (the
    # outline when there is one -- it may well be the ink's own colour).
    hugged = edge if outlined else ink
    if col is not None and _dist(col, bg) >= RING_CONTRAST and _dist(col, hugged) >= RING_CONTRAST // 2:
        share = float(_near(image, col)[ring].mean())
        if share >= RING_SHARE:
            # How far it reaches, and whether it ends sharply (stroke) or fades (glow).
            width, prev = 0, shape
            profile = []
            for r in range(1, MAX_OUTER + 1):
                band = _dilate(shape, r) & ~prev
                prev = _dilate(shape, r)
                if not band.any():
                    break
                m = _median(image, band)
                profile.append(m)
                if m is None or _dist(m, col) > _dist(m, bg):
                    break
                width = r
            fades = len(profile) >= 3 and _dist(profile[0], col) < 40 \
                and any(40 <= _dist(p, col) <= _dist(col, bg) - 40 for p in profile if p)
            if width >= 2:
                if fades:
                    out.glow_colour, out.glow_px = col, width + 2
                else:
                    out.stroke2_colour, out.stroke2_px = col, width

    # --- drop shadow: the shape repeated, offset, on one side only
    body = _dilate(shape, out.stroke2_px) if out.stroke2_px else shape
    best = None
    for d in SHADOW_OFFSETS:
        for ang in range(0, 360, 45):
            dx = int(round(d * np.cos(np.deg2rad(ang))))
            dy = int(round(d * np.sin(np.deg2rad(ang))))
            moved = np.roll(np.roll(body, dy, axis=0), dx, axis=1) & ~_dilate(body, 1)
            back = np.roll(np.roll(body, -dy, axis=0), -dx, axis=1) & ~_dilate(body, 1)
            if moved.sum() < 20:
                continue
            c = _median(image, moved)
            if c is None or _dist(c, bg) < RING_CONTRAST:
                continue
            here = float(_near(image, c)[moved].mean())
            there = float(_near(image, c)[back].mean()) if back.any() else 0.0
            if here >= SHADOW_SHARE and there <= SHADOW_OPPOSITE:
                score = here - there
                if best is None or score > best[0]:
                    best = (score, dx, dy, c)
    if best is not None:
        _s, out.shadow_dx, out.shadow_dy, out.shadow_colour = best

    # --- the fill itself: a gradient, or a texture no effect reproduces
    fill = shape & ~_dilate(_near(image, edge) & ~_near(image, ink), 1) if outlined else (zone & ~_near(image, bg))
    fill = fill & zone
    out.gradient_top, out.gradient_bottom, out.textured = fill_style(image, fill)
    return out


def pieces(zone: np.ndarray, gap: int) -> list[np.ndarray]:
    """The SFX's separate pieces, when it is drawn as 2..MAX_PIECES of them.

    Pieces are what stays apart after closing gaps of ``gap`` pixels (about a
    letter's spacing), each at least MIN_PIECE_SHARE of the lettering. Anything
    else -- one piece, a word with every letter apart -- returns [].
    """
    import cv2

    joined = _dilate(zone, gap).astype(np.uint8)
    n, labels = cv2.connectedComponents(joined, connectivity=8)
    total = float(zone.sum()) or 1.0
    parts = []
    for i in range(1, n):
        part = (labels == i) & (zone > 0)
        if part.sum() / total >= MIN_PIECE_SHARE:
            parts.append(part)
    return parts if 2 <= len(parts) <= MAX_PIECES else []


#: Pieces this unequal (smallest / largest) are not split into separate layers.
SPLIT_MIN_SHARE = 0.35


def split_text(text: str, weights: list[float]) -> list[str]:
    """Share ``text`` among pieces of the given sizes, never splitting a letter.

    Words go one per piece when the counts match ("BÙM BÙM" over two pieces);
    otherwise the letters are shared by size ("QUẪY" over two equal pieces:
    "QU" + "ẪY"). Every piece gets at least one letter.
    """
    import unicodedata

    words = text.split()
    if len(words) == len(weights):
        return words
    # A big letter beside a small mark (a tail, a dot, a small kana) is one
    # word drawn with a flourish, not two pieces: splitting it set "LÓE" as
    # "LÓ" plus a stray tiny "E" (ch.8 p7).
    if min(weights) < SPLIT_MIN_SHARE * max(weights):
        return []
    letters = [c for c in unicodedata.normalize("NFC", "".join(words))]
    n = len(weights)
    if len(letters) < n:
        return []
    total = sum(weights) or 1.0
    cuts, acc = [], 0.0
    for w in weights[:-1]:
        acc += w
        cuts.append(round(acc / total * len(letters)))
    out, start = [], 0
    for i, cut in enumerate(cuts + [len(letters)]):
        lo = start + 1                                   # at least one letter here
        hi = len(letters) - (n - 1 - i)                  # leave one for each later piece
        cut = max(lo, min(hi, cut)) if i < n - 1 else len(letters)
        out.append("".join(letters[start:cut]))
        start = cut
    return out


def vertical_text(text: str) -> str:
    """One letter per line, as a vertical SFX is set (the operator, 2026-11-09)."""
    import unicodedata

    return "\n".join(c for c in unicodedata.normalize("NFC", text) if not c.isspace())


def slant_of(angle: float) -> float:
    """Horizontal step per line, as a share of the line height, of a column
    whose long axis lies at ``angle`` (degrees, image coordinates): 0 for an
    upright column, positive when it leans down-right ( \\ )."""
    t = np.deg2rad(angle)
    s, c = np.sin(t), np.cos(t)
    if abs(s) < 1e-6:
        return 0.0
    return float(c / s)
