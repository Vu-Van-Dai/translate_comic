"""Naming and layout rules (spec 8, 9, 10)."""

from __future__ import annotations

import pytest

from comic.paths import (
    ChapterPaths,
    chapter_label,
    chapter_sort_key,
    page_filename,
    safe_name,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, "Chapter_001"),
        ("1", "Chapter_001"),
        ("007", "Chapter_007"),
        ("10", "Chapter_010"),
        ("1.5", "Chapter_001.5"),
        ("Chapter 46", "Chapter_046"),
        ("chapter-69", "Chapter_069"),
        ("chapter_3", "Chapter_003"),
        ("Extra", "Extra"),
        ("Special", "Special"),
    ],
)
def test_chapter_label(raw, expected):
    assert chapter_label(raw) == expected


def test_chapter_label_rejects_empty():
    with pytest.raises(ValueError):
        chapter_label("   ")


def test_chapters_sort_numerically_with_extras_last():
    labels = ["Extra", "Chapter_010", "Chapter_002", "Chapter_001.5", "Chapter_001"]
    assert sorted(labels, key=chapter_sort_key) == [
        "Chapter_001", "Chapter_001.5", "Chapter_002", "Chapter_010", "Extra",
    ]


@pytest.mark.parametrize(
    "page,expected",
    [(1, "001.png"), (9, "009.png"), (10, "010.png"), (37, "037.png"), (100, "100.png")],
)
def test_page_filename_is_zero_padded(page, expected):
    """File explorers must sort pages correctly without a natural-sort mode."""
    assert page_filename(page) == expected


def test_page_filename_normalises_extension():
    assert page_filename(3, "WEBP") == "003.webp"
    assert page_filename(3, ".JPG") == "003.jpg"


def test_page_filename_is_one_based():
    with pytest.raises(ValueError):
        page_filename(0)


def test_padded_names_sort_lexicographically():
    names = [page_filename(n) for n in (1, 2, 9, 10, 11, 37)]
    assert names == sorted(names)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Paws & Claws", "Paws & Claws"),
        ("A: B/C?", "A B C"),
        ("  spaced   out  ", "spaced out"),
        ("trailing.", "trailing"),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_does_not_fuse_words_across_a_slash():
    """Deleting the separator would give 'Fatestay night'."""
    assert safe_name("Fate/stay night") == "Fate stay night"


def test_safe_name_escapes_windows_reserved_words():
    assert safe_name("CON") == "CON_"
    assert safe_name("nul") == "nul_"


@pytest.mark.parametrize("raw", ["", "   ", "???", "///"])
def test_safe_name_rejects_unusable(raw):
    with pytest.raises(ValueError):
        safe_name(raw)


def test_library_and_workspace_are_separate_trees(chapter: ChapterPaths):
    """Only finished pages may land in library/ (spec 10)."""
    assert "workspace" in chapter.workspace.parts
    assert "library" in chapter.library.parts
    assert chapter.library not in chapter.workspace.parents


def test_ensure_creates_workspace_but_not_library(chapter: ChapterPaths):
    for path in (chapter.original, chapter.analysis, chapter.masks,
                 chapter.cleaned, chapter.translated, chapter.handoff, chapter.logs):
        assert path.is_dir()
    assert not chapter.library.exists()
