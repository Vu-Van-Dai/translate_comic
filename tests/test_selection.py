"""Page selection and reading order (spec 11, 13).

Synthetic layouts stand in for real reader pages: the heuristics operate purely on
geometry, so a fixture that reproduces the geometry reproduces the decision.
"""

from __future__ import annotations

import pytest

from comic.browser.session import DomImage
from comic.downloader.adapters import GENERIC, SiteAdapter
from comic.downloader.selection import order_images, select_pages
from comic.schemas import ReadingDirection


def img(url: str, *, w: int, h: int, x: float = 0, y: float = 0,
        dw: float | None = None, dh: float | None = None) -> DomImage:
    """A DOM image; display size defaults to natural size."""
    return DomImage(
        url=url, natural_width=w, natural_height=h,
        x=x, y=y, width=dw if dw is not None else w, height=dh if dh is not None else h,
    )


def longstrip_page(count: int = 12, width: int = 800, height: int = 1400,
                   left: float = 320) -> list[DomImage]:
    """A webtoon reader: one centred column of tall slices."""
    return [
        img(f"https://cdn.test/p{i}.webp", w=width, h=height, x=left, y=i * height)
        for i in range(count)
    ]


def test_clean_longstrip_keeps_every_page_in_order():
    pages = longstrip_page()
    result = select_pages(pages, GENERIC)
    assert result.count == 12
    assert [p.url for p in result.pages] == [p.url for p in pages]


