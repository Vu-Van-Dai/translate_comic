"""Re-cutting a long strip into pages.

Webtoon sources do not serve pages. They serve an arbitrary stack of tall
slices whose boundaries are an artifact of how the uploader exported the file
-- routinely a fixed 1500px step that lands wherever it lands, including
through the middle of a speech bubble. Two slices of a chapter are not two
pages of it, and treating them as such puts half a bubble on each.

So the chapter is treated as one logical strip and re-cut at **safe seams**:
rows that are flat all the way across. That test is the safety guarantee, not
a heuristic dressed up as one -- lettering contrasts with whatever it sits on,
so a row containing any part of a glyph has a peak-to-peak spread far above
``FLAT_SPREAD`` and can never be chosen. The same holds for panel art. What
survives the test is gutter.

Nothing here decodes the whole strip at once. Measured on a real chapter:
720 x 97325 px, which is 210 MB decoded in colour. Seam analysis needs only one
number per row, so slices are measured one at a time and re-read when pages are
emitted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: How far a row's darkest and lightest pixel may sit apart and still count as
#: flat. Measured per channel, so a red-on-green row cannot pass by matching in
#: luma.
#:
#: Measured over 10956 rows of real source pages: a solid row's spread is
#: *exactly* 0 (1404 rows), the 20th percentile is already 239, and only 54
#: rows in the whole sample land anywhere between 7 and 16. Rows are flat or
#: they are artwork, with almost nothing in between, so a small allowance for
#: codec ringing costs nothing and a generous one starts admitting soft
#: gradients -- at 16 a blurred panel measuring 13-22 was chosen as a seam.
FLAT_SPREAD = 6

#: Share of flat rows a level must hold to count as one of the chapter's
#: gutter colours. Low, because a scene that occupies a fifth of a chapter is
#: entirely normal and its gutters are as real as the majority's.
MIN_GUTTER_SHARE = 0.05

#: Stop after this many. A strip with more distinct flat levels than this is
#: not letterboxed, it is flat artwork, and filtering on colour is not telling
#: us anything.
MAX_GUTTER_LEVELS = 4

#: How far a band's own level may sit from one of the chapter's gutter colours.
#: A flat band matching none of them is a flat piece of *artwork* -- a tinted
#: caption box, a night sky -- where cutting splits something rather than
#: nothing. It also absorbs the codec: the same white gutter was measured at
#: both 255 and 254 in one JPEG chapter.
GUTTER_TOLERANCE = 24

#: A gutter is thick. A flat row or two inside a large pale shape is not, so
#: requiring a band this tall is what separates a real seam from a coincidence.
MIN_BAND = 6

#: Band thickness past this point says nothing more about safety, so proximity
#: to the target height decides instead of running to the middle of a huge gap.
USEFUL_BAND = 48

#: Page geometry, in pixels of the source strip.
TARGET_HEIGHT = 1800
MIN_HEIGHT = 700
MAX_HEIGHT = 2600

#: Below this a chapter is already paged and re-cutting it would be churn.
SLICE_ABOVE = MAX_HEIGHT


class StripError(RuntimeError):
    """Raised when a strip cannot be read or re-cut."""


@dataclass(frozen=True)
class Seam:
    """Where a cut lands, and how much room it had."""

    y: int
    #: Height of the flat band the cut sits in. Zero means nothing qualified
    #: within range and the cut was forced.
    band: int

    @property
    def safe(self) -> bool:
        return self.band >= MIN_BAND


@dataclass
class SliceReport:
    width: int = 0
    total_height: int = 0
    #: ``(top, bottom)`` row ranges in the concatenated strip, in order.
    spans: list[tuple[int, int]] = field(default_factory=list)
    seams: list[Seam] = field(default_factory=list)
    #: Gutter colours measured from the strip. A chapter routinely has more
    #: than one -- black for a night scene, white elsewhere. Empty means there
    #: was too little flat material to judge, so colour was not filtered on.
    gutters: list[float] = field(default_factory=list)
    resized: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def forced(self) -> int:
        return sum(1 for s in self.seams if not s.safe)

    @property
    def page_count(self) -> int:
        return len(self.spans)

    def note(self, message: str) -> None:
        self.notes.append(message)


# --------------------------------------------------------------- measurement


def _imread(path: Path) -> np.ndarray:
    """Decode an image from a path that may contain non-ASCII characters.

    ``cv2.imread`` goes through the C locale and returns None for those, which
    is exactly the shape of failure that looks like a missing file.
    """
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise StripError(f"could not decode {path.name}")
    return image


def _imwrite(image: np.ndarray, path: Path) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise StripError(f"could not encode {path.name}")
    path.write_bytes(buffer.tobytes())


def row_spread(image: np.ndarray) -> np.ndarray:
    """Peak-to-peak value of every row, taken over the widest channel.

    One number per row is all seam-finding needs, which is what keeps a 60000px
    strip out of memory.
    """
    if image.ndim == 2:
        image = image[:, :, None]
    lo = image.min(axis=1)
    hi = image.max(axis=1)
    return (hi.astype(np.int16) - lo.astype(np.int16)).max(axis=1)


def row_level(image: np.ndarray) -> np.ndarray:
    """Mean value of every row: what colour it is, once it is known to be flat."""
    return image.reshape(image.shape[0], -1).mean(axis=1)


def gutter_levels(spread: np.ndarray, level: np.ndarray) -> list[float]:
    """Every colour this chapter's gutters come in, measured from the strip.

    **A chapter has more than one.** Taking only the most common level was a
    real defect, found on a real chapter: it letterboxed on black for its
    opening scene and on white thereafter, and the 71000 white rows outvoted
    the 4911 black ones, so every black gutter was discarded as "flat artwork"
    and 20 of 53 seams fell back to a blind cut at the target height. Measured
    there, the flat rows sat at three levels -- 255 (39%), 254 (34%, the same
    white one JPEG shifted by one) and 0 (24%).

    So peaks are taken greedily, each claiming its neighbourhood, and every
    level holding a real share of the flat rows is accepted. Returns an empty
    list when there is too little flat material to judge, which means "do not
    filter on colour at all" rather than "reject everything".
    """
    flat = spread <= FLAT_SPREAD
    if int(flat.sum()) < MIN_BAND * 4:
        return []

    counts = np.bincount(
        np.clip(level[flat], 0, 255).astype(np.uint8), minlength=256
    ).astype(float)
    total = counts.sum()
    peaks: list[float] = []
    while len(peaks) < MAX_GUTTER_LEVELS:
        top = int(counts.argmax())
        low = max(0, top - GUTTER_TOLERANCE)
        high = min(256, top + GUTTER_TOLERANCE + 1)
        if counts[low:high].sum() < total * MIN_GUTTER_SHARE:
            break
        peaks.append(float(top))
        counts[low:high] = 0.0
    return peaks


def _near_a_gutter(value: float, peaks: list[float]) -> bool:
    """Whether a band's own colour matches any of the chapter's gutters."""
    return not peaks or any(abs(value - p) <= GUTTER_TOLERANCE for p in peaks)


def flat_bands(spread: np.ndarray, threshold: int = FLAT_SPREAD) -> list[tuple[int, int]]:
    """Maximal runs of flat rows, as ``(start, stop)`` half-open ranges."""
    flat = spread <= threshold
    if not flat.any():
        return []
    # Edges of the runs, found by differencing the padded boolean signal.
    padded = np.concatenate(([False], flat, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def plan_cuts(
    spread: np.ndarray,
    level: np.ndarray | None = None,
    target: int = TARGET_HEIGHT,
    min_height: int = MIN_HEIGHT,
    max_height: int = MAX_HEIGHT,
) -> tuple[list[tuple[int, int]], list[Seam]]:
    """Choose page boundaries down a strip.

    Walks from the top, and for each page picks the flat band closest to the
    target height, preferring thick bands. Two things are traded off and both
    matter: a thin band may not be a gutter at all, and a cut far from the
    target makes a page that reads as the wrong length.

    A page with no qualifying band is cut anyway -- at the quietest row in
    range, not at the target -- and the seam records ``band=0`` so the caller
    can report it. Refusing to cut would leave a 60000px "page" that no later
    stage can handle; cutting silently would hide that a bubble may have been
    split. Measured on a real chapter, cutting at the target put every one of
    8 forced seams through maximum-contrast content (median spread 255);
    choosing the quietest row instead brought the median to 29.
    """
    total = int(spread.shape[0])
    if total <= 0:
        return [], []

    bands = [b for b in flat_bands(spread) if b[1] - b[0] >= MIN_BAND]
    if level is not None and bands:
        peaks = gutter_levels(spread, level)
        bands = [
            b for b in bands
            if _near_a_gutter(float(level[b[0] : b[1]].mean()), peaks)
        ]

    spans: list[tuple[int, int]] = []
    seams: list[Seam] = []
    y = 0

    while total - y > max_height:
        ideal = y + target
        lo = y + min_height
        hi = min(y + max_height, total - min_height)
        reach = max(ideal - lo, hi - ideal, 1)

        best: tuple[float, int, int] | None = None
        for start, stop in bands:
            cut = min(max((start + stop) // 2, lo), hi)
            if not lo <= cut <= hi:
                continue
            if stop <= lo or start >= hi:
                continue
            thickness = min(stop - start, USEFUL_BAND) / USEFUL_BAND
            nearness = 1.0 - abs(cut - ideal) / reach
            score = thickness + nearness
            if best is None or score > best[0]:
                best = (score, cut, stop - start)

        if best is None:
            # No gutter anywhere in range: a long full-bleed stretch, which
            # webtoons genuinely have. Cut at the *quietest* row rather than
            # blindly at the target -- the row that varies least across its
            # width is the one least likely to run through lettering, and it
            # costs one argmin over a window we have already measured.
            cut, band = lo + int(spread[lo : hi + 1].argmin()), 0
        else:
            _, cut, band = best

        spans.append((y, cut))
        seams.append(Seam(y=cut, band=band))
        y = cut

    spans.append((y, total))
    return spans, seams


# ------------------------------------------------------------------ emitting


class _StripIndex:
    """Maps a row of the concatenated strip back to the file that holds it.

    Pages are emitted in order and slices are stacked in order, so a page needs
    at most the slice it starts in and the ones after it. One decoded slice is
    cached, which is enough to stop a boundary page decoding its predecessor a
    second time.
    """

    def __init__(self, sources: list[Path], width: int) -> None:
        self.width = width
        self._sources = sources
        self._offsets: list[int] = []
        self._heights: list[int] = []
        self._cached: tuple[int, np.ndarray] | None = None

    @property
    def total(self) -> int:
        if not self._offsets:
            return 0
        return self._offsets[-1] + self._heights[-1]

    def add(self, height: int) -> None:
        self._offsets.append(self.total)
        self._heights.append(height)

    def _slice(self, index: int) -> np.ndarray:
        import cv2

        if self._cached and self._cached[0] == index:
            return self._cached[1]
        image = _imread(self._sources[index])
        if image.shape[1] != self.width:
            height = max(1, round(image.shape[0] * self.width / image.shape[1]))
            image = cv2.resize(image, (self.width, height), interpolation=cv2.INTER_AREA)
        self._cached = (index, image)
        return image

    def rows(self, top: int, bottom: int) -> np.ndarray:
        """The rows ``[top, bottom)`` of the strip, assembled as one image."""
        parts: list[np.ndarray] = []
        for index, offset in enumerate(self._offsets):
            end = offset + self._heights[index]
            if end <= top:
                continue
            if offset >= bottom:
                break
            image = self._slice(index)
            a = max(top, offset) - offset
            b = min(bottom, end) - offset
            parts.append(image[a:b])
        if not parts:
            raise StripError(f"no source rows for {top}-{bottom}")
        return parts[0] if len(parts) == 1 else np.vstack(parts)


def needs_slicing(sources: list[Path], above: int = SLICE_ABOVE) -> bool:
    """Whether this chapter is a strip rather than a set of pages.

    Height alone does not answer this, and testing it alone was wrong: the
    usual webtoon export is a *fixed* step -- 1500px, say -- which is shorter
    than a page and lands wherever it lands, so every file passes a height test
    while half the joins run through artwork. That is precisely the case
    slicing exists for.

    So ask what the existing joins look like. A file that ends where a page
    ends, ends in gutter; one cut by an export step ends mid-panel. If most
    joins are already flat on both sides the files are pages and re-cutting
    them would only move the seams around.
    """
    from PIL import Image

    for path in sources:
        try:
            with Image.open(path) as handle:
                if handle.height > above:
                    return True
        except OSError:
            continue

    if len(sources) < 2:
        return False

    clean = 0
    joins = 0
    for above_path, below_path in zip(sources, sources[1:]):
        try:
            tail = row_spread(_imread(above_path)[-2:])
            head = row_spread(_imread(below_path)[:2])
        except StripError:
            continue
        joins += 1
        if tail.max() <= FLAT_SPREAD and head.max() <= FLAT_SPREAD:
            clean += 1
    return joins > 0 and clean * 2 < joins


def slice_strip(
    sources: list[Path],
    destination: Path,
    target: int = TARGET_HEIGHT,
    min_height: int = MIN_HEIGHT,
    max_height: int = MAX_HEIGHT,
    progress=None,
) -> SliceReport:
    """Concatenate ``sources`` into one strip and re-cut it into ``destination``.

    Returns without writing anything if the strip cannot be measured, so a
    failure here never leaves a half-cut chapter behind.
    """
    from comic.paths import page_filename

    report = SliceReport()
    if not sources:
        report.note("no source pages to slice")
        return report

    widths: list[int] = []
    from PIL import Image

    for path in sources:
        with Image.open(path) as handle:
            widths.append(handle.width)
    # The modal width, not the maximum: one oversized banner that slipped the
    # importer's filters must not rescale the whole chapter around itself.
    report.width = max(set(widths), key=widths.count)

    index = _StripIndex(sources, report.width)
    spreads: list[np.ndarray] = []
    levels: list[np.ndarray] = []
    for path in sources:
        import cv2

        image = _imread(path)
        if image.shape[1] != report.width:
            height = max(1, round(image.shape[0] * report.width / image.shape[1]))
            image = cv2.resize(
                image, (report.width, height), interpolation=cv2.INTER_AREA
            )
            report.resized += 1
        index.add(image.shape[0])
        spreads.append(row_spread(image))
        levels.append(row_level(image))

    spread = np.concatenate(spreads)
    level = np.concatenate(levels)
    report.total_height = int(spread.shape[0])
    report.gutters = gutter_levels(spread, level)
    report.spans, report.seams = plan_cuts(
        spread, level, target, min_height, max_height
    )

    destination.mkdir(parents=True, exist_ok=True)
    for old in destination.glob("*.png"):
        old.unlink()

    for number, (top, bottom) in enumerate(report.spans, start=1):
        _imwrite(index.rows(top, bottom), destination / page_filename(number))
        if progress:
            progress(number, bottom - top)

    if report.forced:
        report.note(
            f"{report.forced} of {len(report.seams)} seam(s) found no gutter in "
            f"range and were cut at the quietest row instead -- check those joins"
        )
    if report.resized:
        report.note(f"{report.resized} slice(s) rescaled to {report.width}px wide")
    return report
