"""Korean OCR: line segmentation, and the provider that drives it.

``line_bands`` is pure geometry and always runs. The provider itself needs the
PP-OCRv5 weights, so those tests skip when the models are not fetched -- they
are the ones that would otherwise fail on a clean checkout for a reason that
has nothing to do with the code.
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from comic.ocr.registry import (
    MIN_LINE_HEIGHT,
    OcrError,
    KoreanOcrProvider,
    line_bands,
    provider_for,
)
from comic.paths import models_dir

HAVE_MODELS = (
    (models_dir() / KoreanOcrProvider.MODEL).exists()
    and (models_dir() / KoreanOcrProvider.KEYS).exists()
)
needs_models = pytest.mark.skipif(
    not HAVE_MODELS, reason="Korean weights not fetched (fetch_models.py --only korean)"
)


def lined(count: int, *, height: int = 30, gap: int = 20, width: int = 400,
          inverted: bool = False) -> np.ndarray:
    """A crop holding ``count`` bars of ink, laid out like lines of type."""
    background, ink = (0, 255) if inverted else (255, 0)
    crop = np.full((gap + count * (height + gap), width, 3), background, np.uint8)
    for line in range(count):
        top = gap + line * (height + gap)
        crop[top:top + height, 30:width - 30] = ink
    return crop


# ---------------------------------------------------------------- segmenting


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_each_line_becomes_one_band(count):
    assert len(line_bands(lined(count))) == count


def test_white_on_black_segments_the_same_way():
    """Inverted panels are ordinary; assuming dark ink reads the gaps instead."""
    assert len(line_bands(lined(3, inverted=True))) == 3


def test_bands_are_returned_top_to_bottom():
    bands = line_bands(lined(4))
    assert bands == sorted(bands)
    for (_, end), (start, _) in zip(bands, bands[1:]):
        assert end <= start


@pytest.mark.parametrize("size", [18, 26, 34, 48, 64])
def test_real_hangul_never_splits_within_a_line(size):
    """The margin this rests on, measured rather than assumed.

    A line breaks only where the crop is clear all the way across, and across
    a row of Hangul some syllable always has ink: measured over eight lines at
    five sizes, the longest clear run *inside* a line is 0 rows. So the whole
    inter-line gap is the margin, and ``LINE_GAP`` is generous rather than
    tight -- which is why one font size does not need one tolerance.
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", size)
    except OSError:
        pytest.skip("no Korean-capable font on this machine")

    for text in ("그 사람이", "정말로 왔다고?", "내가 말했잖아", "괜찮아요?"):
        image = Image.new("RGB", (int(font.getlength(text)) + 40, size + 30), "white")
        ImageDraw.Draw(image).text((20, 14), text, font=font, fill="black")
        assert len(line_bands(np.array(image))) == 1, text


def test_speckle_is_not_a_line():
    crop = np.full((120, 400, 3), 255, np.uint8)
    crop[60:60 + max(1, MIN_LINE_HEIGHT - 2), 200:203] = 0
    assert line_bands(crop) == []


def test_a_blank_crop_has_no_bands():
    assert line_bands(np.full((100, 300, 3), 255, np.uint8)) == []
    assert line_bands(np.zeros((0, 0, 3), np.uint8)) == []


# ------------------------------------------------------------------ routing


def test_korean_routes_to_the_korean_provider():
    for tag in ("ko", "kor", "korean", "KO"):
        assert KoreanOcrProvider.name == _resolve(tag)


def _resolve(tag: str) -> str:
    from comic.ocr.registry import _PROVIDER_CLASSES

    for cls in _PROVIDER_CLASSES:
        if tag.lower() in cls.languages:
            return cls.name
    raise AssertionError(f"nothing serves {tag!r}")


def test_korean_is_never_served_by_the_chinese_engine():
    """Chinese OCR on Korean returns confident nonsense, which is worse than
    an error because nothing downstream can tell."""
    from comic.ocr.registry import RapidOcrProvider

    assert not RapidOcrProvider.languages & KoreanOcrProvider.languages


@pytest.mark.skipif(HAVE_MODELS, reason="weights are present")
def test_missing_weights_name_the_command_that_fixes_it():
    with pytest.raises(OcrError, match="fetch_models"):
        provider_for("ko")


# ----------------------------------------------------------------- reading


@needs_models
def test_the_provider_reads_a_rendered_korean_bubble():
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 34)
    except OSError:
        pytest.skip("no Korean-capable font on this machine")

    lines = ["내가 말했잖아", "여기 오면", "안 된다고"]
    width = max(int(font.getlength(line)) for line in lines) + 60
    image = Image.new("RGB", (width, 50 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((30, 20 + index * 50), line, font=font, fill="black")

    outcome = provider_for("ko").read(np.array(image))
    assert outcome.text == " ".join(lines)
    assert outcome.confidence > 0.8
    # One box per line, which is what truncation detection measures against.
    assert len(outcome.boxes) == len(lines)


@needs_models
def test_an_empty_crop_is_reported_as_unread_not_invented():
    outcome = provider_for("ko").read(np.full((80, 300, 3), 255, np.uint8))
    assert outcome.text == ""
    assert outcome.suspicious
