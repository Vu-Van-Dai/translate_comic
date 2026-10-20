"""Placement checks on a rendered page: blocks off-centre in their bubble,
and lines that spill outside it.

These were ``scripts/centre_check.py`` and ``scripts/spill_check.py``, which
every chapter had to run by hand after ``comic render`` (the operator's first
rule, 2026-10-15: "the dialogue must be centred in its speech bubble").
``render_one`` now runs both on the pixels it has just produced and hands the
findings to the visual_qa request as pre-checks; the scripts call the same
functions, so a standalone re-check prints exactly what it printed before.

The measurements are the scripts', unchanged. The new ink is where the
typeset page differs from the cleaned one, and each new-ink pixel belongs to
the nearest translated region:

- **off-centre** compares the block's ink box with the bubble's centre,
  measured the way the fitter measures it (``fit._bubble_centre``: a cross
  through the source box, tails ignored, leaks guarded);
- **spill** counts the region's new ink lying outside its own *bubble* (the
  light-pixel interior, not the fit area, which may have leaked).

Both are reviews for the eye, not verdicts: dot-tone captions, SFX areas and
shared or leaky bubbles raise false alarms, so the findings go to visual_qa
as warnings.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from comic.schemas import TextRegion

#: Off-centre: allowed offset as a share of the bubble's size ...
CENTRE_TOL = 0.08
#: ... and in pixels regardless (the scripts' ``--tol`` / ``--px`` defaults).
CENTRE_PX = 6
#: Spilled pixels worth reporting (the spill script's ``--min`` default).
SPILL_MIN = 25
#: A rendered pixel is new ink when it differs from the cleaned page by more
#: than this in any channel.
INK_DELTA = 60
#: New ink a region needs before its centring is judged at all.
MIN_CENTRE_INK = 30


@dataclass(frozen=True)
class OffCentre:
    region_id: str
    dx: float
    dy: float
    bubble_w: int
    bubble_h: int
    #: The larger offset as a share of the bubble along its axis.
    score: float
    text: str

    def line(self) -> str:
        """The line ``scripts/centre_check.py`` prints."""
        return (
            f"{self.region_id} off dx{self.dx:+.0f} dy{self.dy:+.0f} "
            f"(bubble {self.bubble_w}x{self.bubble_h}, {self.score:.0%})  {self.text!r}"
        )

    def detail(self) -> str:
        """The visual_qa pre-check detail."""
        return (
            f"off-centre dx{self.dx:+.0f} dy{self.dy:+.0f}px in a "
            f"{self.bubble_w}x{self.bubble_h} bubble ({self.score:.0%})"
        )


@dataclass(frozen=True)
class Spill:
    region_id: str
    pixels: int
    x1: int
    x2: int
    y1: int
    y2: int
    text: str

    def line(self) -> str:
        """The line ``scripts/spill_check.py`` prints."""
        return (
            f"{self.region_id} spill {self.pixels:5d}px at x{self.x1}-{self.x2} "
            f"y{self.y1}-{self.y2}  {self.text!r}"
        )

    def detail(self) -> str:
        """The visual_qa pre-check detail."""
        return (
            f"{self.pixels}px of the line outside its bubble at "
            f"x{self.x1}-{self.x2} y{self.y1}-{self.y2}"
        )


def translated_regions(
    regions: list[TextRegion], text: dict[str, str]
) -> list[TextRegion]:
    """The page's regions that carry a translation, in page order."""
    return [r for r in regions if text.get(r.id, "").strip()]


def new_ink(rendered: np.ndarray, cleaned: np.ndarray) -> np.ndarray:
    """Pixels the typesetter drew: where the rendered page left the cleaned one."""
    delta = np.abs(rendered.astype(np.int16) - cleaned.astype(np.int16))
    return delta.max(axis=-1) > INK_DELTA


def _owners(shape: tuple[int, int], regions: list[TextRegion]) -> np.ndarray:
    """For every pixel, the index of the nearest region's box."""
    import cv2

    h, w = shape
    dist = []
    for r in regions:
        seed = np.ones((h, w), np.uint8)
        seed[r.bbox.y1:r.bbox.y2, r.bbox.x1:r.bbox.x2] = 0
        dist.append(cv2.distanceTransform(seed, cv2.DIST_L2, 3))
    return np.argmin(np.stack(dist), axis=0)


class _Bubbles:
    """Each region's bubble, found once and shared by both checks."""

    def __init__(self, image: np.ndarray, mask: np.ndarray) -> None:
        self.image, self.mask = image, mask
        self._found: dict[str, np.ndarray | None] = {}

    def of(self, region: TextRegion) -> np.ndarray | None:
        if region.id not in self._found:
            from comic.image_processing.mask import _inverted, bubble_interior

            self._found[region.id] = (
                None
                if _inverted(self.image, self.mask, region.bbox)
                else bubble_interior(self.image, self.mask, region.bbox)
            )
        return self._found[region.id]


