"""Reading order of detected text runs.

The geometry below is not invented: it is what RapidOCR actually returned for
four regions of the Conan chapter, recorded so the regression is pinned without
needing the engine or the artwork.

The failure this guards against is nasty precisely because it looks fine --
every individual run is recognised perfectly, and only the sentence is wrong.
"""

from __future__ import annotations

import pytest

import numpy as np

from comic.translation.gate import suggested_chars
from comic.ocr.registry import (
    MAX_UNREAD_MASK,
    _Chunk,
    _in_reading_order,
    _reads_vertically,
    unread_mask_fraction,
)


def chunks(rows: list[tuple[float, float, float, float, str]]) -> list[_Chunk]:
    return [
        _Chunk(cx=cx, cy=cy, width=w, height=h, text=t, score=1.0)
        for cx, cy, w, h, t in rows
    ]


def joined(rows, hint: bool) -> str:
    parts = chunks(rows)
    return "".join(
        c.text for c in _in_reading_order(parts, _reads_vertically(parts, hint))
    )


#: Four vertical columns in a bubble 93x78 -- wider than tall, so the region's
#: own aspect ratio calls this horizontal. The detected runs say otherwise.
BUBBLE_COLUMNS = [
    (73.0, 100.0, 38.0, 148.0, "全部实情了"),
    (108.0, 99.0, 40.0, 148.0, "生早已吐出"),
    (149.0, 99.0, 38.0, 148.0, "你的主治医"),
    (185.0, 79.8, 36.0, 106.0, "死心吧"),
]

#: A horizontal caption: a name over a school year.
CAPTION_LINES = [
    (124.2, 43.8, 113.0, 38.0, "毛利兰"),
    (125.0, 97.0, 200.0, 36.0, "帝丹高中二年级"),
]

#: One tall column plus one stray speck the detector picked up.
MIXED = [
    (45.8, 124.8, 39.0, 162.0, "别生气嘛!"),
    (102.0, 137.0, 12.0, 12.0, "1"),
]

THREE_COLUMNS = [
    (40.2, 55.5, 42.0, 65.0, "乐园"),
    (77.5, 98.5, 39.0, 153.0, "在多罗碧加"),
    (115.5, 92.5, 33.0, 137.0, "明天10点"),
]


def test_columns_are_read_right_to_left():
    assert joined(BUBBLE_COLUMNS, hint=False) == "死心吧你的主治医生早已吐出全部实情了"


def test_column_layout_is_detected_despite_a_wide_region():
    """The bug that motivated this: a wide bubble holding short columns."""
    assert _reads_vertically(chunks(BUBBLE_COLUMNS), hint=False) is True


def test_horizontal_lines_keep_top_to_bottom_order():
    assert joined(CAPTION_LINES, hint=False) == "毛利兰帝丹高中二年级"


def test_horizontal_lines_are_not_reversed():
    """Reversing a horizontal caption is the damage an unconditional flip does."""
    assert _reads_vertically(chunks(CAPTION_LINES), hint=True) is False


def test_exact_tie_falls_back_to_the_region_hint():
    assert _reads_vertically(chunks(MIXED), hint=False) is False
    assert _reads_vertically(chunks(MIXED), hint=True) is True


def test_tie_broken_as_horizontal_keeps_the_legible_reading():
    assert joined(MIXED, hint=False) == "别生气嘛!1"


def test_three_columns():
    assert joined(THREE_COLUMNS, hint=False) == "明天10点在多罗碧加乐园"


#: The same two regions as read by the OCR stage itself, at 1x rather than the
#: gate's 2x crop. Both were merged into one column by an earlier grouping rule
#: that used a size-scaled distance tolerance -- which is about one column
#: wide, so it joined neighbours and then ordered them by height.
ADJACENT_COLUMNS = [
    (21.0, 28.0, 38.0, 44.0, "气"),
    (39.5, 47.5, 25.0, 79.0, "才没有生"),
]

LOOSE_COLUMNS = [
    (52.0, 36.0, 32.0, 58.0, "玩笑的"),
    (20.8, 59.8, 20.0, 86.0, "别生气嘛!"),
    (65.0, 52.5, 24.0, 89.0, "我刚才是开"),
]


def test_a_wide_single_glyph_box_does_not_swallow_its_neighbour():
    """One character gets a box wider than the column it sits in."""
    assert joined(ADJACENT_COLUMNS, hint=True) == "才没有生气"


def test_loosely_drawn_columns_stay_separate():
    assert joined(LOOSE_COLUMNS, hint=False) == "我刚才是开玩笑的别生气嘛!"


@pytest.mark.parametrize("hint", [True, False])
def test_a_single_run_is_never_reordered(hint):
    assert joined([(10.0, 10.0, 8.0, 30.0, "哇")], hint=hint) == "哇"


def test_no_runs_is_empty():
    assert _in_reading_order([], vertical=True) == []


