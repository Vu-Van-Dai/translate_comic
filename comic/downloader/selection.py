"""Deciding which images are comic pages, and in what order.

Pure functions over harvested layout -- no browser, no disk -- so the tricky
heuristics are testable against fixtures.

The problem: a reader page contains the comic plus a logo, avatars, ad slots and
a grid of "related series" thumbnails. Selectors alone do not generalise across
sites, so selection leans on three layout facts that hold for comic readers:

1. pages are large, and far wider than chrome;
2. pages sit in one centred column, while thumbnail grids scatter across x;
3. pages are contiguous in y, while recommendation blocks sit after a big gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from comic.browser.session import DomImage
from comic.downloader.adapters import SiteAdapter
from comic.schemas import PageKind, ReadingDirection

#: Anything narrower than this fraction of the widest page is chrome.
WIDTH_FLOOR_RATIO = 0.4

#: Comic pages are never this extreme; guards against banners and spacers.
MAX_ASPECT_RATIO = 30.0
MIN_AREA = 40_000

#: How far an image's centre may sit from the column centre, as a fraction of
#: the column width, before it is treated as part of a grid rather than the strip.
COLUMN_TOLERANCE = 0.35

#: A vertical gap larger than this many median page heights ends the comic run.
GAP_FACTOR = 3.0

#: How far an image's width:height may stray from the page norm before it is
#: treated as a banner. Relative rather than absolute on purpose: some webtoon
#: sites slice a strip into short, wide pieces, so a fixed cap would reject the
#: whole chapter. An outlier only stands out against its neighbours.
ASPECT_OUTLIER_FACTOR = 3.0

#: Below this many candidates there is no reliable norm to compare against.
MIN_SAMPLE_FOR_ASPECT = 4


@dataclass(frozen=True)
class SelectionResult:
    pages: tuple[DomImage, ...]
    rejected: tuple[tuple[DomImage, str], ...]

    @property
    def count(self) -> int:
        return len(self.pages)

    def reasons(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for _, reason in self.rejected:
            tally[reason] = tally.get(reason, 0) + 1
        return tally


def _vertical_overlap(a: DomImage, b: DomImage) -> float:
    """Fraction of the shorter image's height that overlaps the other."""
    top, bottom = max(a.y, b.y), min(a.y + a.height, b.y + b.height)
    overlap = max(0.0, bottom - top)
    shorter = min(a.height, b.height)
    return overlap / shorter if shorter > 0 else 0.0


def order_images(
    images: list[DomImage], direction: ReadingDirection
) -> list[DomImage]:
    """Sort into reading order (spec 13).

    Images that overlap vertically form a row and are ordered within it; rows run
    top to bottom. For a long strip every image is its own row, so this collapses
    to pure y order. For a right-to-left manga reader laying two pages side by
    side, the rightmost page in each row comes first.
    """
    rows: list[list[DomImage]] = []
    for image in sorted(images, key=lambda i: (i.y, i.x)):
        for row in rows:
            if _vertical_overlap(image, row[-1]) >= 0.5:
                row.append(image)
                break
        else:
            rows.append([image])

    ordered: list[DomImage] = []
    for row in rows:
        row.sort(key=lambda i: i.x, reverse=direction is ReadingDirection.RTL)
        ordered.extend(row)
    return ordered


def _dedupe(images: list[DomImage]) -> list[DomImage]:
    """Keep the first occurrence of each URL, preserving order."""
    seen: set[str] = set()
    out = []
    for image in images:
        if image.url in seen:
            continue
        seen.add(image.url)
        out.append(image)
    return out


def _largest_contiguous_run(images: list[DomImage]) -> list[DomImage]:
    """Longest run of images with no oversized vertical gap between them.

    Recommendation grids and comment sections sit below the comic after a large
    gap; this drops them without knowing anything about the site.
    """
    if len(images) < 3:
        return images

    heights = [i.height for i in images if i.height > 0]
    if not heights:
        return images
    threshold = median(heights) * GAP_FACTOR

    runs: list[list[DomImage]] = [[images[0]]]
    for previous, current in zip(images, images[1:]):
        gap = current.y - (previous.y + previous.height)
        if gap > threshold:
            runs.append([current])
        else:
            runs[-1].append(current)

    # Prefer the run holding the most pixels, not merely the most images -- a
    # 30-thumbnail grid must not outvote 12 full pages.
    return max(runs, key=lambda run: sum(i.natural_area for i in run))


