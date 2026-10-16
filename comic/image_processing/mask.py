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

#: Wider windows to retry with when the bubble is bigger than the first one.
#: A short line in a big bubble -- "I LIKE YOU." in an oval four times its
#: size -- sits so deep inside that the bubble fills the whole 48px window, and
#: the leak guard below cannot tell that from a fill that escaped into the
#: panel. Measured on ch.45: every such region fell back to its own text box,
#: and the translation was set at roughly half the source's type size.
WIDER_SEARCH_PADS = (96, 160, 240)

#: How far a pixel's luma may sit from the region's own background and still
#: count as part of it. Measured against `vision/regions.py`: real bubbles have
#: a spread of 10-51, so this admits the whole interior including screentone
#: while the outline -- which contrasts hard -- still stops the fill.
INTERIOR_TOLERANCE = 55

#: Leave the bubble's own outline alone: pull the interior back from its edge.
INTERIOR_INSET = 2

#: Largest enclosed dark speck taken into a bubble's interior, in pixels. The
#: source's unmasked punctuation is 10-40px here; a drawing in a bubble -- a
#: heart, a sweat drop -- is hundreds, and stays.
SPECK_MAX = 60

#: Where a speck may sit and still be filled: on the region's own rows (give
#: or take ``SPECK_ROWS``) and within ``SPECK_REACH`` of the box's ends.
#: Measured: ch.4's stray dots sit 0px off the rows and up to 17px past the
#: box; ch.40's separate "..." sits 23px above it, on a line of its own.
SPECK_REACH = 24
SPECK_ROWS = 6


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

    A bubble bigger than the search window fills it edge to edge, which the
    leak guard rejects. So when that happens the search is repeated in wider
    windows, and there a result is accepted only if it is **enclosed** -- clear
    of the window's border everywhere except at the page edge. A real bubble
    closes off inside a big enough window; a fill that escaped into the panel
    keeps running into the border however wide the window gets, and still falls
    back to the text box.
    """
    first = _interior_in_window(image, page_mask, bbox, pad)
    if first is None:
        return None
    interior, fills_window, _enclosed = first
    if not fills_window:
        return interior
    for wider in WIDER_SEARCH_PADS:
        if wider <= pad:
            continue
        found = _interior_in_window(image, page_mask, bbox, wider)
        if found is None:
            return None
        interior, fills_window, enclosed = found
        if enclosed and not fills_window:
            return interior
    return None


def _interior_in_window(
    image: np.ndarray, page_mask: np.ndarray, bbox: BBox, pad: int
) -> tuple[np.ndarray, bool, bool] | None:
    """One search for the interior, in a window ``pad`` pixels past the box.

    Returns the full-page interior mask, whether it fills the window (the leak
    guard), and whether it is enclosed -- clear of every window edge that is not
    also a page edge.
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

    light = np.abs(window - background) <= INTERIOR_TOLERANCE

    # Vote with *this region's* lettering only. The window deliberately reaches
    # past the box so a neighbouring bubble does not punch holes in this one,
    # but that also means it contains the neighbour's text -- and counting that
    # hands a small region its larger neighbour's bubble. Measured: one region
    # came back with an interior covering none of its own box at all.
    own = np.zeros_like(glyphs)
    own[
        bbox.y1 - top : bbox.y2 - top, bbox.x1 - left : bbox.x2 - left
    ] = glyphs[bbox.y1 - top : bbox.y2 - top, bbox.x1 - left : bbox.x2 - left]

    interior = _own_component(light | glyphs, own)
    if interior is None:
        return None
    interior = _fill_specks(
        interior,
        (bbox.x1 - left, bbox.y1 - top, bbox.x2 - left, bbox.y2 - top),
    )

    if INTERIOR_INSET > 0:
        size = INTERIOR_INSET * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        interior = cv2.erode(interior, kernel)

    # A component that ran out through a gap in the outline and swallowed the
    # panel is not a bubble. Judged against the window, not the page, so a
    # genuinely large bubble on a small page still qualifies.
    fills_window = bool(interior.sum() > 0.92 * interior.size)

    edges = _window_edges(interior, top, left, bottom, right, height, width)
    enclosed = not any(edge.any() for edge in edges)

    out = np.zeros((height, width), dtype=np.uint8)
    out[top:bottom, left:right] = interior
    return out, fills_window, enclosed


def _window_edges(
    interior: np.ndarray, top: int, left: int, bottom: int, right: int,
    height: int, width: int,
) -> list[np.ndarray]:
    """The interior's rows and columns on window edges that are not page edges."""
    edges = []
    if top > 0:
        edges.append(interior[0, :])
    if bottom < height:
        edges.append(interior[-1, :])
    if left > 0:
        edges.append(interior[:, 0])
    if right < width:
        edges.append(interior[:, -1])
    return edges


