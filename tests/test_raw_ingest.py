"""Manual page ingest -- the escape hatch for hosts we do not read automatically."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from comic.downloader.raw_ingest import (
    OrderBy,
    RawIngestError,
    ingest,
    natural_key,
    scan,
)
from comic.paths import ChapterPaths


def write_image(path: Path, w: int = 800, h: int = 1200, tint: int = 0,
                fmt: str = "JPEG") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (tint, 90, 140)).save(path, format=fmt)


@pytest.fixture
def raw_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "raw"
    for n in (1, 2, 9, 10, 11):
        write_image(folder / f"page{n}.jpg", tint=n * 20)
    return folder


def test_natural_key_orders_numbers_numerically():
    names = ["page10.jpg", "page2.jpg", "page1.jpg", "page11.jpg", "page9.jpg"]
    assert sorted(names, key=natural_key) == [
        "page1.jpg", "page2.jpg", "page9.jpg", "page10.jpg", "page11.jpg",
    ]
    # Plain lexicographic sorting is what silently scrambles a chapter.
    assert sorted(names) != sorted(names, key=natural_key)


def test_scan_orders_naturally(raw_folder: Path):
    plan = scan(raw_folder, OrderBy.NAME)
    assert [p.name for p, _ in plan.mapping()] == [
        "page1.jpg", "page2.jpg", "page9.jpg", "page10.jpg", "page11.jpg",
    ]
    assert [dest for _, dest in plan.mapping()] == [
        "001.jpg", "002.jpg", "003.jpg", "004.jpg", "005.jpg",
    ]


def test_non_images_and_tiny_files_are_rejected(raw_folder: Path):
    write_image(raw_folder / "icon.png", w=50, h=50, fmt="PNG")
    (raw_folder / "notes.txt").write_text("not an image")
    (raw_folder / "broken.jpg").write_bytes(b"\xff\xd8 truncated garbage")

    plan = scan(raw_folder)
    assert plan.count == 5
    reasons = {p.name: why for p, why in plan.rejected}
    assert "not an image file" in reasons["notes.txt"]
    assert "too small" in reasons["icon.png"]
    assert "does not decode" in reasons["broken.jpg"]


def test_ingest_renames_and_records(raw_folder: Path, chapter: ChapterPaths):
    records = ingest(scan(raw_folder), chapter)
    assert [r.filename for r in records] == [
        "001.jpg", "002.jpg", "003.jpg", "004.jpg", "005.jpg",
    ]
    assert all((chapter.original / r.filename).exists() for r in records)
    assert all(r.width == 800 and r.height == 1200 for r in records)
    assert all(r.sha256 for r in records)


def test_ingest_is_byte_preserving(raw_folder: Path, chapter: ChapterPaths):
    """The workspace copy archives exactly what the source gave us."""
    records = ingest(scan(raw_folder), chapter)
    original = (raw_folder / "page1.jpg").read_bytes()
    assert (chapter.original / records[0].filename).read_bytes() == original


def test_normalize_restores_a_dimmed_capture(tmp_path: Path, chapter: ChapterPaths):
    """A browser-dimmed page never reaches white, which costs OCR its contrast."""
    from comic.downloader.raw_ingest import normalize_levels, peak_luminance

    folder = tmp_path / "dim"
    folder.mkdir()
    # A page whose brightest pixel is 153, as measured on real dimmed captures.
    arr = np.zeros((400, 400, 3), dtype=np.uint8)
    arr[:200] = 153
    arr[200:] = 40
    Image.fromarray(arr).save(folder / "001.png")

    data = (folder / "001.png").read_bytes()
    assert peak_luminance(data) == 153
    fixed = normalize_levels(data)
    assert fixed is not None and peak_luminance(fixed) == 255

    # Opting in rewrites; the default still archives verbatim.
    assert ingest(scan(folder), chapter, normalize=True)
    assert (chapter.original / "001.png").read_bytes() != data


def test_normalize_leaves_a_healthy_page_alone(raw_folder: Path):
    """A page already using the full range must not be touched."""
    from comic.downloader.raw_ingest import normalize_levels

    bright = raw_folder / "bright.png"
    arr = np.zeros((300, 300, 3), dtype=np.uint8)
    arr[:150] = 255
    Image.fromarray(arr).save(bright)
    assert normalize_levels(bright.read_bytes()) is None


def test_duplicate_saves_collapse(raw_folder: Path, chapter: ChapterPaths):
    """Sweeping a reader by hand often saves the same page twice."""
    import shutil
    shutil.copy(raw_folder / "page2.jpg", raw_folder / "page2 (1).jpg")
    plan = scan(raw_folder)
    assert plan.count == 6
    records = ingest(plan, chapter)
    assert len(records) == 5
    assert [r.page for r in records] == [1, 2, 3, 4, 5]


def test_order_by_mtime(raw_folder: Path):
    """For batch saves whose names carry no order at all."""
    import os
    import time
    base = time.time()
    for offset, name in enumerate(["page11.jpg", "page1.jpg", "page9.jpg",
                                   "page2.jpg", "page10.jpg"]):
        os.utime(raw_folder / name, (base + offset, base + offset))
    plan = scan(raw_folder, OrderBy.MODIFIED)
    assert [p.name for p, _ in plan.mapping()][:3] == [
        "page11.jpg", "page1.jpg", "page9.jpg",
    ]


def test_missing_folder_is_explained(tmp_path: Path):
    with pytest.raises(RawIngestError, match="no such folder"):
        scan(tmp_path / "nope")


def test_empty_folder_is_explained(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RawIngestError, match="empty"):
        scan(tmp_path / "empty")


def test_folder_with_no_usable_images_lists_why(tmp_path: Path):
    folder = tmp_path / "junk"
    folder.mkdir()
    (folder / "a.txt").write_text("x")
    with pytest.raises(RawIngestError, match="no usable images"):
        scan(folder)


# ------------------------------------------------------- extension handoff


@pytest.mark.parametrize(
    "label,expected",
    [("Chapter_069", "069"), ("Chapter_001.5", "001.5"), ("69", "69"),
     ("Extra", "Extra"), ("Special", "Special")],
)
def test_chapter_number_strips_only_the_prefix(label, expected):
    """lstrip('Chapter_') would eat letters from 'Extra'; removeprefix does not."""
    from comic.downloader.raw_ingest import chapter_number

    assert chapter_number(label) == expected


def test_find_source_prefers_the_project_raw_tree(home, tmp_path, monkeypatch):
    from comic.downloader.raw_ingest import find_source, raw_dir

    folder = raw_dir("Paws & Claws", "069")
    folder.mkdir(parents=True)
    write_image(folder / "001.jpg")
    assert find_source("Paws & Claws", "Chapter_069") == folder


def test_find_source_returns_none_when_nothing_exists(home):
    from comic.downloader.raw_ingest import find_source

    assert find_source("Nothing Here", "Chapter_001") is None


def test_find_source_ignores_an_empty_folder(home):
    from comic.downloader.raw_ingest import find_source, raw_dir

    raw_dir("Paws & Claws", "069").mkdir(parents=True)
    assert find_source("Paws & Claws", "Chapter_069") is None
