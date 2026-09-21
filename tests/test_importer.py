"""Acquisition: validation, naming, dedup and the locked-chapter refusal.

Uses a synthetic harvest so the whole import path is exercised without a browser.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from comic.browser.session import CapturedImage, DomImage, PageHarvest
from comic.downloader.adapters import GENERIC, SiteAdapter
from comic.downloader.importer import (
    ChapterLockedError,
    ImportError_,
    detect_locked,
    import_pages,
    sniff_image,
    verify_pages,
)
from comic.paths import ChapterPaths


def make_image(width: int = 800, height: int = 1200, fmt: str = "PNG",
               colour: tuple[int, int, int] = (120, 140, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format=fmt)
    return buffer.getvalue()


def make_harvest(count: int = 5, *, html: str = "", fmt: str = "PNG",
                 height: int = 1200) -> PageHarvest:
    """A clean long-strip harvest of ``count`` distinct pages."""
    dom, captured = [], {}
    for i in range(count):
        url = f"https://cdn.test/page{i}.{fmt.lower()}"
        # Vary the colour so pages are not accidental duplicates.
        body = make_image(800, height, fmt, colour=(10 * i, 100, 200))
        dom.append(DomImage(url=url, natural_width=800, natural_height=height,
                            x=320, y=i * height, width=800, height=height))
        captured[url] = CapturedImage(url, body, f"image/{fmt.lower()}")
    return PageHarvest(url="https://site.test/ch1", title="Chapter 1",
                       dom_images=dom, captured=captured, html=html)


# ------------------------------------------------------------------- sniffing


@pytest.mark.parametrize("fmt,ext", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")])
def test_sniff_recognises_real_formats(fmt, ext):
    assert sniff_image(make_image(fmt=fmt)) == (ext, 800, 1200)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n truncated"])
def test_sniff_rejects_non_images(data):
    assert sniff_image(data) is None


def test_sniff_uses_real_format_not_the_url_extension():
    """Sites serve .jpg URLs holding WebP payloads; the bytes are authoritative."""
    assert sniff_image(make_image(fmt="WEBP"))[0] == ".webp"


# --------------------------------------------------------------------- import


def test_pages_are_renamed_to_padded_sequence(chapter: ChapterPaths):
    report = import_pages(make_harvest(12), GENERIC, chapter)
    assert report.count == 12
    names = sorted(p.name for p in chapter.original.iterdir())
    assert names[:3] == ["001.png", "002.png", "003.png"]
    assert names[-1] == "012.png"
    # Source filenames are discarded entirely (spec 9).
    assert not any("page" in n for n in names)


def test_written_files_are_byte_identical_to_what_was_served(chapter: ChapterPaths):
    """Originals are archived verbatim, never re-encoded."""
    harvest = make_harvest(3)
    report = import_pages(harvest, GENERIC, chapter)
    for record in report.pages:
        written = (chapter.original / record.filename).read_bytes()
        assert written == harvest.captured[record.source_url].body


def test_records_carry_dimensions_and_hash(chapter: ChapterPaths):
    report = import_pages(make_harvest(2), GENERIC, chapter)
    for record in report.pages:
        assert record.width == 800 and record.height == 1200
        assert record.sha256 and len(record.sha256) == 64


def test_identical_pages_are_deduplicated(chapter: ChapterPaths):
    harvest = make_harvest(4)
    body = make_image(800, 1200, "PNG", colour=(7, 7, 7))
    for i in (1, 2):
        url = f"https://cdn.test/page{i}.png"
        harvest.captured[url] = CapturedImage(url, body, "image/png")

    report = import_pages(harvest, GENERIC, chapter)
    assert report.count == 3
    assert any("duplicate" in w for w in report.warnings)
    # Numbering stays contiguous after the skip.
    assert [r.page for r in report.pages] == [1, 2, 3]


def test_undecodable_payload_is_skipped_not_fatal(chapter: ChapterPaths):
    harvest = make_harvest(4)
    bad = "https://cdn.test/page2.png"
    harvest.captured[bad] = CapturedImage(bad, b"<html>404</html>" * 600, "image/png")

    report = import_pages(harvest, GENERIC, chapter)
    assert report.count == 3
    assert any("not a decodable image" in s for s in report.skipped)


def test_missing_bytes_are_skipped_when_no_session(chapter: ChapterPaths):
    harvest = make_harvest(4)
    del harvest.captured["https://cdn.test/page1.png"]
    report = import_pages(harvest, GENERIC, chapter)
    assert report.count == 3
    assert any("no bytes available" in s for s in report.skipped)


def test_thumbnail_sized_images_are_rejected(chapter: ChapterPaths):
    harvest = make_harvest(3, height=150)
    with pytest.raises(ImportError_):
        import_pages(harvest, GENERIC, chapter)


def test_no_pages_raises_with_the_rejection_reasons(chapter: ChapterPaths):
    empty = PageHarvest(url="https://site.test/ch1", title="", dom_images=[], captured={})
    with pytest.raises(ImportError_, match="no page images"):
        import_pages(empty, GENERIC, chapter)


def test_verify_pages_detects_a_truncated_file(chapter: ChapterPaths):
    report = import_pages(make_harvest(3), GENERIC, chapter)
    assert verify_pages(chapter, report.pages) == []

    (chapter.original / "002.png").write_bytes(b"corrupted")
    problems = verify_pages(chapter, report.pages)
    assert len(problems) == 1 and "002.png" in problems[0]


# --------------------------------------------------------------- locked chapters

LOCKED_ADAPTER = SiteAdapter(
    match="paid.test", requires_login=True, locked_selector=".manga-pay, .unlock-btn"
)


def test_locked_chapter_is_refused_not_worked_around(chapter: ChapterPaths):
    harvest = make_harvest(3, html='<div class="manga-pay">Unlock this chapter</div>')
    assert detect_locked(harvest, LOCKED_ADAPTER)
    with pytest.raises(ChapterLockedError, match="locked"):
        import_pages(harvest, LOCKED_ADAPTER, chapter)


def test_unlocked_chapter_on_a_paid_site_imports_normally(chapter: ChapterPaths):
    harvest = make_harvest(3, html="<div class='reader'>ok</div>")
    assert not detect_locked(harvest, LOCKED_ADAPTER)
    assert import_pages(harvest, LOCKED_ADAPTER, chapter).count == 3


def test_empty_result_on_a_login_site_blames_the_session(chapter: ChapterPaths):
    """Signed out looks like 'no images'; say so instead of a generic failure."""
    empty = PageHarvest(url="https://paid.test/ch1", title="", dom_images=[], captured={})
    with pytest.raises(ChapterLockedError, match="comic login"):
        import_pages(empty, LOCKED_ADAPTER, chapter)


def test_no_locked_selector_means_never_locked(chapter: ChapterPaths):
    harvest = make_harvest(2, html='<div class="manga-pay">x</div>')
    assert not detect_locked(harvest, GENERIC)
