"""Erasing source lettering without regenerating the page.

Two tiers do nearly all the work, chosen per region by measuring the background
rather than by guessing from the region's kind:

Both tiers touch **only the dilated glyph pixels** -- never the rest of the
bubble, whose dots, gradient or colour must survive (see ``clean_page``):

* **flat** -- the band just around the letters is one plain colour: the
  glyphs are filled with exactly that colour (its median, not a quantised
  bin). Most white bubbles, and dark caption boxes.
* **inpaint** -- anything busier behind the letters (screentone, gradient,
  artwork): the glyphs are repainted by LaMa from their surroundings.

A series can send every region to LaMa instead (``fonts.yaml: clean: lama``,
see ``series_clean_mode``): the operator's choice for Super Lovers,
2026-10-12, judging LaMa's erasure cleaner than a flat fill.

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

#: An area no bigger than this multiple of the text box is the box itself:
#: ``fit_areas`` found no bubble. Matches ``typesetting/render.py``.
OWN_BOX_RATIO = 1.25

#: Width of the band around the glyphs that decides what is behind them.
RING_WIDTH = 4
#: Below this luma spread in that band the letters sat on one plain colour
#: and are filled with it; anything busier (dots, gradient, art) is inpainted.
#: Measured: a white bubble's band through JPEG noise 2-6, dot tone 60+.
PLAIN_SPREAD = 12


def _dilate(mask: np.ndarray, pixels: int) -> np.ndarray:
    if pixels <= 0:
        return mask.astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pixels + 1, 2 * pixels + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel)


def _plain_colour(image: np.ndarray, ring: np.ndarray) -> np.ndarray | None:
    """The exact colour behind the lettering, or None if it is not plain."""
    if int(ring.sum()) < 16:
        return None
    if float(_gray(image)[ring].std()) > PLAIN_SPREAD:
        return None
    pixels = image[ring]
    return np.median(pixels, axis=0).round().astype(image.dtype)


#: Extra dilation for lettering with no bubble around it.
ART_EXTRA_GROW = 3


def _own_box(area: np.ndarray, box) -> bool:
    box_px = max(1, (box.x2 - box.x1) * (box.y2 - box.y1))
    return int(np.count_nonzero(area)) <= OWN_BOX_RATIO * box_px


def erase_mask(
    page_mask: np.ndarray, region: TextRegion, area: np.ndarray, grow: int
) -> np.ndarray:
    """The pixels cleaning erases for a region: its lettering, dilated."""
    glyphs = region_glyphs(page_mask, region, grow)
    inside = ((page_mask > 0) & (area > 0)).astype(np.uint8)
    return np.maximum(glyphs, _dilate(inside, grow))


def plain_behind(image: np.ndarray, glyphs: np.ndarray) -> np.ndarray | None:
    """The plain colour the lettering sat on, or None if it sat on texture."""
    ring = (_dilate(glyphs, RING_WIDTH) > 0) & (glyphs == 0)
    return _plain_colour(image, ring)


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
        # The GPU cannot run this model (measured 2026-10-12, Iris Xe):
        # DirectML rejects its FFT-attention MatMul at any optimisation
        # level; OpenVINO's GPU plugin fails to build it in f32 and in f16
        # returns garbage (mean error 30 of 255). OpenVINO on the CPU is
        # 4x faster than onnxruntime (1.8s vs 7.1s a 512px patch) with the
        # same output (mean diff 2e-5), so it is preferred; threads are split
        # across the worker processes so a render does not oversubscribe.
        self._run = None
        try:
            import openvino as ov

            from comic.cpu import threads_per_worker

            core = ov.Core()
            net = core.read_model(str(model))
            net.reshape({"image": [1, 3, LAMA_SIZE, LAMA_SIZE],
                         "mask": [1, 1, LAMA_SIZE, LAMA_SIZE]})
            threads = threads_per_worker()
            compiled = core.compile_model(net, "CPU", {"INFERENCE_NUM_THREADS": threads})
            self._run = lambda feed: next(iter(compiled(feed).values()))
        except Exception:  # noqa: BLE001 - openvino absent or unhappy: use ORT
            import onnxruntime as ort

            from comic.cpu import ort_options

            session = ort.InferenceSession(
                str(model), ort_options(), providers=["CPUExecutionProvider"]
            )
            self._run = lambda feed: session.run(None, feed)[0]

    def repair(self, patch: np.ndarray, hole: np.ndarray) -> np.ndarray:
        height, width = patch.shape[:2]
        rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
        small = cv2.resize(rgb, (LAMA_SIZE, LAMA_SIZE), interpolation=cv2.INTER_AREA)
        small_hole = cv2.resize(
            hole, (LAMA_SIZE, LAMA_SIZE), interpolation=cv2.INTER_NEAREST
        )
        image_in = small.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        mask_in = (small_hole > 0)[None, None].astype(np.float32)
        out = self._run({"image": image_in, "mask": mask_in})
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
    repaired = engine.repair(image, hole)
    # LaMa paints in colour whatever it is given. On black-and-white manga
    # it left green/red specks (Super Lovers ch.1 p.31, 2026-10-13), so a
    # patch whose surroundings carry no colour gets grey back.
    if image.ndim == 3 and _is_grey(image, hole):
        grey = cv2.cvtColor(repaired, cv2.COLOR_BGR2GRAY)
        repaired = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
    return repaired


#: Channel spread below which a pixel counts as grey (JPEG/WebP chroma noise).
GREY_SPREAD = 12


def _is_grey(image: np.ndarray, hole: np.ndarray) -> bool:
    """Whether the pixels around a hole are (nearly) all grey."""
    around = image[hole == 0]
    if around.size == 0:
        return False
    spread = around.max(axis=-1).astype(np.int16) - around.min(axis=-1)
    return float((spread > GREY_SPREAD).mean()) < 0.01


def clean_page(
    image: np.ndarray,
    page_mask: np.ndarray,
    regions: list[TextRegion],
    areas: dict[str, np.ndarray],
    model: Path | None = None,
    dilation: int = GLYPH_DILATION,
    overrides: dict[str, RegionOverride] | None = None,
    inpaint_all: bool = False,
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
        grow = max(0, dilation + override.mask_grow)
        if _own_box(area, box):
            # Lettering on open art: the detector's mask often covers only
            # part of a small hand-lettered aside, and what it misses stays
            # as ghost letters beside the new line (ch.0 p.12). Erase wider.
            grow += ART_EXTRA_GROW
        # Erase the lettering only -- never repaint the bubble. Filling the
        # whole interior with one colour wiped dot screentone, gradients and
        # coloured shading, and its quantised colour never quite matched: the
        # operator saw "a white frame ... sometimes it even has polka dots"
        # (Super Lovers ch.0). The region's lettering is the detector mask in
        # its own box plus any it marked inside the region's own area -- a
        # stray "!" -- and the areas are disjoint, so no neighbour's text.
        glyphs = erase_mask(page_mask, region, area, grow)
        if not glyphs.any():
            report.notes.append(f"{region.id}: nothing to erase")
            continue

        fill = None if inpaint_all else plain_behind(image, glyphs)
        if not override.force_inpaint and fill is not None:
            # The letters sat on one plain colour: put exactly that colour
            # back where they were, and nothing else changes.
            spot = glyphs > 0
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

        repaired = _inpaint(patch, hole, model)
        spot = hole > 0
        window = cleaned[top:bottom, left:right]
        window[spot] = repaired[spot]
        cleaned[top:bottom, left:right] = window
        touched[top:bottom, left:right] |= spot.astype(np.uint8)
        report.tiers[region.id] = "inpaint"

    return cleaned, touched, report


def series_clean_mode(fonts_file: Path) -> str:
    """``lama`` when the series' fonts.yaml says ``clean: lama``, else ``auto``."""
    import yaml

    if not fonts_file.exists():
        return "auto"
    raw = yaml.safe_load(fonts_file.read_text(encoding="utf-8")) or {}
    mode = str(raw.get("clean", "auto")).strip().lower()
    return mode if mode in ("auto", "lama") else "auto"