def test_chrome_and_thumbnails_are_rejected():
    """The realistic case: comic column plus logo, avatar and a related grid."""
    pages = longstrip_page(count=10)
    bottom = 10 * 1400
    noise = [
        img("https://cdn.test/logo.png", w=180, h=60, x=20, y=0),
        img("https://cdn.test/avatar.png", w=64, h=64, x=1200, y=10),
        img("https://cdn.test/banner-ad.jpg", w=970, h=90, x=250, y=bottom + 40),
    ]
    # A 4-wide recommendation grid well below the comic.
    grid = [
        img(f"https://cdn.test/thumb{i}.jpg", w=300, h=420,
            x=100 + (i % 4) * 320, y=bottom + 600 + (i // 4) * 440)
        for i in range(8)
    ]
    result = select_pages(pages + noise + grid, GENERIC)

    assert result.count == 10
    urls = {p.url for p in result.pages}
    assert all("thumb" not in u and "logo" not in u and "ad" not in u for u in urls)


def test_a_large_thumbnail_grid_cannot_outvote_the_comic():
    """30 thumbnails have more images than 8 pages, but far fewer pixels."""
    pages = longstrip_page(count=8, width=900, height=1300)
    bottom = 8 * 1300
    grid = [
        img(f"https://cdn.test/t{i}.jpg", w=280, h=390,
            x=100 + (i % 5) * 300, y=bottom + 900 + (i // 5) * 410)
        for i in range(30)
    ]
    result = select_pages(pages + grid, GENERIC)
    assert result.count == 8
    assert all("/p" in p.url for p in result.pages)


def test_double_page_spread_survives_the_width_filter():
    """A spread is wider than a normal page and must not be mistaken for chrome."""
    pages = [
        img("https://cdn.test/p0.jpg", w=800, h=1200, x=300, y=0),
        img("https://cdn.test/spread.jpg", w=1600, h=1200, x=0, y=1200),
        img("https://cdn.test/p2.jpg", w=800, h=1200, x=300, y=2400),
    ]
    result = select_pages(pages, GENERIC)
    assert result.count == 3
    assert any("spread" in p.url for p in result.pages)


def test_leaderboard_ad_flush_against_the_comic_is_rejected():
    """Sits at a plausible width with no gap, so only its shape gives it away."""
    pages = longstrip_page(count=10)
    ad = img("https://cdn.test/promo.jpg", w=970, h=90, x=250, y=10 * 1400 + 20)
    result = select_pages(pages + [ad], GENERIC)
    assert result.count == 10
    assert "shape unlike the other pages" in result.reasons()


def test_uniformly_wide_slices_are_not_mistaken_for_ads():
    """Some sites cut a strip into short wide pieces; an absolute aspect cap
    would reject the entire chapter, so the rule must be relative."""
    slices = [
        img(f"https://cdn.test/s{i}.jpg", w=800, h=100, x=320, y=i * 100)
        for i in range(20)
    ]
    result = select_pages(slices, GENERIC)
    assert result.count == 20


def test_blocklisted_urls_are_dropped():
    adapter = SiteAdapter(match="x", url_blocklist=("/banner", "/logo"))
    pages = longstrip_page(count=5)
    noise = [img("https://cdn.test/banner/huge.jpg", w=900, h=1400, x=320, y=1400 * 5)]
    result = select_pages(pages + noise, adapter)
    assert result.count == 5
    assert "blocklisted url" in result.reasons()


def test_duplicate_urls_collapse():
    pages = longstrip_page(count=4)
    result = select_pages(pages + [pages[1]], GENERIC)
    assert result.count == 4


def test_tracking_pixels_and_spacers_rejected():
    pages = longstrip_page(count=4)
    noise = [
        img("https://cdn.test/px.gif", w=1, h=1, x=0, y=0),
        img("https://cdn.test/spacer.png", w=2000, h=3, x=0, y=10),
    ]
    result = select_pages(pages + noise, GENERIC)
    assert result.count == 4
    reasons = result.reasons()
    assert reasons.get("too small") or reasons.get("extreme aspect ratio")


def test_empty_input_yields_nothing():
    assert select_pages([], GENERIC).count == 0


# ------------------------------------------------------------------- ordering


def test_ttb_order_is_pure_vertical():
    scrambled = [
        img("c", w=800, h=100, y=200), img("a", w=800, h=100, y=0),
        img("b", w=800, h=100, y=100),
    ]
    assert [i.url for i in order_images(scrambled, ReadingDirection.TTB)] == ["a", "b", "c"]


def test_rtl_reads_rightmost_first_within_a_row():
    """Japanese manga: the right-hand page of a spread comes first (spec 13)."""
    row = [
        img("left", w=800, h=1200, x=0, y=0),
        img("right", w=800, h=1200, x=800, y=0),
    ]
    assert [i.url for i in order_images(row, ReadingDirection.RTL)] == ["right", "left"]


def test_ltr_reads_leftmost_first_within_a_row():
    row = [
        img("left", w=800, h=1200, x=0, y=0),
        img("right", w=800, h=1200, x=800, y=0),
    ]
    assert [i.url for i in order_images(row, ReadingDirection.LTR)] == ["left", "right"]


def test_rows_run_top_to_bottom_regardless_of_direction():
    images = [
        img("r2-left", w=800, h=1200, x=0, y=1200),
        img("r2-right", w=800, h=1200, x=800, y=1200),
        img("r1-left", w=800, h=1200, x=0, y=0),
        img("r1-right", w=800, h=1200, x=800, y=0),
    ]
    assert [i.url for i in order_images(images, ReadingDirection.RTL)] == [
        "r1-right", "r1-left", "r2-right", "r2-left",
    ]


def test_slightly_misaligned_images_still_count_as_one_row():
    """Real layouts are never pixel-perfect."""
    row = [
        img("left", w=800, h=1200, x=0, y=0),
        img("right", w=800, h=1190, x=800, y=6),
    ]
    assert [i.url for i in order_images(row, ReadingDirection.RTL)] == ["right", "left"]


@pytest.mark.parametrize("direction", list(ReadingDirection))
def test_ordering_never_drops_or_duplicates(direction):
    images = longstrip_page(count=9)
    ordered = order_images(images, direction)
    assert sorted(i.url for i in ordered) == sorted(i.url for i in images)