def check_page(
    image: np.ndarray,
    mask: np.ndarray,
    rendered: np.ndarray,
    cleaned: np.ndarray,
    regions: list[TextRegion],
    text: dict[str, str],
    areas: dict[str, np.ndarray] | None = None,
    centre: bool = True,
    spill: bool = True,
    tol: float = CENTRE_TOL,
    px: int = CENTRE_PX,
    minimum: int = SPILL_MIN,
) -> tuple[list[OffCentre], list[Spill]]:
    """Both checks for one rendered page.

    ``regions`` is the page's whole analysis, ``text`` region id -> Vietnamese.
    ``areas`` may be the renderer's own ``fit_areas`` over those regions; it
    is reused where it is what a check would compute, and recomputed where
    not, so the findings match the standalone scripts exactly.

    Off-centre findings come back worst first, spills in page order -- each
    the order its script printed.
    """
    from comic.image_processing.mask import dilate, fit_areas

    translated = translated_regions(regions, text)
    if not translated:
        return [], []

    ink = new_ink(rendered, cleaned)
    owner = _owners(ink.shape, translated)
    bubbles = _Bubbles(image, mask)

    found_off: list[OffCentre] = []
    if centre:
        # The centring check splits the page as the renderer does: over every
        # region, translated or not.
        centre_areas = areas if areas is not None else fit_areas(image, mask, regions)
        found_off = _off_centre(
            translated, ink, owner, centre_areas, bubbles, text, tol, px
        )

    found_spill: list[Spill] = []
    if spill:
        # The spill check splits it over the translated regions only. That is
        # the same split whenever every region that could take a translation
        # has one -- the normal case -- because ``fit_areas`` only ever uses
        # those regions, in page order.
        usable = {r.id for r in regions if r.translatable and r.source_text.strip()}
        if areas is not None and usable <= {r.id for r in translated}:
            spill_areas = areas
        else:
            spill_areas = fit_areas(image, mask, translated)
        found_spill = _spills(
            translated, ink, owner, spill_areas, bubbles, text, minimum, dilate
        )

    return found_off, found_spill


def _off_centre(
    regions: list[TextRegion],
    ink: np.ndarray,
    owner: np.ndarray,
    areas: dict[str, np.ndarray],
    bubbles: _Bubbles,
    text: dict[str, str],
    tol: float,
    px: int,
) -> list[OffCentre]:
    from comic.typesetting.fit import _bubble_centre

    found: list[OffCentre] = []
    for i, r in enumerate(regions):
        area = areas.get(r.id)
        if area is None:
            continue
        mine = ink & (owner == i) & (area > 0)
        if mine.sum() < MIN_CENTRE_INK:
            continue
        bubble = bubbles.of(r)
        room = area > 0 if bubble is None else (bubble & (area > 0))
        if not room.any():
            continue
        box = (r.bbox.x1, r.bbox.y1, r.bbox.x2, r.bbox.y2)
        cx, cy = _bubble_centre(room, box)[:2]
        ys, xs = np.nonzero(mine)
        ix, iy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
        ry, rx = np.nonzero(room)
        bw, bh = rx.max() - rx.min() + 1, ry.max() - ry.min() + 1
        dx, dy = ix - cx, iy - cy
        score = max(abs(dx) / bw, abs(dy) / bh)
        if (abs(dx) > max(px, tol * bw)) or (abs(dy) > max(px, tol * bh)):
            found.append(OffCentre(
                region_id=r.id, dx=float(dx), dy=float(dy),
                bubble_w=int(bw), bubble_h=int(bh), score=float(score),
                text=text[r.id][:40],
            ))
    # Worst first, ties as the script broke them (by the rest of the tuple).
    found.sort(
        key=lambda f: (f.score, f.region_id, f.dx, f.dy, f.bubble_w, f.bubble_h, f.text),
        reverse=True,
    )
    return found


def _spills(
    regions: list[TextRegion],
    ink: np.ndarray,
    owner: np.ndarray,
    areas: dict[str, np.ndarray],
    bubbles: _Bubbles,
    text: dict[str, str],
    minimum: int,
    dilate,
) -> list[Spill]:
    found: list[Spill] = []
    for i, r in enumerate(regions):
        area = areas.get(r.id)
        if area is None:
            continue
        mine = ink & (owner == i)
        if not mine.any():
            continue
        bubble = bubbles.of(r)
        room = area if bubble is None else (bubble & area)
        out = mine & (dilate(room, 2) == 0)
        n = int(out.sum())
        if n >= minimum:
            ys, xs = np.nonzero(out)
            found.append(Spill(
                region_id=r.id, pixels=n,
                x1=int(xs.min()), x2=int(xs.max()), y1=int(ys.min()), y2=int(ys.max()),
                text=text[r.id][:40],
            ))
    return found
