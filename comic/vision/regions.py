"""Turning a text mask into text regions.

Measured on a real manga page: the detector's segmentation mask covers every
piece of lettering accurately, while its box proposals miss a substantial
fraction of them. So regions are derived from the *mask* -- dilate to glue
characters into blocks, then take connected components -- and the box proposals
are used only as a hint for classifying what kind of text a region holds.

Getting this the other way round loses whole speech bubbles silently, which is
the worst failure mode here: a missed bubble is never translated and never
cleaned, and nothing downstream can notice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from comic.schemas import BBox, TextKind

#: Gap (as a fraction of page width) below which two glyphs belong together.
#: Vertical is looser than horizontal because lines of a bubble stack closely.
H_GAP_RATIO = 0.012
V_GAP_RATIO = 0.010

#: Components smaller than this fraction of the page are noise, not lettering.
MIN_AREA_RATIO = 0.00004

#: A region wider than this fraction of the page is almost certainly a mistake.
MAX_WIDTH_RATIO = 0.95

#: How far outside a region to sample when judging its background.
SURROUND_PAD = 14

#: Above this mean luminance the background counts as a light bubble interior.
#: Measured across a real manga page: genuine bubbles sit at 235-251, while text
#: over artwork sits at 137-217. Screentone bleeding into the sample is why this
#: is not simply "white".
BUBBLE_LUMA = 228
#: Below this spread the background counts as flat. Tuned the same way: bubbles
#: measured 10-51, artwork 74-105. A tighter bound loses half the real bubbles.
BUBBLE_FLATNESS = 60

#: Fraction of a region's box that must actually be text pixels. The single
#: strongest signal for "is this lettering at all": real text measured 0.31-0.42
#: on that page, while mask noise over artwork measured 0.03-0.06.
MIN_DENSITY = 0.12

# Splitting a region that holds two balloons (``text_blocks``), in units of
# the lettering's own letter height ("cap"): letters closer than WORD_GAP
# are one line; lines within LINE_GAP that overlap by OVERLAP of the
# narrower and share a centre (within CENTRE of the wider) or an edge are
# one balloon. A block needs BLOCK_MIN_SHARE of the ink to count. Measured
# on Dare mo ch.1-3, Kimigazer, Super Lovers and Ju Jue (vertical).
BLOCK_MIN_CAP = 8
BLOCK_WORD_GAP = 0.8
BLOCK_ACCENT_GAP = 0.3
BLOCK_LINE_GAP = 0.9
BLOCK_OVERLAP = 0.25
BLOCK_CENTRE = 0.35
BLOCK_MIN_SHARE = 0.15
BLOCK_PAD = 4


@dataclass
class RegionCandidate:
    bbox: BBox
    kind: TextKind
    #: Fraction of the bbox that is actually text pixels; low means a loose box.
    density: float
    is_vertical: bool
    #: True when the region sits on a flat, light background (a bubble).
    in_bubble: bool


def _kernel(width: int, ratio: float, minimum: int = 3):
    import cv2

    size = max(minimum, int(round(width * ratio)) | 1)
    return size


def group_mask(mask: np.ndarray) -> np.ndarray:
    """Dilate a glyph mask so characters in one block merge into one blob."""
    import cv2

    height, width = mask.shape[:2]
    h_size = _kernel(width, H_GAP_RATIO)
    v_size = _kernel(width, V_GAP_RATIO)

    horizontal = cv2.getStructuringElement(cv2.MORPH_RECT, (h_size, 1))
    vertical = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_size))
    grouped = cv2.dilate(mask, horizontal, iterations=1)
    grouped = cv2.dilate(grouped, vertical, iterations=1)
    # Close the remaining pinholes so one bubble is one component.
    grouped = cv2.morphologyEx(
        grouped, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (h_size, v_size)),
    )
    return grouped


def _background(image: np.ndarray, mask: np.ndarray, bbox: BBox) -> tuple[float, float]:
    """Mean and spread of the non-text pixels *behind* a region.

    Sampling a ring outside the box instead looks obvious but fails: a tight box
    sits right against the bubble's black outline, so the ring is half ink and
    reads as high-variance artwork. The pixels between and around the letters,
    inside the box, are the actual background -- flat white in a bubble, busy
    linework when the text sits over art.
    """
    height, width = image.shape[:2]
    outer = bbox.padded(SURROUND_PAD // 2, bounds=(width, height))
    patch = image[outer.y1:outer.y2, outer.x1:outer.x2]
    crop = mask[outer.y1:outer.y2, outer.x1:outer.x2]
    if patch.size == 0:
        return 0.0, 255.0
    if patch.ndim == 3:
        patch = patch.mean(axis=2)

    # Ignore the glyphs themselves, plus a little bleed around their edges.
    import cv2

    bleed = cv2.dilate(crop, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    background = patch[bleed == 0]
    if background.size < 24:
        return float(patch.mean()), float(patch.std())
    return float(background.mean()), float(background.std())


def classify(
    image: np.ndarray,
    mask: np.ndarray,
    bbox: BBox,
    mask_crop: np.ndarray,
) -> tuple[TextKind, bool]:
    """Decide what kind of text a region holds (spec 12).

    Not everything is dialogue: narration boxes, free-floating captions and
    sound effects all need different handling downstream, and SFX must be left
    untouched entirely.
    """
    luma, spread = _background(image, mask, bbox)
    in_bubble = luma >= BUBBLE_LUMA and spread <= BUBBLE_FLATNESS

    page_h, page_w = image.shape[:2]
    coverage = float((mask_crop > 0).mean()) if mask_crop.size else 0.0
    relative_height = bbox.height / page_h

    # Heavy, sparse strokes sprawling over artwork are sound effects.
    if not in_bubble and coverage < 0.16 and relative_height > 0.045:
        return TextKind.SFX, False
    if in_bubble:
        return TextKind.DIALOGUE, True
    # Rectangular, flat-backgrounded blocks at panel edges read as narration.
    if spread <= BUBBLE_FLATNESS:
        return TextKind.NARRATION, False
    return TextKind.CAPTION, False


def text_blocks(mask_crop: np.ndarray, vertical: bool = False) -> list[BBox]:
    """The separate blocks of lettering inside one region, in crop coordinates.

    Two balloons that touch -- a joined double balloon, or a bubble butted
    against another -- are one component after ``group_mask``, so a single
    region held both, and the typesetter set both translations as one block
    in one lobe (the operator, Dare mo ch.1 p.11/20/23, 2026-10-27; 5 of 5
    such regions in that chapter, ~18 in Kimigazer ch.1-3). The lettering
    still shows the seam: cut the mask into lines, and lines of one balloon
    stack -- vertically close, overlapping, sharing a centre or a left or
    right edge. Lines that do not chain that way are another balloon.

    Returns the blocks big enough to be lettering of their own (a lone "!"
    or "..." beside a balloon is not one); one block means nothing to split.
    ``vertical`` reads CJK columns as the lines.
    """
    import cv2

    m = (mask_crop > 0).astype(np.uint8)
    if vertical:
        m = np.ascontiguousarray(m.T)
    count, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    heights = [stats[i, 3] for i in range(1, count) if stats[i, 4] >= 6]
    if len(heights) < 4:
        return []
    cap = float(np.median(heights))
    if cap < BLOCK_MIN_CAP:            # a speckled mask, not letters
        return []

    lines = cv2.dilate(m, cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(3, round(BLOCK_WORD_GAP * cap)), max(1, round(BLOCK_ACCENT_GAP * cap))),
    ))
    count, _, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    segs = [tuple(int(v) for v in stats[i, :4]) for i in range(1, count)]
    parent = list(range(len(segs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (x, y, w, h) in enumerate(segs):
        for j in range(i + 1, len(segs)):
            X, Y, W, H = segs[j]
            vgap = max(Y - (y + h), y - (Y + H))
            overlap = min(x + w, X + W) - max(x, X)
            if vgap > BLOCK_LINE_GAP * cap or overlap < BLOCK_OVERLAP * min(w, W):
                continue
            aligned = (abs((x + w / 2) - (X + W / 2)) <= BLOCK_CENTRE * max(w, W)
                       or abs(x - X) <= cap or abs((x + w) - (X + W)) <= cap)
            if aligned or vgap <= 0:
                parent[find(i)] = find(j)

    groups: dict[int, list[tuple[int, int, int, int]]] = {}
    for i, seg in enumerate(segs):
        groups.setdefault(find(i), []).append(seg)
    blocks = []
    for members in groups.values():
        x1 = min(s[0] for s in members)
        y1 = min(s[1] for s in members)
        x2 = max(s[0] + s[2] for s in members)
        y2 = max(s[1] + s[3] for s in members)
        blocks.append((x1, y1, x2, y2, int(m[y1:y2, x1:x2].sum())))
    total = sum(b[4] for b in blocks) or 1
    big = [b for b in blocks
           if b[2] - b[0] >= 2 * cap and b[3] - b[1] >= 0.8 * cap
           and b[4] >= BLOCK_MIN_SHARE * total]
    if vertical:
        big = [(y1, x1, y2, x2, ink) for (x1, y1, x2, y2, ink) in big]
    return [BBox(x1=b[0], y1=b[1], x2=b[2], y2=b[3]) for b in big]


def split_merged(
    image: np.ndarray,
    mask: np.ndarray,
    candidate: RegionCandidate,
    vertical: bool = False,
) -> list[RegionCandidate]:
    """One candidate per balloon: the candidate itself, or its blocks."""
    box = candidate.bbox
    if candidate.kind is TextKind.SFX:
        return [candidate]
    blocks = text_blocks(mask[box.y1:box.y2, box.x1:box.x2], vertical)
    if len(blocks) < 2:
        return [candidate]
    out = []
    for b in blocks:
        sub = BBox(
            x1=max(box.x1, box.x1 + b.x1 - BLOCK_PAD), y1=max(box.y1, box.y1 + b.y1 - BLOCK_PAD),
            x2=min(box.x2, box.x1 + b.x2 + BLOCK_PAD), y2=min(box.y2, box.y1 + b.y2 + BLOCK_PAD),
        )
        crop = mask[sub.y1:sub.y2, sub.x1:sub.x2]
        kind, in_bubble = classify(image, mask, sub, crop)
        out.append(RegionCandidate(
            bbox=sub, kind=kind,
            density=float((crop > 0).mean()) if crop.size else 0.0,
            is_vertical=(sub.y2 - sub.y1) > (sub.x2 - sub.x1) * 1.6,
            in_bubble=in_bubble,
        ))
    return out


def extract_regions(
    image: np.ndarray,
    mask: np.ndarray,
    detections: list | None = None,
    vertical_text: bool = False,
) -> list[RegionCandidate]:
    """Find every text region on a page from its mask.

    A region holding two balloons' lettering is split into one per balloon
    (``split_merged``); ``vertical_text`` says the lettering runs in columns.
    """
    import cv2

    height, width = mask.shape[:2]
    grouped = group_mask(mask)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)

    min_area = MIN_AREA_RATIO * height * width
    max_width = MAX_WIDTH_RATIO * width

    candidates: list[RegionCandidate] = []
    for index in range(1, count):                     # 0 is the background
        x, y, w, h, area = stats[index]
        if area < min_area or w > max_width:
            continue
        if w < 8 or h < 8:
            continue

        bbox = BBox(x1=int(x), y1=int(y), x2=int(x + w), y2=int(y + h))
        crop = mask[bbox.y1:bbox.y2, bbox.x1:bbox.x2]
        density = float((crop > 0).mean()) if crop.size else 0.0
        if density < MIN_DENSITY:      # mask speckle on artwork, not lettering
            continue

        kind, in_bubble = classify(image, mask, bbox, crop)
        candidate = RegionCandidate(
            bbox=bbox,
            kind=kind,
            density=density,
            # Japanese manga sets dialogue vertically; tall narrow blocks
            # need a different OCR path and a different typesetting box.
            is_vertical=h > w * 1.6,
            in_bubble=in_bubble,
        )
        candidates.extend(split_merged(image, mask, candidate, vertical_text))
    return candidates


def order_regions(
    regions: list[RegionCandidate], right_to_left: bool
) -> list[RegionCandidate]:
    """Sort regions into reading order within a page (spec 13).

    Regions overlapping vertically form a row; within a row, manga reads right
    to left. Row grouping uses each region's own height, so a tall bubble does
    not swallow a short one beside it.
    """
    if not regions:
        return []

    rows: list[list[RegionCandidate]] = []
    for region in sorted(regions, key=lambda r: r.bbox.y1):
        placed = False
        for row in rows:
            reference = row[-1]
            overlap = min(region.bbox.y2, reference.bbox.y2) - max(
                region.bbox.y1, reference.bbox.y1
            )
            shorter = min(region.bbox.height, reference.bbox.height)
            if shorter > 0 and overlap / shorter >= 0.4:
                row.append(region)
                placed = True
                break
        if not placed:
            rows.append([region])

    ordered: list[RegionCandidate] = []
    for row in rows:
        row.sort(key=lambda r: r.bbox.x1, reverse=right_to_left)
        ordered.extend(row)
    return ordered