def _fill_specks(
    interior: np.ndarray, box: tuple[int, int, int, int]
) -> np.ndarray:
    """Close the interior over small dark specks it encloses.

    Anything dark that the detector did not mark as lettering is a hole in the
    interior, and the flat fill paints around it. That is right for a drawing
    inside a bubble, and wrong for the source's own stray punctuation: measured
    on ch.4, "WELL...." left its raised dots and final period behind, under
    and beside the Vietnamese "Thì...". So holes up to ``SPECK_MAX`` pixels are
    taken into the interior; anything larger is artwork and stays a hole.

    Only specks on this region's own lines, though.
    A bubble can hold lettering the detector never made a region of -- ch.40
    has a lone "..." in a thought bubble's second lobe -- and punctuation is
    the same size as a stray dot. Filling every small hole erased that line.
    """
    from scipy.ndimage import binary_fill_holes

    holes = binary_fill_holes(interior > 0) & (interior == 0)
    if not holes.any():
        return interior
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        holes.astype(np.uint8), connectivity=8
    )
    x1, y1, x2, y2 = box
    out = interior.copy()
    for index in range(1, count):
        left, top, width, height, area = stats[index]
        # On the region's own lines, and trailing off their ends.
        near = (
            left + width >= x1 - SPECK_REACH and left <= x2 + SPECK_REACH
            and top + height >= y1 - SPECK_ROWS and top <= y2 + SPECK_ROWS
        )
        if area <= SPECK_MAX and near:
            out[labels == index] = 1
    return out


def _own_component(matches: np.ndarray, own: np.ndarray) -> np.ndarray | None:
    """The connected piece of ``matches`` most of ``own``'s pixels fall in."""
    count, labels = cv2.connectedComponents(matches.astype(np.uint8), connectivity=4)
    if count < 2:
        return None
    inside = labels[own]
    inside = inside[inside > 0]
    if inside.size == 0:
        return None
    label = int(np.bincount(inside).argmax())
    return (labels == label).astype(np.uint8)


def _distance_to_box(shape: tuple[int, int], box: BBox) -> np.ndarray:
    """Euclidean distance from every pixel to the nearest point of ``box``."""
    height, width = shape
    ys = np.arange(height, dtype=np.float32)[:, None]
    xs = np.arange(width, dtype=np.float32)[None, :]
    dx = np.maximum(np.maximum(box.x1 - xs, xs - box.x2), 0.0)
    dy = np.maximum(np.maximum(box.y1 - ys, ys - box.y2), 0.0)
    return np.sqrt(dx * dx + dy * dy)


#: Below this luminance the field behind the lettering is dark.
INVERTED_FIELD_LUMA = 110


def _inverted(image: np.ndarray, page_mask: np.ndarray, box: BBox) -> bool:
    """Light lettering on a dark field: a black caption box, a dark balloon.

    ``bubble_interior`` grows from light pixels, and here the only light
    pixels are the letters themselves, so the "interior" it returns is the
    shape of the old words -- measured on Super Lovers ch.0, a 223x213 black
    caption gave a fragmented area that set its translation at 14px. The box
    is the honest space for such lettering.
    """
    crop = image[box.y1 : box.y2, box.x1 : box.x2]
    glyphs = page_mask[box.y1 : box.y2, box.x1 : box.x2] > 0
    if crop.size == 0 or not glyphs.any() or glyphs.all():
        return False
    grey = crop if crop.ndim == 2 else crop[:, :, :3].mean(axis=2)
    # The letters' bright part: a white face with a heavy black outline (ch.0
    # "No way.") has a dark median over its mask.
    field, letters = float(np.median(grey[~glyphs])), float(np.percentile(grey[glyphs], 80))
    return field < INVERTED_FIELD_LUMA and letters > field + 40


#: Blur radius that turns dot screentone into its average grey.
TONE_BLUR = 3.0
#: Largest step between neighbouring blurred pixels inside one box. Measured
#: on Super Lovers ch.0: gradient caption boxes, a dotted balloon and a
#: white caption pair all fill to their edges at 1.5.
TONE_STEP = 1.5
#: Search windows, in multiples of the text box's longer side.
TONE_WINDOWS = (1.5, 3.0, 5.0)


