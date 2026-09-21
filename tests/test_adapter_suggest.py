"""The starter adapter written for a host that did not have one.

The generic defaults are paged, top-to-bottom and English. For a webtoon that
is wrong in a way nothing downstream can detect -- the strip is never re-cut
and OCR is handed the wrong engine -- so the import leaves a file saying what
the images actually looked like.
"""

from __future__ import annotations

import pytest

from comic.downloader.adapters import (
    adapter_for,
    load_adapters,
    suggest_adapter,
)
from comic.schemas import PageKind

Image = pytest.importorskip("PIL.Image")


def write_images(directory, count, width, height):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in range(1, count + 1):
        path = directory / f"{n:03d}.png"
        Image.new("RGB", (width, height), "white").save(path)
        paths.append(path)
    return paths


@pytest.fixture
def sites(tmp_path):
    directory = tmp_path / "sites"
    directory.mkdir()
    load_adapters.cache_clear()
    yield directory
    load_adapters.cache_clear()


def test_tall_images_are_called_a_longstrip(tmp_path, sites):
    pages = write_images(tmp_path / "pages", 5, 800, 4000)
    written = suggest_adapter("https://new-host.example/read/x/ch-1", pages, sites)
    assert written is not None
    assert "page_kind: longstrip" in written.read_text(encoding="utf-8")


def test_page_shaped_images_stay_paged(tmp_path, sites):
    pages = write_images(tmp_path / "pages", 5, 900, 1300)
    written = suggest_adapter("https://new-host.example/read/x/ch-1", pages, sites)
    assert "page_kind: paged" in written.read_text(encoding="utf-8")


def test_the_file_loads_back_as_a_working_adapter(tmp_path, sites):
    pages = write_images(tmp_path / "pages", 4, 800, 4000)
    suggest_adapter("https://new-host.example/read/x/ch-1", pages, sites)
    adapter = adapter_for("https://new-host.example/read/x/ch-1", sites)
    assert adapter.match == "new-host.example"
    assert adapter.page_kind is PageKind.LONGSTRIP


def test_a_host_that_already_has_an_adapter_is_left_alone(tmp_path, sites):
    (sites / "known.example.yaml").write_text(
        'match: "known.example"\npage_kind: paged\n', encoding="utf-8"
    )
    load_adapters.cache_clear()
    pages = write_images(tmp_path / "pages", 3, 800, 4000)
    assert suggest_adapter("https://known.example/x", pages, sites) is None


def test_www_is_not_treated_as_a_different_host(tmp_path, sites):
    pages = write_images(tmp_path / "pages", 3, 800, 4000)
    written = suggest_adapter("https://www.new-host.example/x", pages, sites)
    assert written.stem == "new-host.example"


def test_the_file_says_to_check_the_language(tmp_path, sites):
    """It cannot be measured from geometry, so it must not look decided."""
    pages = write_images(tmp_path / "pages", 3, 800, 4000)
    written = suggest_adapter("https://new-host.example/x", pages, sites)
    assert "CHECK source_language" in written.read_text(encoding="utf-8")


def test_unreadable_images_do_not_raise(tmp_path, sites):
    directory = tmp_path / "pages"
    directory.mkdir()
    broken = directory / "001.png"
    broken.write_bytes(b"not an image")
    written = suggest_adapter("https://new-host.example/x", [broken], sites)
    assert written is not None
