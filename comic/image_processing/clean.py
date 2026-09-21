"""Erasing source lettering without regenerating the page.

Two tiers do nearly all the work, chosen per region by measuring the background
rather than by guessing from the region's kind:

* **flat** -- the lettering sat on a bubble interior, which is by construction
  a light, even area. Filling that area with its own modal colour erases the
  text and changes nothing else, because everywhere but the glyphs already
  *was* that colour. This handles the large majority of dialogue, and it keeps
  the bubble outline perfectly, which an inpainting model will not.
* **inpaint** -- the lettering sat over artwork. Only the dilated glyph pixels
  are touched, and the repair is composited back through that mask.

A third tier exists but is not code: `visual_qa` returns `expand_mask` /
`shrink_mask` / `force_inpaint`, and the corrected mask comes back through
here. The agent decides what is wrong; this module decides how pixels move.

The invariant that matters (CLAUDE.md): **the page is never regenerated.**
Every tier ends in a composite through its own mask, so a pixel no mask names
is bit-identical to the original. ``clean_page`` returns that union so the
guarantee can be asserted rather than assumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from comic.image_processing.mask import GLYPH_DILATION, _gray, region_glyphs
from comic.schemas import RegionOverride, TextRegion

log = logging.getLogger(__name__)

#: Below this spread the background counts as flat enough to fill. Matches the
#: bubble measurements in `vision/regions.py` (bubbles 10-51, artwork 74-105).
FLAT_SPREAD = 60
#: And it has to be light, or we are filling a dark panel with its own grey.
FLAT_LUMA = 170

#: LaMa's fixed input size.
LAMA_SIZE = 512
#: How much context to give the model around the region being repaired.
INPAINT_CONTEXT = 32


@dataclass
class CleanReport:
    """What the cleaning pass did, per region, so it can be reviewed."""

    tiers: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def count(self, tier: str) -> int:
        return sum(1 for value in self.tiers.values() if value == tier)


def _modal_colour(image: np.ndarray, area: np.ndarray) -> np.ndarray:
    """The most common colour in ``area``, quantised so near-identical
    anti-aliased shades count as one."""
    pixels = image[area > 0]
    if pixels.size == 0:
        return np.array([255, 255, 255], dtype=np.uint8)
    if pixels.ndim == 1:
        pixels = pixels[:, None]
    quantised = (pixels // 8).astype(np.int32)
    keys = quantised[:, 0]
    for channel in range(1, quantised.shape[1]):
        keys = keys * 32 + quantised[:, channel]
    winner = np.bincount(keys).argmax()
    chosen = quantised[keys == winner]
    return (np.median(chosen, axis=0) * 8 + 4).clip(0, 255).astype(np.uint8)


def background_of(
    image: np.ndarray, area: np.ndarray, glyphs: np.ndarray
) -> tuple[float, float]:
    """(mean luma, spread) of the area, ignoring the lettering itself.

    Sampling the glyphs would drag the mean toward the ink and make every
    bubble look like textured artwork.
    """
    clean = (area > 0) & (glyphs == 0)
    if clean.sum() < 16:
        clean = area > 0
    values = _gray(image)[clean]
    if values.size == 0:
        return 0.0, 255.0
    return float(values.mean()), float(values.std())


class _Lama:
    """LaMa inpainting over onnxruntime, loaded once and reused."""

    def __init__(self, model: Path) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(model), providers=["CPUExecutionProvider"]
        )

    def repair(self, patch: np.ndarray, hole: np.ndarray) -> np.ndarray:
        height, width = patch.shape[:2]
        rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
        small = cv2.resize(rgb, (LAMA_SIZE, LAMA_SIZE), interpolation=cv2.INTER_AREA)
        small_hole = cv2.resize(
            hole, (LAMA_SIZE, LAMA_SIZE), interpolation=cv2.INTER_NEAREST
        )
        image_in = small.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        mask_in = (small_hole > 0)[None, None].astype(np.float32)
        out = self._session.run(
            None, {"image": image_in, "mask": mask_in}
        )[0]
        painted = out[0].transpose(1, 2, 0)
        if painted.max() <= 1.5:
            painted = painted * 255.0
        painted = painted.clip(0, 255).astype(np.uint8)
        painted = cv2.resize(painted, (width, height), interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(painted, cv2.COLOR_RGB2BGR)


_LAMA: _Lama | None = None
_LAMA_TRIED = False


def _lama(model: Path | None) -> _Lama | None:
    """Load LaMa once. A missing or broken model is not fatal: OpenCV's
    inpainting is worse but always available, and a chapter that cleans
    slightly less well beats a chapter that does not clean at all."""
    global _LAMA, _LAMA_TRIED
    if _LAMA_TRIED:
        return _LAMA
    _LAMA_TRIED = True
    if model and model.exists():
        try:
            _LAMA = _Lama(model)
        except Exception as exc:  # noqa: BLE001 - any failure falls back
            log.warning("LaMa unavailable (%s); using OpenCV inpainting", exc)
    return _LAMA


def _inpaint(
    image: np.ndarray, hole: np.ndarray, model: Path | None
) -> np.ndarray:
    """Repair ``hole`` in ``image``, returning a full-size replacement."""
    engine = _lama(model)
    if engine is None:
        return cv2.inpaint(image, (hole > 0).astype(np.uint8), 3, cv2.INPAINT_TELEA)
    return engine.repair(image, hole)


def clean_page(
    image: np.ndarray,
    page_mask: np.ndarray,
    regions: list[TextRegion],
    areas: dict[str, np.ndarray],
    model: Path | None = None,
    dilation: int = GLYPH_DILATION,
    overrides: dict[str, RegionOverride] | None = None,
) -> tuple[np.ndarray, np.ndarray, CleanReport]:
    """Erase the source lettering.

    Returns (cleaned page, the union of every touched pixel, report). The
    second value is what makes the artwork-preservation guarantee checkable.
    """
    cleaned = image.copy()
    touched = np.zeros(image.shape[:2], dtype=np.uint8)
    report = CleanReport()

    for region in regions:
        if not region.translatable or not region.source_text.strip():
            continue
        area = areas.get(region.id)
        if area is None or not area.any():
            report.notes.append(f"{region.id}: no area to clean")
            continue

        override = (overrides or {}).get(region.id) or RegionOverride()
        box = region.bbox
        glyphs = region_glyphs(
            page_mask, region, max(0, dilation + override.mask_grow)
        )

        luma, spread = background_of(image, area, glyphs)
        if not override.force_inpaint and luma >= FLAT_LUMA and spread <= FLAT_SPREAD:
            # Fill the whole interior: it was already one flat colour, so this
            # erases the lettering and is a no-op everywhere else.
            fill = _modal_colour(image, (area > 0) & (glyphs == 0))
            spot = area > 0
            cleaned[spot] = fill
            touched |= spot.astype(np.uint8)
            report.tiers[region.id] = "flat"
            continue

        # Over artwork: touch only the glyphs, and give the model context.
        top = max(0, box.y1 - INPAINT_CONTEXT)
        left = max(0, box.x1 - INPAINT_CONTEXT)
        bottom = min(image.shape[0], box.y2 + INPAINT_CONTEXT)
        right = min(image.shape[1], box.x2 + INPAINT_CONTEXT)
        patch = image[top:bottom, left:right]
        hole = glyphs[top:bottom, left:right]
        if not hole.any():
            report.notes.append(f"{region.id}: nothing to erase")
            continue

        repaired = _inpaint(patch, hole, model)
        spot = hole > 0
        window = cleaned[top:bottom, left:right]
        window[spot] = repaired[spot]
        cleaned[top:bottom, left:right] = window
        touched[top:bottom, left:right] |= spot.astype(np.uint8)
        report.tiers[region.id] = "inpaint"

    return cleaned, touched, report