def toned_interior(image: np.ndarray, page_mask: np.ndarray, box: BBox) -> np.ndarray | None:
    """The tinted or screentoned box the lettering sits in, or None.

    ``bubble_interior`` grows from light pixels, so a caption box laid in
    grey dot tone gives it nothing: measured on Super Lovers ch.0 p.13, a
    roughly 300x300 dotted caption came back as a 199x104 sliver and the
    translation was set at 13px against the source's 30. Blurred, dot tone
    is a smooth grey; the box is everything a flood fill reaches from the
    lettering in small steps, which ends at the box's drawn edge. A piece
    that runs off the search window is open artwork, not a box: None.
    """
    height, width = image.shape[:2]
    bw, bh = box.x2 - box.x1, box.y2 - box.y1
    # A short line in a big box reaches the edge of a window sized from its
    # own text (ch.0 p.13: "ALL THE TIME." in an L-shaped caption), so widen
    # the window before calling the piece open artwork.
    for scale in TONE_WINDOWS:
        reach = int(scale * max(bw, bh)) + 20
        found = _toned_in_window(image, page_mask, box, reach)
        if found is not False:
            return found
    return None


def _toned_in_window(image: np.ndarray, page_mask: np.ndarray, box: BBox, reach: int):
    """``toned_interior`` within one window: the area, None, or False if the
    piece ran off the window (try a larger one)."""
    height, width = image.shape[:2]
    x1, y1 = max(0, box.x1 - reach), max(0, box.y1 - reach)
    x2, y2 = min(width, box.x2 + reach), min(height, box.y2 + reach)
    grey = _gray(image[y1:y2, x1:x2]).astype(np.float32)
    glyphs = dilate((page_mask[y1:y2, x1:x2] > 0).astype(np.uint8), 3) > 0
    inner = np.zeros(grey.shape, bool)
    inner[box.y1 - y1 : box.y2 - y1, box.x1 - x1 : box.x2 - x1] = True
    behind = inner & ~glyphs
    if behind.sum() < 30:
        return None
    # The mean, not the median: on dot tone the median is the paper white,
    # and a white plug where the letters were walls the flood in.
    grey[glyphs] = float(grey[behind].mean())
    blurred = cv2.GaussianBlur(grey, (0, 0), TONE_BLUR)
    # Flood from the lettering, stepping only between near-equal neighbours:
    # follows a gradual shade across the box, stops at its drawn edge.
    ys, xs = np.nonzero(behind)
    k = len(ys) // 2
    filled = np.zeros((grey.shape[0] + 2, grey.shape[1] + 2), np.uint8)
    cv2.floodFill(blurred, filled, (int(xs[k]), int(ys[k])), 0, TONE_STEP, TONE_STEP,
                  cv2.FLOODFILL_MASK_ONLY | (255 << 8) | 4)
    piece = filled[1:-1, 1:-1] > 0
    sides = [(x1 > 0, piece[:, 0]), (x2 < width, piece[:, -1]), (y1 > 0, piece[0]), (y2 < height, piece[-1])]
    if any(open_side and run.mean() > 0.05 for open_side, run in sides):
        return False
    out = np.zeros((height, width), np.uint8)
    out[y1:y2, x1:x2] = (piece | inner).astype(np.uint8)
    return out


#: Share of the pixels behind a region's letters that must be dark for the
#: background to count as tone rather than a plain bubble. A white bubble has
#: next to none (stray specks, the odd outline corner); dot tone is dots
#: everywhere.
TONE_DARK_SHARE = 0.08


def _on_tone(image: np.ndarray, page_mask: np.ndarray, box: BBox) -> bool:
    """Whether the lettering sits on dot tone or a gradient, not plain paper.

    The median cannot say: on dot tone it is the paper white between the dots.
    The share of dark pixels behind the letters can.
    """
    grey = _gray(image[box.y1 : box.y2, box.x1 : box.x2])
    glyphs = dilate((page_mask[box.y1 : box.y2, box.x1 : box.x2] > 0).astype(np.uint8), 3) > 0
    behind = grey[~glyphs]
    if behind.size < 30:
        return False
    return float((behind < 200).mean()) > TONE_DARK_SHARE


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
        found = None if _inverted(image, page_mask, region.bbox) else bubble_interior(
            image, page_mask, region.bbox
        )
        # The light-pixel search stops at dot tone and gradients; the tone
        # search follows them. But only letters that actually sit on tone get
        # the tone search: "take whichever is larger" let it win on plain white
        # bubbles too, where it escapes past an outline that touches artwork
        # (Super Lovers ch.1, 2026-10-13: 4x-14x the real bubble; p.14 set its
        # line above an empty bubble, p.38 merged two bubbles into one area).
        if not _inverted(image, page_mask, region.bbox) and (
            found is None or _on_tone(image, page_mask, region.bbox)
        ):
            toned = toned_interior(image, page_mask, region.bbox)
            if toned is not None and (found is None or toned.sum() > found.sum()):
                found = toned
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