def test_runs_sharing_a_column_are_ordered_downwards():
    """Two runs the detector split inside one column must not swap."""
    rows = [
        (50.0, 120.0, 30.0, 60.0, "下"),
        (52.0, 40.0, 30.0, 60.0, "上"),
    ]
    assert joined(rows, hint=True) == "上下"


# ------------------------------------------------------------------ coverage


def test_unread_fraction_is_zero_when_runs_cover_the_mask():
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:30, 10:30] = 255
    assert unread_mask_fraction(mask, ((10, 10, 30, 30),)) == 0.0


def test_unread_fraction_counts_text_no_run_reached():
    """Two of three columns never detected: the case that reports 1.00."""
    mask = np.zeros((40, 60), dtype=np.uint8)
    for left in (5, 25, 45):
        mask[10:30, left : left + 10] = 255
    unread = unread_mask_fraction(mask, ((45, 10, 55, 30),))
    assert unread == pytest.approx(2 / 3)


def test_no_boxes_means_unknown_not_missing():
    """An engine that reports no geometry must not look like total failure."""
    mask = np.ones((10, 10), dtype=np.uint8) * 255
    assert unread_mask_fraction(mask, ()) == 0.0


def test_a_mask_with_no_text_is_not_unread():
    assert unread_mask_fraction(np.zeros((10, 10), dtype=np.uint8), ((0, 0, 5, 5),)) == 0.0


def test_runs_outside_the_crop_are_clipped_not_crashed():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[5:15, 5:15] = 255
    assert unread_mask_fraction(mask, ((-50, -50, 500, 500),)) == 0.0


def test_a_truncated_reading_clears_the_threshold():
    """The measured case: one of three columns read, reported at 1.00."""
    mask = np.zeros((40, 60), dtype=np.uint8)
    for left in (5, 25, 45):
        mask[10:30, left : left + 10] = 255
    assert unread_mask_fraction(mask, ((45, 10, 55, 30),)) > MAX_UNREAD_MASK


# ------------------------------------------------------- bubble space budget


def test_budget_fits_a_real_line():
    """43x105 held 12 CJK characters; its Vietnamese needs about 36.

    The earlier formula pinned the font to height/3.2, which forces roughly
    three lines however much text there is, and offered 4.
    """
    assert suggested_chars(43, 105, 12) >= 30


def test_budget_grows_with_the_box_at_a_fixed_lettering_size():
    """Nine times the area at the same type size holds about nine times as much."""
    small = suggested_chars(40, 40, 10)
    large = suggested_chars(120, 120, 90)
    assert large > small * 5


def test_budget_is_scale_invariant():
    """The same page at twice the resolution must not change the budget.

    Deriving the font from the source lettering rather than from a pixel
    constant is what buys this, and it matters: these captures are 590px wide
    against a 1100px source, so the same bubble is measured at both sizes.
    """
    assert suggested_chars(80, 80, 12) == suggested_chars(160, 160, 12)


def test_smaller_source_lettering_means_more_room():
    """More source characters in one box means smaller type, so more fits."""
    assert suggested_chars(80, 80, 24) > suggested_chars(80, 80, 6)


def test_budget_survives_a_region_with_no_source_text():
    assert suggested_chars(60, 60, 0) >= 1


def test_budget_never_returns_zero():
    assert suggested_chars(1, 1, 50) >= 1
    assert suggested_chars(0, 0, 0) >= 1


def test_tiny_type_is_floored():
    """A huge character count must not imply a sub-legible font."""
    packed = suggested_chars(100, 100, 100000)
    loose = suggested_chars(100, 100, 1)
    assert packed >= loose
    assert packed < 100000


# ------------------------------------------------- joining runs back together


def test_latin_runs_are_joined_with_spaces():
    """An engine returns runs, not sentences, and Latin runs are words.

    Joining them with "" is correct for CJK and was never revisited, so on a
    real English scanlation a whole line came back as one unbroken token --
    untranslatable, unwrappable, and read by the length checks as a single
    enormous word.
    """
    from comic.ocr.registry import join_runs

    assert join_runs(["SINCE", "WHEN", "DO", "WE"]) == "SINCE WHEN DO WE"


def test_cjk_runs_are_still_joined_tight():
    """The behaviour the Chinese path was measured on must not change."""
    from comic.ocr.registry import join_runs

    assert join_runs(["死心吧", "你的主治医"]) == "死心吧你的主治医"
    assert join_runs(["안녕", "하세요"]) == "안녕하세요"


def test_the_separator_follows_the_text_not_the_declared_language():
    """So a Latin caption inside a Chinese page still reads correctly."""
    from comic.ocr.registry import join_runs

    assert join_runs(["HELLO", "THERE"]) == "HELLO THERE"


def test_blank_and_empty_runs_are_dropped():
    from comic.ocr.registry import join_runs

    assert join_runs([]) == ""
    assert join_runs(["   ", "x"]) == "x"
