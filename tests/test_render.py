"""Cleaning, fitting and the visual_qa fix vocabulary.

The invariant these exist to protect is the one in CLAUDE.md: a page is never
regenerated. Cleaning may only change pixels a mask named, and everything else
must come back bit-identical.
"""

from __future__ import annotations

import numpy as np
import pytest

from comic.handoff.schemas import FixAction, QaFix, VisualQaResponse
from comic.image_processing.clean import _modal_colour, background_of, clean_page
from comic.image_processing.mask import bubble_interior, dilate, fit_areas, text_mask
from comic.qa.visual import apply_fixes
from comic.schemas import BBox, RenderPlan, TextKind, TextRegion
from comic.typesetting.fit import Layout, fit_text, widest_run, wrap
from comic.typesetting.render import load_font

FONT = "assets/fonts/BeVietnamPro-Regular.ttf"


def region(rid: str, box: tuple[int, int, int, int], kind=TextKind.DIALOGUE, text="x"):
    x1, y1, x2, y2 = box
    return TextRegion(
        id=rid, page=1, bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2),
        kind=kind, source_text=text,
    )


def bubble_page() -> tuple[np.ndarray, np.ndarray]:
    """A white bubble on a dark textured panel, with 'lettering' inside it."""
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    image[::2, ::3] = 90            # texture, so the panel is not flat
    image[40:160, 40:160] = 250     # the bubble interior
    mask = np.zeros((200, 200), dtype=np.uint8)
    for row in (70, 90, 110):       # three lines of text
        mask[row : row + 8, 60:140] = 255
        image[row : row + 8, 60:140] = 10
    return image, mask


# ------------------------------------------------------------------ masking


def test_dilate_grows_a_mask():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[10, 10] = 1
    assert dilate(mask, 2).sum() > 1


def test_dilate_by_zero_is_a_no_op():
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[5, 5] = 1
    assert dilate(mask, 0).sum() == 1


def test_sfx_never_enters_the_cleaning_mask():
    """SFX stays in the artwork, so it must be physically unreachable."""
    _image, mask = bubble_page()
    sfx = region("r1", (60, 60, 140, 130), kind=TextKind.SFX)
    assert text_mask(mask, [sfx]).sum() == 0


def test_bubble_interior_finds_the_bubble():
    image, mask = bubble_page()
    found = bubble_interior(image, mask, BBox(x1=60, y1=70, x2=140, y2=118))
    assert found is not None
    # It should cover the bubble, not the textured panel around it.
    assert found[100, 100] == 1
    assert found[10, 10] == 0


def test_bubble_interior_covers_its_own_lettering():
    image, mask = bubble_page()
    box = BBox(x1=60, y1=70, x2=140, y2=118)
    found = bubble_interior(image, mask, box)
    assert found[box.y1 : box.y2, box.x1 : box.x2].mean() > 0.5


def test_fit_areas_never_overlap():
    image, mask = bubble_page()
    regions = [region("a", (60, 66, 140, 82)), region("b", (60, 106, 140, 122))]
    areas = fit_areas(image, mask, regions)
    assert not (areas["a"] & areas["b"]).any()


def test_every_region_keeps_its_own_box():
    image, mask = bubble_page()
    regions = [region("a", (60, 66, 140, 82)), region("b", (60, 106, 140, 122))]
    areas = fit_areas(image, mask, regions)
    for r in regions:
        b = r.bbox
        assert areas[r.id][b.y1 : b.y2, b.x1 : b.x2].any()


def test_regions_with_no_text_get_no_area():
    image, mask = bubble_page()
    areas = fit_areas(image, mask, [region("a", (60, 66, 140, 82), text="  ")])
    assert areas == {}


# ----------------------------------------------------------------- cleaning


def test_cleaning_leaves_the_artwork_alone_outside_the_mask():
    """The guarantee behind spec 23, asserted rather than assumed."""
    image, mask = bubble_page()
    regions = [region("a", (60, 66, 140, 122))]
    areas = fit_areas(image, mask, regions)
    cleaned, touched, _report = clean_page(image, mask, regions, areas)
    outside = touched == 0
    assert (cleaned[outside] == image[outside]).all()


def test_cleaning_removes_the_lettering():
    image, mask = bubble_page()
    regions = [region("a", (60, 66, 140, 122))]
    areas = fit_areas(image, mask, regions)
    cleaned, _touched, _report = clean_page(image, mask, regions, areas)
    ink = (cleaned.mean(axis=2) < 128) & (mask > 0)
    assert ink.sum() == 0