def _aspect_outliers(images: list[DomImage]) -> tuple[list[DomImage], list[DomImage]]:
    """Split off images whose shape does not match the rest of the page.

    A leaderboard ad sits flush against the comic at a plausible width, so gap
    and column rules both let it through. Its shape gives it away: pages in a
    given chapter share an aspect ratio, and the ad does not.

    Returns ``(kept, outliers)``.
    """
    if len(images) < MIN_SAMPLE_FOR_ASPECT:
        return images, []

    aspects = [i.natural_width / i.natural_height for i in images]
    norm = median(aspects)
    if norm <= 0:
        return images, []

    high, low = norm * ASPECT_OUTLIER_FACTOR, norm / ASPECT_OUTLIER_FACTOR
    kept, outliers = [], []
    for image, aspect in zip(images, aspects):
        (outliers if aspect > high or aspect < low else kept).append(image)

    # If the rule would discard most of the page, the norm was not meaningful.
    return (kept, outliers) if len(kept) >= len(images) // 2 else (images, [])


def _in_main_column(images: list[DomImage]) -> list[DomImage]:
    """Drop images whose horizontal centre strays from the main column."""
    if len(images) < 3:
        return images
    centres = [i.x + i.width / 2 for i in images]
    widths = [i.width for i in images if i.width > 0]
    if not widths:
        return images
    centre = median(centres)
    tolerance = max(median(widths) * COLUMN_TOLERANCE, 40.0)
    kept = [i for i in images if abs((i.x + i.width / 2) - centre) <= tolerance]
    # If the rule would throw away most of the page, it is wrong for this layout.
    return kept if len(kept) >= max(2, len(images) // 2) else images


def select_pages(
    images: list[DomImage], adapter: SiteAdapter
) -> SelectionResult:
    """Pick the comic pages out of every image on the reader page."""
    rejected: list[tuple[DomImage, str]] = []
    candidates: list[DomImage] = []

    for image in images:
        if image.natural_width <= 0 or image.natural_height <= 0:
            rejected.append((image, "no natural size"))
            continue
        if adapter.is_blocked_url(image.url):
            rejected.append((image, "blocklisted url"))
            continue
        if image.natural_area < MIN_AREA:
            rejected.append((image, "too small"))
            continue
        ratio = max(image.natural_width, image.natural_height) / min(
            image.natural_width, image.natural_height
        )
        if ratio > MAX_ASPECT_RATIO:
            rejected.append((image, "extreme aspect ratio"))
            continue
        candidates.append(image)

    if not candidates:
        return SelectionResult((), tuple(rejected))

    # Width floor is relative to the widest candidate, so it adapts to the
    # site's resolution instead of assuming a fixed pixel size. Double-page
    # spreads are wider than normal pages and survive this by construction.
    widest = max(i.natural_width for i in candidates)
    floor = max(adapter.min_image_width, widest * WIDTH_FLOOR_RATIO)
    wide_enough = []
    for image in candidates:
        if image.natural_width < floor:
            rejected.append((image, "narrower than page column"))
        else:
            wide_enough.append(image)

    if not wide_enough:
        return SelectionResult((), tuple(rejected))

    # The shape test assumes the pages of a chapter share an aspect ratio.
    # That holds for a paged source and is false for a long strip, where slice
    # height is an artifact of the uploader's export: measured on this series,
    # one chapter mixes body slices of 12156-12781px with a 693px intro page,
    # all 700px wide. The intro is then 18x off the median and is discarded as
    # an ad -- it silently cost two pages per chapter on four chapters.
    #
    # Width already separates pages from furniture here (every real page sits
    # at the column width, the junk at 200x75 and 75x105), and
    # MAX_ASPECT_RATIO still rejects anything truly absurd, so a long strip
    # simply keeps what cleared the width floor.
    if adapter.page_kind is PageKind.LONGSTRIP:
        shaped, odd_shapes = _dedupe(wide_enough), []
    else:
        shaped, odd_shapes = _aspect_outliers(_dedupe(wide_enough))
    rejected.extend((image, "shape unlike the other pages") for image in odd_shapes)
    if not shaped:
        return SelectionResult((), tuple(rejected))

    ordered = order_images(shaped, adapter.reading_direction)
    columnar = _in_main_column(ordered)
    for image in ordered:
        if image not in columnar:
            rejected.append((image, "outside main column"))

    run = _largest_contiguous_run(columnar)
    for image in columnar:
        if image not in run:
            rejected.append((image, "separated by a large gap"))

    return SelectionResult(tuple(run), tuple(rejected))
