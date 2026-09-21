"""The detect stage: a page in, a `PageAnalysis` document out.

Writes three artifacts per page:

``analysis/page_NNN.json``  regions, kinds, reading order
``masks/page_NNN.png``      the text mask the cleaning stage will erase
                            (kept because re-running detection is the slowest
                            step in the pipeline, ~10s a page on CPU)

Regions come from the mask rather than the detector's box proposals; see
CLAUDE.md for why that distinction matters.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from comic.paths import ChapterPaths
from comic.schemas import (
    PageAnalysis,
    ReadingDirection,
    TextRegion,
    region_id,
)
from comic.vision.detector import detect, is_grayscale, load_page
from comic.vision.regions import extract_regions, order_regions

log = logging.getLogger(__name__)


def analyze_page(
    image_path: Path,
    page: int,
    direction: ReadingDirection,
    conf_threshold: float | None = None,
) -> tuple[PageAnalysis, np.ndarray]:
    """Detect text on one page and order it for reading.

    Returns the analysis document and the text mask, so the caller can persist
    both without decoding the page twice.
    """
    image = load_page(image_path)
    height, width = image.shape[:2]

    kwargs = {} if conf_threshold is None else {"conf_threshold": conf_threshold}
    result = detect(image, **kwargs)

    right_to_left = direction is ReadingDirection.RTL
    candidates = order_regions(
        extract_regions(image, result.mask), right_to_left=right_to_left
    )

    regions = [
        TextRegion(
            id=region_id(page, index),
            page=page,
            bbox=candidate.bbox,
            kind=candidate.kind,
            polygon=[],
            bubble_polygon=[],
            reading_order=index,
            is_vertical=candidate.is_vertical,
        )
        for index, candidate in enumerate(candidates, start=1)
    ]

    analysis = PageAnalysis(
        page=page,
        width=width,
        height=height,
        reading_direction=direction,
        panels=[],
        regions=regions,
        is_grayscale=is_grayscale(image),
    )
    return analysis, result.mask


def save_mask(mask: np.ndarray, path: Path) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    # imencode rather than imwrite: imwrite cannot handle non-ASCII paths, and
    # series names are routinely non-ASCII here.
    ok, buffer = cv2.imencode(".png", mask)
    if not ok:
        raise RuntimeError(f"could not encode mask for {path}")
    path.write_bytes(buffer.tobytes())


def load_mask(path: Path) -> np.ndarray | None:
    import cv2

    if not path.exists():
        return None
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def crop_region(
    image: np.ndarray, region: TextRegion, pad: int = 8, scale: int = 1
) -> np.ndarray:
    """Crop a region, optionally upscaled.

    OCR engines do measurably better on an enlarged crop when the source text is
    small, which is common: region heights on a screen-captured page cluster
    well below what recognition wants.
    """
    import cv2

    height, width = image.shape[:2]
    box = region.bbox.padded(pad, bounds=(width, height))
    crop = image[box.y1:box.y2, box.x1:box.x2]
    if scale > 1 and crop.size:
        crop = cv2.resize(
            crop,
            (crop.shape[1] * scale, crop.shape[0] * scale),
            interpolation=cv2.INTER_CUBIC,
        )
    return crop


def write_crop(crop: np.ndarray, path: Path) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError(f"could not encode crop for {path}")
    path.write_bytes(buffer.tobytes())


def overlay_regions(image: np.ndarray, analysis: PageAnalysis) -> np.ndarray:
    """A page with numbered, colour-coded regions drawn on it.

    This is what the agent reads at a gate: the numbers are the region ids, so a
    response can refer to them without ambiguity.
    """
    import cv2

    colours = {
        "dialogue": (0, 200, 0),
        "narration": (0, 120, 255),
        "caption": (255, 140, 0),
        "thought": (200, 0, 200),
        "shout": (255, 60, 60),
        "sfx": (255, 0, 255),
    }
    canvas = image.copy()
    thickness = max(2, image.shape[1] // 300)
    font_scale = max(0.5, image.shape[1] / 900)

    for index, region in enumerate(analysis.regions, start=1):
        colour = colours.get(region.kind.value, (128, 128, 128))
        b = region.bbox
        cv2.rectangle(canvas, (b.x1, b.y1), (b.x2, b.y2), colour, thickness)
        cv2.putText(
            canvas, str(index), (b.x1 + 3, max(14, b.y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, colour, thickness,
        )
    return canvas


#: Contact-sheet layout. Crops are enlarged until their text is legible by eye,
#: then tiled, because reading twenty crops as twenty images costs twenty times
#: as much attention as reading one sheet.
SHEET_WIDTH = 1500
SHEET_CROP_HEIGHT = 260
SHEET_MAX_CROP_WIDTH = 460
SHEET_GAP = 14
SHEET_LABEL = 22


def contact_sheet(
    crops: list[tuple[str, np.ndarray]],
    width: int = SHEET_WIDTH,
) -> np.ndarray:
    """Tile labelled crops into one reviewable image.

    Every crop is scaled to the same height so small bubbles are not punished
    for being small -- a two-character interjection needs the same enlargement
    to read as a five-column speech.
    """
    import cv2

    if not crops:
        return np.full((60, width, 3), 255, dtype=np.uint8)

    tiles: list[tuple[str, np.ndarray]] = []
    for region_id, crop in crops:
        if crop is None or crop.size == 0:
            continue
        image = crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        height, wide = image.shape[:2]
        scale = SHEET_CROP_HEIGHT / max(1, height)
        new_w = max(1, int(round(wide * scale)))
        if new_w > SHEET_MAX_CROP_WIDTH:
            scale = SHEET_MAX_CROP_WIDTH / max(1, wide)
            new_w = SHEET_MAX_CROP_WIDTH
        new_h = max(1, int(round(height * scale)))
        tiles.append(
            (region_id, cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC))
        )

    rows: list[list[tuple[str, np.ndarray]]] = [[]]
    used = 0
    for tile in tiles:
        tile_w = tile[1].shape[1] + SHEET_GAP
        if used + tile_w > width and rows[-1]:
            rows.append([])
            used = 0
        rows[-1].append(tile)
        used += tile_w

    row_heights = [
        max((t[1].shape[0] for t in row), default=0) + SHEET_LABEL + SHEET_GAP
        for row in rows
    ]
    sheet = np.full((max(1, sum(row_heights)), width, 3), 255, dtype=np.uint8)

    y = 0
    for row, row_height in zip(rows, row_heights):
        x = SHEET_GAP // 2
        for region_id, tile in row:
            th, tw = tile.shape[:2]
            top = y + SHEET_LABEL
            sheet[top : top + th, x : x + tw] = tile
            cv2.rectangle(
                sheet, (x - 1, top - 1), (x + tw, top + th), (150, 150, 150), 1
            )
            cv2.putText(
                sheet, region_id, (x, y + SHEET_LABEL - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )
            x += tw + SHEET_GAP
        y += row_height
    return sheet