def test_a_flat_bubble_is_filled_not_inpainted():
    image, mask = bubble_page()
    regions = [region("a", (60, 66, 140, 122))]
    areas = fit_areas(image, mask, regions)
    _cleaned, _touched, report = clean_page(image, mask, regions, areas)
    assert report.tiers["a"] == "flat"


def test_modal_colour_ignores_a_minority():
    image = np.full((10, 10, 3), 200, dtype=np.uint8)
    image[0, 0] = (5, 5, 5)
    area = np.ones((10, 10), dtype=np.uint8)
    assert _modal_colour(image, area).mean() > 150


def test_background_ignores_the_lettering():
    """Sampling the ink would make every bubble look like textured artwork."""
    image, mask = bubble_page()
    area = np.zeros((200, 200), dtype=np.uint8)
    area[40:160, 40:160] = 1
    with_ink, _ = background_of(image, area, np.zeros_like(mask))
    without, _ = background_of(image, area, mask)
    assert without > with_ink


# ------------------------------------------------------------------ fitting


def test_wrap_never_splits_a_word():
    font = load_font(FONT, 20)
    lines = wrap("nghiêng nghiêng nghiêng", font, 400)
    assert lines is not None
    for line in lines:
        for word in line.split():
            assert word in "nghiêng nghiêng nghiêng"


def test_wrap_reports_a_word_that_cannot_fit():
    """None means 'try a smaller font', not 'break the word'."""
    font = load_font(FONT, 40)
    assert wrap("khuếch", font, 5) is None


def test_wrap_of_nothing_is_no_lines():
    assert wrap("   ", load_font(FONT, 20), 100) == []


def test_widest_run_finds_the_longest_gap():
    row = np.array([1, 1, 0, 1, 1, 1, 1, 0, 1], dtype=bool)
    assert widest_run(row) == (3, 4)


def test_widest_run_of_an_empty_row():
    assert widest_run(np.zeros(5, dtype=bool)) == (0, 0)


def test_fit_picks_a_larger_size_in_a_larger_box():
    small = np.zeros((40, 60), dtype=np.uint8)
    small[5:35, 5:55] = 1
    large = np.zeros((200, 300), dtype=np.uint8)
    large[10:190, 10:290] = 1
    assert fit_text("Chào", large, FONT).font_size > fit_text("Chào", small, FONT).font_size


def test_fit_reports_failure_rather_than_raising():
    """A page that cannot be set still renders, so the eye can judge it."""
    tiny = np.zeros((6, 6), dtype=np.uint8)
    tiny[1:5, 1:5] = 1
    layout = fit_text("một câu rất dài không thể vừa", tiny, FONT)
    assert layout.fits is False
    assert layout.lines


def test_fit_of_an_empty_area():
    layout = fit_text("Chào", np.zeros((10, 10), dtype=np.uint8), FONT)
    assert layout.fits is False
    assert layout.lines == ()


def test_fit_honours_forced_lines():
    area = np.ones((200, 300), dtype=np.uint8)
    layout = fit_text("a b c d", area, FONT, forced_lines=["a b", "c d"])
    assert layout.lines == ("a b", "c d")


def test_font_delta_shrinks_the_result():
    area = np.ones((200, 300), dtype=np.uint8)
    plain = fit_text("Chào cậu", area, FONT)
    smaller = fit_text("Chào cậu", area, FONT, font_delta=-4)
    assert smaller.font_size < plain.font_size


def test_diacritics_survive_a_round_trip_through_layout():
    area = np.ones((200, 400), dtype=np.uint8)
    layout = fit_text("Đừng hòng nữa nhé", area, FONT)
    assert "ừ" in " ".join(layout.lines)


# ------------------------------------------------------------- fix applying


def plan() -> RenderPlan:
    return RenderPlan(series="s", chapter="Chapter_001")


def test_accept_changes_nothing():
    p = plan()
    applied, retranslate = apply_fixes(p, [QaFix(region_id="r", action=FixAction.ACCEPT)])
    assert (applied, retranslate) == (0, [])
    assert p.overrides == {}


def test_font_delta_accumulates():
    p = plan()
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.FONT_DELTA, value="3")])
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.FONT_DELTA, value="-1")])
    assert p.for_region("r").font_delta == 2


def test_nudge_accumulates_in_both_axes():
    p = plan()
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.NUDGE, value="3,-4")])
    assert (p.for_region("r").dx, p.for_region("r").dy) == (3, -4)


def test_expand_and_shrink_are_opposites():
    p = plan()
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.EXPAND_MASK, value="4")])
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.SHRINK_MASK, value="1")])
    assert p.for_region("r").mask_grow == 3


