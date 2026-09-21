"""Deciding which pixels a cleaning pass is allowed to touch.

Two different shapes are needed and they are not the same thing:

* the **text mask** -- the glyphs themselves, dilated enough to take their
  anti-aliased edges with them. This is what gets erased over artwork, where
  every pixel we do not have to change is a pixel we do not risk damaging.
* the **bubble interior** -- the whole flat area the lettering sat in. This is
  what gets erased inside a speech bubble, and what the typesetter is allowed
  to fill. It matters because Vietnamese runs noticeably longer than the CJK it
  replaces, so a line confined to the source's bounding box would either
  overflow onto uncleaned artwork or be shrunk until it is unreadable.

Nothing here paints; it only decides. The artwork-preservation guarantee in
CLAUDE.md is enforced by compositing through these masks, so a mask that
reaches further than it should is the one way that guarantee can be lost.
"""

from __future__ import annotations

import cv2
import numpy as np

from comic.schemas import BBox, TextRegion

#: How far past a glyph edge to erase. Anti-aliasing leaves a grey halo a
#: pixel or two wide; leaving it behind reads as a smudge where text used to be.
GLYPH_DILATION = 3

#: How far outside a text box to look for the bubble it sits in.
BUBBLE_SEARCH_PAD = 48

#: How far a pixel's luma may sit from the region's own background and still
#: count as part of it. Measured against `vision/regions.py`: real bubbles have
#: a spread of 10-51, so this admits the whole interior including screentone
#: while the outline -- which contrasts hard -- still stops the fill.
INTERIOR_TOLERANCE = 55

#: Leave the bubble's own outline alone: pull the interior back from its edge.
INTERIOR_INSET = 2


def _gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def dilate(mask: np.ndarray, pixels: int = GLYPH_DILATION) -> np.ndarray:
    """Grow a mask by ``pixels`` in every direction."""
    if pixels <= 0:
        return (mask > 0).astype(np.uint8)
    size = pixels * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return (cv2.dilate((mask > 0).astype(np.uint8), kernel) > 0).astype(np.uint8)


def text_mask(
    page_mask: np.ndarray, regions: list[TextRegion], dilation: int = GLYPH_DILATION
) -> np.ndarray:
    """The glyph pixels of every translatable region, dilated.

    SFX is left in the artwork by default, so its pixels never enter the mask
    and the cleaning pass physically cannot touch them.
    """
    out = np.zeros(page_mask.shape[:2], dtype=np.uint8)
    for region in regions:
        if not region.translatable:
            continue
        box = region.bbox
        patch = page_mask[box.y1 : box.y2, box.x1 : box.x2]
        if patch.size:
            out[box.y1 : box.y2, box.x1 : box.x2] |= (patch > 0).astype(np.uint8)
    return dilate(out, dilation)


def region_glyphs(
    page_mask: np.ndarray, region: TextRegion, dilation: int = GLYPH_DILATION
) -> np.ndarray:
    """Just this region's lettering, dilated: the pixels cleaning must erase.

    Shared with the QA check so "did the erase take?" is asked about the same
    pixels the cleaner was asked to change. Measuring residue over the whole
    bubble area instead counts the outline and any artwork beside it, which
    must stay -- measured at 60-73% "residue" on regions that were in fact
    erased completely.
    """
    out = np.zeros(page_mask.shape[:2], dtype=np.uint8)
    box = region.bbox
    out[box.y1 : box.y2, box.x1 : box.x2] = (
        page_mask[box.y1 : box.y2, box.x1 : box.x2] > 0
    ).astype(np.uint8)
    return dilate(out, dilation)


