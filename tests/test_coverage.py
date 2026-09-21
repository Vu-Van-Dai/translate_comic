"""Vietnamese font coverage gating.

Not theoretical: every handwriting face shipped with Windows fails this check, and
Bahnschrift fails by exactly one glyph. A partially-covering font does not error --
it renders tofu mid-sentence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comic.paths import fonts_dir
from comic.typesetting.coverage import (
    VIETNAMESE_VOWELS,
    check_directory,
    check_font,
    required_codepoints,
)

WINDOWS_FONTS = Path("C:/Windows/Fonts")

#: Stacked diacritics that separate real Vietnamese support from "latin-ext".
HARD_GLYPHS = "ếộữỡẳẵỷợừẩẫậ"


def test_required_set_covers_all_tone_marks():
    required = required_codepoints()
    for ch in HARD_GLYPHS:
        assert ch in required, f"{ch} missing from the required repertoire"
    # 12 vowels x 6 tones, upper and lower.
    assert len(VIETNAMESE_VOWELS) == 72
    assert all(c.upper() in required for c in VIETNAMESE_VOWELS)


def test_required_set_is_stable():
    assert required_codepoints() is required_codepoints()


@pytest.mark.skipif(not (WINDOWS_FONTS / "arial.ttf").exists(), reason="needs Windows fonts")
def test_arial_passes():
    result = check_font(WINDOWS_FONTS / "arial.ttf")
    assert result.ok
    assert result.missing == ()
    assert result.ratio == 1.0


@pytest.mark.skipif(not (WINDOWS_FONTS / "comic.ttf").exists(), reason="needs Windows fonts")
def test_comic_sans_is_rejected():
    """The obvious face to reach for is the one that cannot render Vietnamese."""
    result = check_font(WINDOWS_FONTS / "comic.ttf")
    assert not result.ok
    assert "ơ" in result.missing


@pytest.mark.skipif(not (WINDOWS_FONTS / "bahnschrift.ttf").exists(), reason="needs Windows fonts")
def test_a_single_missing_glyph_still_fails():
    """Bahnschrift lacks only 'ỷ' -- invisible until it renders as tofu."""
    result = check_font(WINDOWS_FONTS / "bahnschrift.ttf")
    assert not result.ok
    assert "ỷ" in result.missing


def test_unreadable_font_raises_valueerror(tmp_path: Path):
    fake = tmp_path / "not-a-font.ttf"
    fake.write_bytes(b"definitely not a font")
    with pytest.raises(ValueError, match="cannot read font"):
        check_font(fake)


def test_describe_is_human_readable():
    result = check_font(WINDOWS_FONTS / "arial.ttf")
    assert "full Vietnamese coverage" in result.describe()


def test_bundled_fonts_all_pass():
    """Whatever fetch_fonts.py left on disk must be renderable."""
    results = check_directory(fonts_dir())
    if not results:
        pytest.skip("no bundled fonts yet; run scripts/fetch_fonts.py")
    bad = [r.describe() for r in results if not r.ok]
    assert not bad, "fonts on disk that cannot render Vietnamese:\n" + "\n".join(bad)


def test_check_directory_on_missing_dir_returns_empty(tmp_path: Path):
    assert check_directory(tmp_path / "nope") == []