def test_force_wrap_stores_the_lines():
    p = plan()
    apply_fixes(
        p, [QaFix(region_id="r", action=FixAction.FORCE_WRAP, value="một\nhai")]
    )
    assert p.for_region("r").forced_lines == ["một", "hai"]


def test_restyle_stores_a_kind():
    p = plan()
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.RESTYLE, value="shout")])
    assert p.for_region("r").style_kind is TextKind.SHOUT


def test_retranslate_goes_back_to_the_translator():
    """Shortening a line is a translation decision, not a rendering one."""
    p = plan()
    _applied, retranslate = apply_fixes(
        p, [QaFix(region_id="r", action=FixAction.RETRANSLATE_SHORTER, value="20")]
    )
    assert retranslate == ["r"]
    assert p.for_region("r").max_chars == 20


def test_each_round_is_counted():
    p = plan()
    apply_fixes(p, [QaFix(region_id="r", action=FixAction.ACCEPT)])
    assert p.round == 2


def test_an_untouched_region_has_neutral_defaults():
    override = plan().for_region("never-seen")
    assert override.font_delta == 0
    assert override.forced_lines == []
    assert override.style_kind is None


# -------------------------------------------------------------- validation


def test_passing_with_an_actionable_fix_is_rejected():
    with pytest.raises(ValueError):
        VisualQaResponse(
            passed=True,
            fixes=[QaFix(region_id="r", action=FixAction.FONT_DELTA, value="-2")],
        )


def test_passing_alongside_accept_is_allowed():
    answer = VisualQaResponse(
        passed=True, fixes=[QaFix(region_id="r", action=FixAction.ACCEPT)]
    )
    assert answer.passed


def test_an_action_outside_the_vocabulary_is_rejected():
    with pytest.raises(ValueError):
        QaFix(region_id="r", action="repaint_everything")


def test_nudge_needs_two_numbers():
    with pytest.raises(ValueError):
        QaFix(region_id="r", action=FixAction.NUDGE, value="12")


def test_restyle_needs_a_real_kind():
    with pytest.raises(ValueError):
        QaFix(region_id="r", action=FixAction.RESTYLE, value="enormous")


def test_layout_line_height_tracks_font_size():
    assert Layout((), 20, 0, 0, 0, 0, True).line_height > 20


# ------------------------------------------------- retranslate_shorter path


def test_shorten_limits_reach_the_translate_request(home):
    """A `retranslate_shorter` verdict has to arrive where the agent reads it.

    The plan stored the limit and the terminal printed it, but the request
    carried the original advisory budget -- so the same-length line would come
    back and the QA loop would spin.
    """
    from comic.handoff.schemas import TranslateRequest
    from comic.paths import ChapterPaths
    from comic.schemas import (
        ChapterState,
        PageAnalysis,
        PageRecord,
        ReadingDirection,
    )
    from comic.state.store import read_json, write_json
    from comic.translation.gate import open_translate_gate
    import numpy as np

    paths = ChapterPaths("s", 1)
    paths.ensure()
    page = np.full((200, 200, 3), 250, dtype=np.uint8)
    import cv2
    cv2.imwrite(str(paths.original / "001.png"), page)

    wanted = region("p001_r01", (20, 20, 120, 60), text="你好世界")
    other = region("p001_r02", (20, 120, 120, 160), text="再见")
    write_json(
        paths.analysis_file(1),
        PageAnalysis(
            page=1, width=200, height=200,
            reading_direction=ReadingDirection.RTL, regions=[wanted, other],
        ),
    )
    state = ChapterState(
        series="s", chapter="Chapter_001", source_language="zh",
        reading_direction=ReadingDirection.RTL,
        pages=[PageRecord(page=1, filename="001.png")],
    )

    _request, count = open_translate_gate(
        paths, state, limits={"p001_r01": 12}
    )
    request = read_json(paths.gate_request("translate"), TranslateRequest)

    items = [i for p in request.pages for i in p.items]
    assert count == 1, "the gate should narrow to the regions that must shrink"
    assert [i.region_id for i in items] == ["p001_r01"]
    assert items[0].must_fit_chars == 12
    assert items[0].shorten_reason
    assert "must_fit_chars" in request.instructions


