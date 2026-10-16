"""Where a translator's note for a difficult SFX goes.

The operator (2026-10-15, with marked-up pages): a note must not sit beside
the SFX it explains -- there it covers artwork. It goes in empty space: a
gutter between panels, the page margin, a blank corner of a panel. A tall
gutter takes the note turned on its side.

So this finds, near the SFX, a rectangle with nothing in it: no ink (every
pixel light), none of the page's lettering, and nothing already set there.
Deterministic and measured on the page; the agent only says *what* the note
reads.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import ImageFont

#: A pixel lighter than this (0-255 grey) counts as empty paper.
PAPER = 232
#: Clear space kept around the note, in pixels at the page's own scale.
NOTE_PAD = 4
#: Note type size as a share of the page width (13-14 px on an 850 px scan).
NOTE_EM = 0.016
#: Never smaller than this, or the note cannot be read.
NOTE_MIN_PX = 11
#: Ways to break a note into lines before giving up on a spot.
MAX_LINES = 3


@dataclass(frozen=True)
class NoteSpot:
    x1: int
    y1: int
    x2: int
    y2: int
    lines: tuple[str, ...]
    #: 0 for a horizontal note; -90 for one set up a vertical gutter.
    angle: float


#: Clear space demanded around a note set *inside* a panel (a blank corner):
#: white flecks inside the art are everywhere, and a note squeezed between
#: strokes reads as part of the drawing.
INSIDE_PAD = 14
#: How much farther an inside-panel spot may be before a gutter one wins.
INSIDE_COST = 2.5


def outside_panels(image: np.ndarray) -> np.ndarray:
    """Paper reachable from the page edge: the margins and the gutters.

    Panel borders are closed lines, so white inside a panel is cut off from
    the edge and stays out -- which is exactly the art a note must not sit
    on (measured on Kimigazer ch.1 p.11-12: a plain "any blank pixel" rule
    put every note in a white fleck inside the drawing).
    """
    grey = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    paper = (grey >= PAPER).astype(np.uint8)
    count, labels = cv2.connectedComponents(paper, connectivity=4)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    edge = edge[edge > 0]
    return np.isin(labels, edge)


def occupied_mask(image: np.ndarray, extra: np.ndarray | None = None) -> np.ndarray:
    """Everything a note may not cover: ink, plus ``extra`` (lettering, notes)."""
    grey = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    busy = (grey < PAPER).astype(np.uint8)
    if extra is not None:
        busy |= (extra > 0).astype(np.uint8)
    return cv2.dilate(busy, np.ones((2 * NOTE_PAD + 1, 2 * NOTE_PAD + 1), np.uint8))


def placement_masks(image: np.ndarray, extra: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(gutter_busy, inside_busy): where a note may not go, for each kind of spot.

    A gutter spot must lie in the margins/gutters; an inside spot may lie in a
    panel but needs ``INSIDE_PAD`` of clear paper round it.
    """
    busy = occupied_mask(image, extra)
    gutter = busy | (~outside_panels(image)).astype(np.uint8)
    inside = cv2.dilate(busy, np.ones((2 * INSIDE_PAD + 1, 2 * INSIDE_PAD + 1), np.uint8))
    return gutter, inside


def _splits(text: str, n: int) -> list[str]:
    """``text`` broken into ``n`` lines of about equal length, at spaces."""
    words = text.split()
    if n <= 1 or len(words) < n:
        return [text]
    target = len(text) / n
    lines, cur = [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > target and len(lines) < n - 1:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    lines.append(cur)
    return lines


def _free_positions(busy: np.ndarray, w: int, h: int) -> np.ndarray:
    """Top-left corners (y, x) where a w x h box touches nothing busy."""
    H, W = busy.shape
    if w >= W or h >= H:
        return np.empty((0, 2), int)
    ii = cv2.integral((busy > 0).astype(np.uint8))
    sums = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
    return np.argwhere(sums == 0)


def find_spot(
    busy: np.ndarray | tuple[np.ndarray, np.ndarray],
    near: tuple[float, float],
    text: str,
    font_path: str,
    page_width: int,
) -> NoteSpot | None:
    """The empty box nearest ``near`` that holds ``text``, or None.

    Tries the note on one line, then two and three, horizontal and turned up
    a vertical strip; of every shape that fits somewhere, the placement whose
    centre is nearest the SFX wins. Lines are measured with the note's own
    face at its own size, so the box is the note's true footprint.
    """
    size = max(NOTE_MIN_PX, int(round(page_width * NOTE_EM)))
    font = ImageFont.truetype(font_path, size)
    lead = int(round(size * 1.25))
    best: tuple[float, NoteSpot] | None = None
    for n in range(1, MAX_LINES + 1):
        lines = _splits(text, n)
        if len(lines) != n and n > 1:
            continue
        w = max(int(font.getlength(line)) for line in lines) + 2
        h = lead * len(lines)
        kinds = [(busy, 1.0)] if isinstance(busy, np.ndarray) else [(busy[0], 1.0), (busy[1], INSIDE_COST)]
        for mask, cost in kinds:
            for angle, (bw, bh) in ((0.0, (w, h)), (-90.0, (h, w))):
                hits = _free_positions(mask, bw, bh)
                if not len(hits):
                    continue
                cy = hits[:, 0] + bh / 2
                cx = hits[:, 1] + bw / 2
                dist = np.hypot(cx - near[0], cy - near[1]) * cost
                # A turned note costs a little: it reads less easily.
                if angle:
                    dist = dist * 1.3 + 40
                k = int(np.argmin(dist))
                y, x = (int(v) for v in hits[k])
                spot = NoteSpot(x, y, x + bw, y + bh, tuple(lines), angle)
                if best is None or float(dist[k]) < best[0]:
                    best = (float(dist[k]), spot)
    return best[1] if best else None