def bubble_interior(
    image: np.ndarray,
    page_mask: np.ndarray,
    bbox: BBox,
    pad: int = BUBBLE_SEARCH_PAD,
) -> np.ndarray | None:
    """The flat area the lettering sits in, or None if there is not one.

    Found by growing outward from the text rather than by looking for a shape:
    take the pixels near the box that match the background the lettering sits
    on, add the glyphs back (they contrast with it, and would otherwise punch
    holes in their own bubble), and keep the connected piece the text actually
    sits in. The bubble's outline contrasts too, so it stops the region
    naturally -- no polygon, no contour fitting, and it works on the irregular
    and cloud-shaped bubbles that a rectangle gets wrong.

    The background is **measured, not assumed light**. Inverted panels -- white
    lettering on black -- are ordinary in manga, and testing for light pixels
    finds nothing there, so the region falls back to its own text box and the
    translation has no room to grow into.

    Returns a full-page mask so callers never juggle window offsets.
    """
    height, width = image.shape[:2]
    top = max(0, bbox.y1 - pad)
    left = max(0, bbox.x1 - pad)
    bottom = min(height, bbox.y2 + pad)
    right = min(width, bbox.x2 + pad)
    if bottom <= top or right <= left:
        return None

    window = _gray(image[top:bottom, left:right]).astype(np.int16)
    glyphs = page_mask[top:bottom, left:right] > 0

    inner = window[bbox.y1 - top : bbox.y2 - top, bbox.x1 - left : bbox.x2 - left]
    inner_glyphs = glyphs[
        bbox.y1 - top : bbox.y2 - top, bbox.x1 - left : bbox.x2 - left
    ]
    around = inner[~inner_glyphs]
    background = float(np.median(around)) if around.size else 255.0

    matches = (
        (np.abs(window - background) <= INTERIOR_TOLERANCE) | glyphs
    ).astype(np.uint8)

    count, labels = cv2.connectedComponents(matches, connectivity=4)
    if count < 2:
        return None

    # Vote with *this region's* lettering only. The window deliberately reaches
    # past the box so a neighbouring bubble does not punch holes in this one,
    # but that also means it contains the neighbour's text -- and counting that
    # hands a small region its larger neighbour's bubble. Measured: one region
    # came back with an interior covering none of its own box at all.
    own = np.zeros_like(glyphs)
    own[
        bbox.y1 - top : bbox.y2 - top, bbox.x1 - left : bbox.x2 - left
    ] = glyphs[bbox.y1 - top : bbox.y2 - top, bbox.x1 - left : bbox.x2 - left]
    inside = labels[own]
    inside = inside[inside > 0]
    if inside.size == 0:
        return None
    label = int(np.bincount(inside).argmax())

    interior = (labels == label).astype(np.uint8)
    if INTERIOR_INSET > 0:
        size = INTERIOR_INSET * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        interior = cv2.erode(interior, kernel)

    # A component that ran out through a gap in the outline and swallowed the
    # panel is not a bubble. Judged against the window, not the page, so a
    # genuinely large bubble on a small page still qualifies.
    if interior.sum() > 0.92 * interior.size:
        return None

    out = np.zeros((height, width), dtype=np.uint8)
    out[top:bottom, left:right] = interior
    return out


def _distance_to_box(shape: tuple[int, int], box: BBox) -> np.ndarray:
    """Euclidean distance from every pixel to the nearest point of ``box``."""
    height, width = shape
    ys = np.arange(height, dtype=np.float32)[:, None]
    xs = np.arange(width, dtype=np.float32)[None, :]
    dx = np.maximum(np.maximum(box.x1 - xs, xs - box.x2), 0.0)
    dy = np.maximum(np.maximum(box.y1 - ys, ys - box.y2), 0.0)
    return np.sqrt(dx * dx + dy * dy)


def fit_areas(
    image: np.ndarray, page_mask: np.ndarray, regions: list[TextRegion]
) -> dict[str, np.ndarray]:
    """The area each region may be cleaned into and typeset within.

    A bubble interior alone is not enough. Two bubbles that touch, or two
    captions sharing a panel's white background, come back as one connected
    component, so both regions claim the same pixels -- measured here, one
    interior covered 100% of its neighbour's own lettering. Typesetting both
    would stack two lines in the same place.

    So contested pixels are awarded to the nearest region's box, which is the
    reading a person makes too: text belongs to the bubble it sits in. A region
    that found no interior falls back to its own box, and every region's own
    lettering is protected from its neighbours.
    """
    shape = image.shape[:2]
    usable = [r for r in regions if r.translatable and r.source_text.strip()]
    if not usable:
        return {}

    interiors: dict[str, np.ndarray] = {}
    for region in usable:
        found = bubble_interior(image, page_mask, region.bbox)
        if found is None:
            found = np.zeros(shape, dtype=np.uint8)
            box = region.bbox
            found[box.y1 : box.y2, box.x1 : box.x2] = 1
        interiors[region.id] = found

    distances = np.stack([_distance_to_box(shape, r.bbox) for r in usable])
    owner = distances.argmin(axis=0)

    areas: dict[str, np.ndarray] = {}
    for index, region in enumerate(usable):
        area = interiors[region.id].copy()
        area[owner != index] = 0

        # Floor: a region keeps at least the space its own lettering occupied.
        # Two regions the detector split out of one bubble would otherwise
        # divide it by distance alone, and the smaller box can come away with
        # less room than its source text had -- measured at 0.65x, which is
        # not enough to set a translation that is longer than the original.
        box = region.bbox
        own = np.zeros(shape, dtype=np.uint8)
        own[box.y1 : box.y2, box.x1 : box.x2] = 1
        own &= interiors[region.id]
        for other in usable:
            if other.id == region.id:
                continue
            spot = other.bbox
            own[spot.y1 : spot.y2, spot.x1 : spot.x2] = 0
        area |= own

        # And its own lettering always belongs to it, whatever is nearest.
        area[box.y1 : box.y2, box.x1 : box.x2] |= (
            page_mask[box.y1 : box.y2, box.x1 : box.x2] > 0
        ).astype(np.uint8)
        areas[region.id] = area

    return areas