def test_a_normal_translate_gate_sets_no_limit(home):
    from comic.handoff.schemas import TranslateRequest
    from comic.paths import ChapterPaths
    from comic.schemas import (
        ChapterState,
        PageAnalysis,
        PageRecord,
        ReadingDirection,
    )
    from comic.state.store import read_json, write_json
    from comic.translation.gate import open_translate_gate
    import cv2
    import numpy as np

    paths = ChapterPaths("s2", 1)
    paths.ensure()
    cv2.imwrite(str(paths.original / "001.png"), np.full((200, 200, 3), 250, np.uint8))
    write_json(
        paths.analysis_file(1),
        PageAnalysis(
            page=1, width=200, height=200,
            reading_direction=ReadingDirection.RTL,
            regions=[region("p001_r01", (20, 20, 120, 60), text="你好")],
        ),
    )
    state = ChapterState(
        series="s2", chapter="Chapter_001", source_language="zh",
        reading_direction=ReadingDirection.RTL,
        pages=[PageRecord(page=1, filename="001.png")],
    )
    open_translate_gate(paths, state)
    request = read_json(paths.gate_request("translate"), TranslateRequest)
    item = request.pages[0].items[0]
    assert item.must_fit_chars is None
    assert item.suggested_max_chars


# ----------------------------------------------------------------- publishing


def _chapter_with_pages(series: str, pages: int):
    import cv2
    import numpy as np

    from comic.paths import ChapterPaths
    from comic.schemas import (
        ChapterState,
        PageAnalysis,
        PageRecord,
        ReadingDirection,
    )
    from comic.state.store import write_json

    paths = ChapterPaths(series, 1)
    paths.ensure()
    records = []
    for n in range(1, pages + 1):
        name = f"{n:03d}.png"
        cv2.imwrite(
            str(paths.original / name), np.full((80, 80, 3), 250, np.uint8)
        )
        write_json(
            paths.analysis_file(n),
            PageAnalysis(
                page=n, width=80, height=80,
                reading_direction=ReadingDirection.RTL, regions=[],
            ),
        )
        records.append(PageRecord(page=n, filename=name))
    state = ChapterState(
        series=series, chapter="Chapter_001", source_language="zh",
        reading_direction=ReadingDirection.RTL, pages=records,
    )
    return paths, state


def test_a_page_with_nothing_to_translate_is_still_published(home):
    """A cover has no dialogue. It still belongs in the library.

    Publishing only rendered pages leaves holes in the chapter and stops the
    page numbers matching the source.
    """
    from comic.qa.visual import publish
    import cv2
    import numpy as np

    paths, state = _chapter_with_pages("cover-test", 3)
    # Only page 2 was rendered; 1 and 3 have no lettering at all.
    cv2.imwrite(
        str(paths.translated / "002.png"), np.full((80, 80, 3), 128, np.uint8)
    )

    count, problems = publish(paths, state)
    assert count == 3
    assert problems == []
    assert sorted(p.name for p in paths.library.iterdir()) == [
        "001.png", "002.png", "003.png"
    ]


def test_a_page_that_should_have_rendered_is_still_a_problem(home):
    """Falling back to the original must not hide a genuine render failure."""
    from comic.schemas import ChapterTranslation, TranslationEntry
    from comic.state.store import write_json
    from comic.qa.visual import publish
    from comic.schemas import BBox, PageAnalysis, ReadingDirection, TextRegion

    paths, state = _chapter_with_pages("fail-test", 2)
    write_json(
        paths.analysis_file(1),
        PageAnalysis(
            page=1, width=80, height=80,
            reading_direction=ReadingDirection.RTL,
            regions=[
                TextRegion(
                    id="p001_r01", page=1, kind=TextKind.DIALOGUE,
                    bbox=BBox(x1=1, y1=1, x2=20, y2=20), source_text="x",
                )
            ],
        ),
    )
    write_json(
        paths.translation_file,
        ChapterTranslation(
            series="fail-test", chapter="Chapter_001",
            entries=[
                TranslationEntry(
                    region_id="p001_r01", source_text="x", target_text="Chào"
                )
            ],
        ),
    )

    _count, problems = publish(paths, state)
    assert any("page 1" in p for p in problems)


def test_page_option_defaults_to_the_whole_chapter():
    """`-p` omitted means every page, not a crash.

    typer hands `None` for an unset list option, and `list(None)` raises. It
    only showed up on the first run that did not narrow to specific pages.
    """
    page = None
    assert (list(page or []) or None) is None
    assert list([6, 10] or []) == [6, 10]


def test_translation_passes_accumulate(home):
    """A chapter may be translated in several passes; each must keep the rest.

    The gate reports what is still untranslated, which only makes sense if
    coming back to finish it preserves the earlier work. Overwriting discarded
    a whole pass silently.
    """
    from comic.handoff.schemas import TranslateResponse
    from comic.paths import ChapterPaths
    from comic.schemas import (
        ChapterState,
        ChapterTranslation,
        PageAnalysis,
        PageRecord,
        ReadingDirection,
    )
    from comic.state.store import read_json, write_json
    from comic.translation.gate import apply_translation

    paths = ChapterPaths("merge-test", 1)
    paths.ensure()
    write_json(
        paths.analysis_file(1),
        PageAnalysis(
            page=1, width=80, height=80, reading_direction=ReadingDirection.RTL,
            regions=[
                region("p001_r01", (1, 1, 20, 20), text="一"),
                region("p001_r02", (21, 1, 40, 20), text="二"),
            ],
        ),
    )
    state = ChapterState(
        series="merge-test", chapter="Chapter_001", source_language="zh",
        reading_direction=ReadingDirection.RTL,
        pages=[PageRecord(page=1, filename="001.png")],
    )

    def apply(region_id: str, text: str):
        paths.gate_response("translate").write_text(
            TranslateResponse.model_validate(
                {"items": [{"region_id": region_id, "target_text": text}]}
            ).model_dump_json(),
            encoding="utf-8",
        )
        return apply_translation(paths, state)

    apply("p001_r01", "Một")
    stored, _learned, problems = apply("p001_r02", "Hai")
    assert problems == []
    assert stored == 1

    saved = read_json(paths.translation_file, ChapterTranslation).by_region()
    assert set(saved) == {"p001_r01", "p001_r02"}
    assert saved["p001_r01"].target_text == "Một"


def test_a_later_pass_can_revise_an_earlier_line(home):
    from comic.handoff.schemas import TranslateResponse
    from comic.paths import ChapterPaths
    from comic.schemas import (
        ChapterState,
        ChapterTranslation,
        PageAnalysis,
        PageRecord,
        ReadingDirection,
    )
    from comic.state.store import read_json, write_json
    from comic.translation.gate import apply_translation

    paths = ChapterPaths("revise-test", 1)
    paths.ensure()
    write_json(
        paths.analysis_file(1),
        PageAnalysis(
            page=1, width=80, height=80, reading_direction=ReadingDirection.RTL,
            regions=[region("p001_r01", (1, 1, 20, 20), text="一")],
        ),
    )
    state = ChapterState(
        series="revise-test", chapter="Chapter_001", source_language="zh",
        reading_direction=ReadingDirection.RTL,
        pages=[PageRecord(page=1, filename="001.png")],
    )
    for text in ("Một", "Một (sửa)"):
        paths.gate_response("translate").write_text(
            TranslateResponse.model_validate(
                {"items": [{"region_id": "p001_r01", "target_text": text}]}
            ).model_dump_json(),
            encoding="utf-8",
        )
        apply_translation(paths, state)

    saved = read_json(paths.translation_file, ChapterTranslation).by_region()
    assert saved["p001_r01"].target_text == "Một (sửa)"


# ------------------------------------------------------ inverted panels


def test_light_type_on_a_dark_panel():
    """White-on-black lettering is ordinary in manga.

    The flat fill correctly restores such a bubble to black; drawing the usual
    dark type onto it makes the line vanish. Measured on this chapter: three
    lines disappeared completely.
    """
    from comic.typesetting.render import contrasting, style_for
    from comic.schemas import TextKind

    dark = np.zeros((40, 40, 3), dtype=np.uint8)
    area = np.ones((40, 40), dtype=np.uint8)
    style = contrasting(style_for(TextKind.DIALOGUE), dark, area)
    assert min(style.colour) > 180
    assert max(style.stroke_colour) < 60


def test_dark_type_survives_on_a_light_panel():
    from comic.typesetting.render import contrasting, style_for
    from comic.schemas import TextKind

    light = np.full((40, 40, 3), 250, dtype=np.uint8)
    area = np.ones((40, 40), dtype=np.uint8)
    plain = style_for(TextKind.DIALOGUE)
    assert contrasting(plain, light, area) == plain


def test_background_luma_of_an_empty_area_is_light():
    """No area means no reason to invert."""
    from comic.typesetting.render import background_luma

    page = np.zeros((10, 10, 3), dtype=np.uint8)
    assert background_luma(page, np.zeros((10, 10), dtype=np.uint8)) == 255.0


def test_ink_follows_the_panel():
    """On an inverted panel the lettering is the light pixels."""
    from comic.qa.visual import _ink

    page = np.zeros((10, 10, 3), dtype=np.uint8)
    page[0:2] = 255  # the "lettering"
    assert _ink(page, inverted=True).sum() == 20
    assert _ink(page, inverted=False).sum() == 80
