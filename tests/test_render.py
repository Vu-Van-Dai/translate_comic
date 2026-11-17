"""Cleaning, fitting and the visual_qa fix vocabulary.

The invariant these exist to protect is the one in CLAUDE.md: a page is never
regenerated. Cleaning may only change pixels a mask named, and everything else
must come back bit-identical.
"""

from __future__ import annotations

from pathlib import Path

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


def test_a_short_line_in_a_big_bubble_gets_the_whole_bubble():
    """The bubble is far bigger than the search window around one short line.

    It used to fill that window edge to edge, trip the leak guard, and fall back
    to the text box -- so the translation was set at a fraction of the source's
    size. The wider retry must find the bubble instead.
    """
    image = np.zeros((500, 700, 3), dtype=np.uint8)
    image[::2, ::3] = 90                    # textured panel
    image[60:440, 60:640] = 250             # a big bubble
    mask = np.zeros((500, 700), dtype=np.uint8)
    mask[240:260, 290:410] = 255            # one short line in the middle
    image[240:260, 290:410] = 10
    box = BBox(x1=290, y1=240, x2=410, y2=260)
    found = bubble_interior(image, mask, box)
    assert found is not None
    assert found.sum() > 10 * (box.x2 - box.x1) * (box.y2 - box.y1)
    assert found[10, 10] == 0               # still not the panel


def test_text_on_an_open_panel_still_falls_back():
    """A flat area with no outline keeps running into every window's border.

    The wider retry must not mistake that for a bubble, or a caption on page
    white would be allowed to spread across the whole gutter.
    """
    image = np.full((500, 700, 3), 250, dtype=np.uint8)   # no outline anywhere
    mask = np.zeros((500, 700), dtype=np.uint8)
    mask[240:260, 290:410] = 255
    image[240:260, 290:410] = 10
    assert bubble_interior(image, mask, BBox(x1=290, y1=240, x2=410, y2=260)) is None


def test_a_white_bubble_ignores_a_tone_search_that_leaked(monkeypatch):
    """The tone search escapes a white bubble whose outline touches artwork.

    It used to win whenever it was larger, which on Super Lovers ch.1 was
    4x-14x the bubble: a line set above an empty bubble, two bubbles merged.
    """
    import comic.image_processing.mask as mask_mod

    image, mask = bubble_page()
    leaked = np.ones(image.shape[:2], dtype=np.uint8)      # the whole page
    monkeypatch.setattr(mask_mod, "toned_interior", lambda *a: leaked)
    areas = fit_areas(image, mask, [region("a", (60, 70, 140, 118))])
    assert areas["a"][10, 10] == 0          # not the panel
    assert areas["a"][100, 100] == 1        # still the bubble


def test_lettering_on_dot_tone_still_takes_the_tone_search(monkeypatch):
    import comic.image_processing.mask as mask_mod

    image, mask = bubble_page()
    image[40:160, 40:160] = 250
    image[40:160:3, 40:160:3] = 60           # dot tone behind the letters
    for row in (70, 90, 110):
        image[row : row + 8, 60:140] = 10
    toned = np.zeros(image.shape[:2], dtype=np.uint8)
    toned[30:170, 30:170] = 1
    monkeypatch.setattr(mask_mod, "toned_interior", lambda *a: toned)
    areas = fit_areas(image, mask, [region("a", (60, 70, 140, 118))])
    assert areas["a"][35, 35] == 1


def test_inpainting_a_grey_page_adds_no_colour(monkeypatch):
    """LaMa paints in colour; a black-and-white page must stay grey."""
    import comic.image_processing.clean as clean_mod

    class Colourful:
        def repair(self, patch, hole):
            out = patch.copy()
            out[hole > 0] = (0, 200, 0)           # a green speck
            return out

    monkeypatch.setattr(clean_mod, "_lama", lambda model: Colourful())
    patch = np.full((40, 40, 3), 120, dtype=np.uint8)
    hole = np.zeros((40, 40), dtype=np.uint8)
    hole[15:25, 15:25] = 1
    out = clean_mod._inpaint(patch, hole, None)
    spread = out.max(axis=-1).astype(int) - out.min(axis=-1)
    assert spread.max() == 0


def _column_page(field: int = 250):
    """A column of vertical 'lettering' on a plain field, hatching at x>=130."""
    image = np.full((200, 200, 3), field, dtype=np.uint8)
    image[:, 130::2] = 255 - field          # artwork to the right
    mask = np.zeros((200, 200), dtype=np.uint8)
    for row in range(50, 150, 12):          # one glyph per row, a column
        mask[row : row + 8, 92:108] = 255
        image[row : row + 8, 92:108] = 255 - field
    return image, mask


def test_vertical_lettering_on_open_field_widens_over_the_field():
    from comic.image_processing.mask import OPEN_CLEARANCE, widen_open_box

    image, mask = _column_page()
    box = BBox(x1=90, y1=48, x2=110, y2=150)
    wide = widen_open_box(image, mask, box, [])
    assert wide.x2 - wide.x1 > 2 * (box.x2 - box.x1)
    assert wide.x2 <= 130 - OPEN_CLEARANCE          # stops short of the hatching
    assert (wide.y1, wide.y2) == (box.y1, box.y2)


def test_open_widening_works_on_a_dark_field_and_spares_neighbours():
    from comic.image_processing.mask import widen_open_box

    image, mask = _column_page(field=10)
    box = BBox(x1=90, y1=48, x2=110, y2=150)
    neighbour = BBox(x1=40, y1=60, x2=60, y2=140)
    wide = widen_open_box(image, mask, box, [neighbour])
    assert wide.x1 >= neighbour.x2
    assert wide.x2 > box.x2


def test_a_wide_box_is_not_widened():
    from comic.image_processing.mask import widen_open_box

    image, mask = _column_page()
    box = BBox(x1=60, y1=90, x2=140, y2=110)
    assert widen_open_box(image, mask, box, []) == box


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


def test_restyle_needs_a_kind_or_a_voice_name():
    # "enormous" is shaped like a voice name; resume checks it against fonts.yaml.
    assert QaFix(region_id="r", action=FixAction.RESTYLE, value="enormous")
    with pytest.raises(ValueError):
        QaFix(region_id="r", action=FixAction.RESTYLE, value="ENORMOUS TEXT")


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


def test_the_group_poster_opens_every_published_chapter(home):
    """The operator (2026-10-26): every chapter opens with the QR poster.

    It is page 000, so the story pages keep the source's numbers.
    """
    import cv2
    import numpy as np

    from comic.poster import poster_path
    from comic.qa.visual import publish

    cv2.imwrite(str(poster_path()), np.full((60, 40, 3), 30, np.uint8))
    paths, state = _chapter_with_pages("poster-test", 2)

    publish(paths, state)
    assert sorted(p.name for p in paths.library.iterdir()) == [
        "000.png", "001.png", "002.png"
    ]


def test_an_excluded_page_is_not_published(home):
    """An uploader's insert is not a page of the comic.

    Distinct from the test above. A cover has nothing to translate and is
    still the work, so it is published untranslated. A scanlation group's
    credits card is somebody else's banner, and falling back to the original
    for it would put that banner in the library as though it were story.
    """
    from comic.qa.visual import publish

    paths, state = _chapter_with_pages("insert-test", 3)
    state.pages[0].excluded = True

    count, problems = publish(paths, state)
    assert count == 2
    assert problems == []
    assert sorted(p.name for p in paths.library.iterdir()) == ["002.png", "003.png"]


def test_a_held_page_keeps_its_number_in_the_webp_route(home):
    """The operator (2026-10-27): a page held back for its content is still
    a page of the comic, so the pages after it keep the source's numbers.

    An excluded insert closes up; a held page leaves its number, and the file
    the operator puts there survives a republish.
    """
    from comic.photoshop.publish import publish_chapter
    from comic.state.store import ChapterStore

    paths, state = _chapter_with_pages("held-test", 4)
    state.pages[0].excluded = True                       # insert: closes up
    state.pages[2].excluded = state.pages[2].held = True  # held: keeps a number
    ChapterStore(paths).save(state)
    paths.library.mkdir(parents=True, exist_ok=True)
    (paths.library / "002.webp").write_bytes(b"operator's own page")

    report = publish_chapter(paths)
    assert report.pages == 2 and report.held == 1
    assert sorted(p.name for p in paths.library.iterdir()) == [
        "001.webp", "002.webp", "003.webp"
    ]
    assert (paths.library / "002.webp").read_bytes() == b"operator's own page"


def test_a_page_whose_only_text_is_a_group_mark_is_never_published_raw(home):
    """The operator (2026-11-12): a wordless page carrying another group's
    watermark had no PSD, so publishing fell back to the original and the
    mark reached the library. Such a page needs its build like any other."""
    import pytest

    from comic.photoshop.publish import publish_chapter
    from comic.schemas import PageAnalysis, ReadingDirection
    from comic.state.store import ChapterStore, write_json

    paths, state = _chapter_with_pages("mark-test", 2)
    ChapterStore(paths).save(state)
    mark = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=0, y1=60, x2=40, y2=78),
                      kind=TextKind.ERASE, tag="watermark")
    write_json(paths.analysis_file(1), PageAnalysis(
        page=1, width=80, height=80, reading_direction=ReadingDirection.RTL, regions=[mark]))
    with pytest.raises(ValueError, match="1"):
        publish_chapter(paths)


def test_keep_published_keeps_lettered_pages_an_sfx_pass_did_not_rebuild(home):
    """The operator (2026-11-10): an old-route chapter gets an SFX-only pass.
    Its dialogue pages are already lettered in library/; only pages with an
    SFX get a new Photoshop build. Without the flag such a chapter is refused;
    with it, every other translated page keeps the page already published."""
    import cv2
    import pytest

    from comic.photoshop.publish import publish_chapter
    from comic.schemas import ChapterTranslation, PageAnalysis, ReadingDirection, TranslationEntry
    from comic.state.store import ChapterStore, write_json

    paths, state = _chapter_with_pages("keep-test", 3)
    ChapterStore(paths).save(state)
    for n in (1, 2):
        write_json(paths.analysis_file(n), PageAnalysis(
            page=n, width=80, height=80, reading_direction=ReadingDirection.RTL,
            regions=[region(f"p{n:03d}_r01", (10, 10, 60, 30))]))
    write_json(paths.translation_file, ChapterTranslation(
        series="keep-test", chapter="Chapter_001",
        entries=[TranslationEntry(region_id=f"p{n:03d}_r01", source_text="HI", target_text="CHÀO")
                 for n in (1, 2)]))
    paths.library.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 3):
        cv2.imwrite(str(paths.library / f"{n:03d}.png"), np.full((80, 80, 3), 10 * n, np.uint8))
    ps = paths.workspace / "photoshop"
    ps.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(ps / "002.png"), np.full((80, 80, 3), 200, np.uint8))  # the SFX page

    with pytest.raises(ValueError):
        publish_chapter(paths)
    report = publish_chapter(paths, keep_published=True)
    assert report.from_photoshop == 1 and report.pages == 3
    names = sorted(p.name for p in paths.library.iterdir() if p.name[:3] != "000")
    assert names == ["001.webp", "002.webp", "003.webp"]
    from PIL import Image
    assert abs(int(np.asarray(Image.open(paths.library / "001.webp"))[40, 40, 0]) - 10) <= 3
    assert abs(int(np.asarray(Image.open(paths.library / "002.webp"))[40, 40, 0]) - 200) <= 3


def test_an_erase_only_region_is_cleaned_and_never_translated():
    """The operator (2026-10-28): logos and watermarks are erased without the
    Photoshop pass. Kind "erase" is cleaned like lettering, gets no bubble,
    is never sent to the translate gate, and makes its page render even when
    nothing on it is translated."""
    from types import SimpleNamespace

    from comic.qa.visual import page_renders

    image = np.full((120, 200, 3), 230, np.uint8)
    image[95:110, 120:190] = 20                      # the watermark's letters
    page_mask = np.zeros((120, 200), np.uint8)
    page_mask[95:110, 120:190] = 255
    mark = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=115, y1=90, x2=195, y2=115),
                      kind=TextKind.ERASE, source_text="[erase]")
    assert not mark.translatable and mark.erasable

    areas = fit_areas(image, page_mask, [mark])
    assert areas["p001_r01"].sum() == 80 * 25        # its own box, nothing more
    cleaned, touched, _ = clean_page(image, page_mask, [mark], areas)
    assert (cleaned[95:110, 120:190] > 200).all()    # letters gone
    assert (cleaned[touched == 0] == image[touched == 0]).all()

    assert page_renders(SimpleNamespace(regions=[mark]), {})
    plain = mark.model_copy(update={"kind": TextKind.DIALOGUE})
    assert not page_renders(SimpleNamespace(regions=[plain]), {})


def test_content_status_tells_a_block_from_held_pages():
    """The operator (2026-10-28): a chapter-level problem is not a series
    shutdown, and one held page is not a chapter block."""
    from comic.schemas import ChapterState, PageRecord

    state = ChapterState(series="s", chapter="Chapter_001",
                         pages=[PageRecord(page=n, filename=f"{n:03d}.png") for n in (1, 2)])
    assert state.content_status == "ok"
    state.pages[1].excluded = state.pages[1].held = True
    assert state.content_status == "partial"
    assert [p.page for p in state.held_pages] == [2]
    state.blocked = "whole chapter"
    assert state.content_status == "blocked"


def test_a_blocked_chapter_is_skipped_and_its_neighbour_is_not(home):
    """`comic block` stops that chapter alone (exit 3); the next chapter's
    commands still run."""
    from typer.testing import CliRunner

    from comic.cli.app import app
    from comic.state.store import ChapterStore

    from comic.paths import ChapterPaths

    blocked, _ = _chapter_with_pages("queue-test", 2)
    ChapterStore(blocked).save(_)
    other_paths = ChapterPaths("queue-test", 2)
    other_paths.ensure()
    other = _.model_copy(update={"chapter": "Chapter_002"})
    ChapterStore(other_paths).save(other)

    runner = CliRunner()
    r = runner.invoke(app, ["block", "--series", "queue-test", "-c", "1", "--reason", "test"])
    assert r.exit_code == 0
    again = runner.invoke(app, ["block", "--series", "queue-test", "-c", "1", "--reason", "test"])
    assert again.exit_code == 0 and "already" in again.output       # idempotent

    assert runner.invoke(app, ["ps", "publish", "--series", "queue-test", "-c", "1"]).exit_code == 3
    assert runner.invoke(app, ["render", "--series", "queue-test", "-c", "1"]).exit_code == 3
    # The block does not spread: the neighbour is not refused as blocked (it
    # fails here only because this fixture gave it no page files).
    assert runner.invoke(app, ["ps", "publish", "--series", "queue-test", "-c", "2"]).exit_code != 3
    assert runner.invoke(app, ["render", "--series", "queue-test", "-c", "2"]).exit_code != 3

    report = runner.invoke(app, ["holds", "--series", "queue-test"])
    assert "blocked" in report.output and "Chapter_002" in report.output

    assert runner.invoke(app, ["block", "--series", "queue-test", "-c", "1", "--release"]).exit_code == 0
    assert ChapterStore(blocked).load().blocked is None


def test_a_held_page_needs_a_reason_and_is_reported(home):
    from typer.testing import CliRunner

    from comic.cli.app import app
    from comic.state.store import ChapterStore

    paths, state = _chapter_with_pages("hold-test", 3)
    ChapterStore(paths).save(state)
    runner = CliRunner()
    bad = runner.invoke(app, ["exclude", "--series", "hold-test", "-c", "1", "-p", "2", "--held"])
    assert bad.exit_code == 1
    ok = runner.invoke(app, ["exclude", "--series", "hold-test", "-c", "1", "-p", "2",
                             "--held", "--reason", "romantic scene, characters are minors"])
    assert ok.exit_code == 0
    record = ChapterStore(paths).load().page(2)
    assert record.held and record.excluded and record.hold_reason.startswith("romantic")
    report = runner.invoke(app, ["holds", "--series", "hold-test"])
    assert "partial" in report.output and "page 2" in report.output

    runner.invoke(app, ["exclude", "--series", "hold-test", "-c", "1", "-p", "2", "--restore"])
    record = ChapterStore(paths).load().page(2)
    assert not record.held and record.hold_reason is None


def test_the_analyze_queue_skips_a_blocked_chapter(tmp_path):
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location(
        "analyze_all", Path(__file__).resolve().parents[1] / "scripts" / "analyze_all.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tmp_path / "Chapter_001"
    d.mkdir()
    (d / "chapter.json").write_text(json.dumps(
        {"blocked": "test", "pages": [{"page": 1, "stages": {}}]}), encoding="utf-8")
    assert mod.blocked(d) == "test" and mod.done(d)
    (d / "chapter.json").write_text(json.dumps({"pages": [{"page": 1, "stages": {}}]}),
                                    encoding="utf-8")
    assert mod.blocked(d) is None and not mod.done(d)


def test_an_excluded_page_stays_in_the_archive(home):
    """`original/` is the verbatim record of what the source served.

    Excluding decides what reaches `library/`, and nothing else -- otherwise
    re-running the chapter could not put the page back.
    """
    from comic.qa.visual import publish

    paths, state = _chapter_with_pages("archive-test", 2)
    state.pages[0].excluded = True
    publish(paths, state)

    assert (paths.source_pages / "001.png").exists()


def test_an_excluded_page_is_skipped_by_every_stage(home):
    """It must not reach a gate: the agent would be asked to transcribe a banner."""
    from comic.schemas import Stage
    from comic.stages import _pages_to_process

    _paths, state = _chapter_with_pages("stage-test", 3)
    state.pages[1].excluded = True

    todo = _pages_to_process(state, Stage.OCR, only=None, force=False)
    assert [record.page for record in todo] == [1, 3]

    # --force replays finished work; it does not resurrect a dropped page.
    todo = _pages_to_process(state, Stage.OCR, only=None, force=True)
    assert [record.page for record in todo] == [1, 3]


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


def test_one_ink_colour_by_default():
    """A chapter is lettered in a single colour, with no shading.

    Type that changes colour panel by panel reads as inconsistent even when
    every panel is legible on its own, so the flip is opt-in per series.
    """
    from dataclasses import replace

    from comic.schemas import TextKind
    from comic.typesetting.render import contrasting, style_for

    dark = np.zeros((40, 40, 3), dtype=np.uint8)
    area = np.ones((40, 40), dtype=np.uint8)
    base = style_for(TextKind.DIALOGUE)
    assert contrasting(base, dark, area).colour == base.colour


def test_light_type_on_a_dark_panel_when_the_profile_asks():
    """White-on-black lettering is ordinary in manga.

    The flat fill correctly restores such a bubble to black; drawing the usual
    dark type onto it makes the line vanish -- measured on a real chapter,
    three lines disappeared completely. That is why the behaviour survives as
    ``auto_contrast``, which a paged-manga profile turns on. Webtoons leave it
    off: their text sits in bubbles the cleaner fills pale, and anything over
    dark artwork is drawn-in lettering nobody should be translating.
    """
    from dataclasses import replace

    from comic.schemas import TextKind
    from comic.typesetting.render import contrasting, style_for

    dark = np.zeros((40, 40, 3), dtype=np.uint8)
    area = np.ones((40, 40), dtype=np.uint8)
    style = replace(style_for(TextKind.DIALOGUE), auto_contrast=True)
    got = contrasting(style, dark, area)
    assert min(got.colour) > 180
    assert max(got.stroke_colour) < 60


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


# ------------------------------------------------- ink against the background


def test_ink_is_chosen_by_which_separates_further():
    """A fixed cutoff works on manga and fails in the middle.

    Manga pages are bimodal -- white bubbles or black panels -- so a threshold
    of 110 was never tested between. Webtoons live there: measured on a real
    chapter, six regions sat on backgrounds of luma 111-160 and were given
    dark type, and the two on a night panel at luma 126 and 130 were barely
    legible.
    """
    import numpy as np

    from comic.schemas import TextKind
    from comic.typesetting.render import LIGHT_INK, contrasting, style_for

    from dataclasses import replace

    style = replace(style_for(TextKind.DIALOGUE), auto_contrast=True)
    area = np.ones((8, 8), np.uint8)

    def ink_for(luma: int):
        page = np.full((8, 8, 3), luma, np.uint8)
        return contrasting(style, page, area).colour

    assert ink_for(0) == LIGHT_INK
    assert ink_for(110) == LIGHT_INK
    assert ink_for(126) == LIGHT_INK      # was dark type, and unreadable
    assert ink_for(200) == style.colour
    assert ink_for(255) == style.colour


def test_the_crossover_sits_where_contrast_equalises():
    """Not at an arbitrary constant, so it follows the style's own colours."""
    import numpy as np

    from comic.schemas import TextKind
    from comic.typesetting.render import LIGHT_INK, contrasting, style_for

    from dataclasses import replace

    style = replace(style_for(TextKind.DIALOGUE), auto_contrast=True)
    area = np.ones((8, 8), np.uint8)
    midpoint = (sum(style.colour) / 3 + sum(LIGHT_INK) / 3) / 2

    below = np.full((8, 8, 3), int(midpoint) - 6, np.uint8)
    above = np.full((8, 8, 3), int(midpoint) + 6, np.uint8)
    assert contrasting(style, below, area).colour == LIGHT_INK
    assert contrasting(style, above, area).colour == style.colour


def _boxed_area(width=396, height=200, notches=()):
    """A rectangular caption interior, optionally with stray narrow rows."""
    import numpy as np

    area = np.zeros((height + 80, width + 80), np.uint8)
    area[40:40 + height, 40:40 + width] = 1
    for row, keep in notches:
        area[40 + row, 40 + keep:40 + width] = 0
    return area


def test_a_stray_narrow_row_does_not_collapse_the_font():
    """Mask pinholes must not starve the fitter.

    A line may only sit where its whole line-height band is solid, which keeps
    type off a bubble's corners. Measured on a real caption box: the interior
    was 396px wide on nearly every row, but three rows came back at 200, 206
    and 330 and every candidate block position straddled one of them. The
    fitter fell to 15px inside a box with room for 23px, while the *larger*
    box below it on the same page set at 29px -- type visibly uneven across
    one page.
    """
    from comic.typesetting.fit import fit_text

    fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    face = fonts / "Mali-Regular.ttf"
    if not face.exists():
        pytest.skip("fonts not fetched (scripts/fetch_fonts.py)")

    text = " ".join(["mot hai ba bon nam sau bay tam"] * 6)
    clean = fit_text(text, _boxed_area(), face)
    pitted = fit_text(text, _boxed_area(notches=((30, 206), (110, 330))), face)

    assert clean.font_size > 0
    assert pitted.font_size == clean.font_size, (
        f"pinholes cost {clean.font_size - pitted.font_size}px of type"
    )


def test_closing_never_shrinks_the_type():
    """The fix is only safe if it cannot make any region worse."""
    from comic.typesetting.fit import fit_text

    fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    face = fonts / "Mali-Regular.ttf"
    if not face.exists():
        pytest.skip("fonts not fetched (scripts/fetch_fonts.py)")

    text = "mot hai ba bon nam sau bay tam chin muoi"
    for notches in ((), ((30, 206),), ((30, 206), (110, 330))):
        assert fit_text(text, _boxed_area(notches=notches), face).font_size >= 11


def test_translation_is_capped_near_the_source_lettering_size():
    """A short line in a big bubble must not be shouted just because there is room.

    Before the bubble was found properly, the text box capped the size by
    accident; with the whole bubble available the fitter went to ~2x the source.
    """
    from comic.typesetting.render import SOURCE_BAND_EM, SOURCE_SIZE_HEADROOM, source_size_cap

    mask = np.zeros((500, 700), dtype=np.uint8)
    mask[240:267, 290:410] = 255                       # one 27px line of lettering
    cap = source_size_cap(mask, region("a", (290, 240, 410, 267)))
    assert cap == round(27 / SOURCE_BAND_EM * SOURCE_SIZE_HEADROOM)

    area = np.zeros((500, 700), dtype=np.uint8)
    area[60:440, 60:640] = 1                           # a big bubble
    layout = fit_text("Em thích anh.", area, FONT, max_size=cap)
    assert layout.fits and layout.font_size <= cap


def test_no_mask_means_no_source_cap():
    from comic.typesetting.fit import MAX_FONT_PX
    from comic.typesetting.render import source_size_cap

    assert source_size_cap(None, region("a", (0, 0, 10, 10))) == MAX_FONT_PX


def test_a_capped_line_does_not_strand_one_word_on_its_last_line():
    # A wide, roomy area and a size cap: the widest wrap that fits leaves "nay."
    # alone, and a narrower one fits at the same size without doing so.
    area = np.zeros((400, 380), dtype=np.uint8)
    area[40:360, 40:340] = 1
    text = "Lee Jiun sẽ là trợ lý mới của tôi từ hôm nay."
    layout = fit_text(text, area, FONT, max_size=30)
    assert layout.font_size == 30
    assert len(layout.lines) >= 2 and len(layout.lines[-1].split()) > 1


def test_avoiding_a_widow_never_costs_type_size():
    from comic.typesetting.fit import _widowed

    assert _widowed(["Cậu dễ thương", "ghê."])
    assert not _widowed(["Cậu dễ", "thương ghê."])
    assert not _widowed(["Và..."])
    area = np.zeros((120, 300), dtype=np.uint8)
    area[10:110, 10:290] = 1
    text = "Cậu dễ thương ghê."
    free = fit_text(text, area, FONT)
    assert free.fits and free.font_size >= 12


def test_spare_room_puts_the_translation_where_the_source_sat():
    # A bubble (bottom) whose area leaked into a wide open page above it, as a
    # dashed outline lets happen. The widest, slackest rows are in the leak;
    # the source lettering was in the bubble.
    area = np.zeros((500, 600), dtype=np.uint8)
    area[0:250, 0:600] = 1          # leaked page
    area[250:450, 150:450] = 1      # the bubble
    source = (200, 300, 400, 400)
    layout = fit_text("Chắc em cũng không đi đâu.", area, FONT, max_size=24, anchor=source)
    centre_y = layout.y + layout.height / 2
    assert source[1] <= centre_y <= source[3]
    assert layout.font_size == 24


def test_an_off_centre_source_still_gets_a_centred_translation():
    # A round bubble whose English sat up and to the left (the operator,
    # 2026-10-15: off-centre text is the thing readers notice).
    import cv2

    area = np.zeros((400, 400), dtype=np.uint8)
    cv2.ellipse(area, (200, 200), (150, 120), 0, 0, 360, 1, -1)
    area[310:380, 190:210] = 1                       # the tail
    source = (90, 110, 230, 190)
    layout = fit_text("Chắc chắn là cậu ấy.", area, FONT, max_size=24, anchor=source)
    assert abs(layout.x + layout.width / 2 - 200) <= 6
    assert abs(layout.y + layout.height / 2 - 200) <= 8


def test_an_anchor_that_would_cost_type_size_is_ignored():
    # Only a sliver of room around the source box; the free layout is much larger.
    area = np.zeros((400, 600), dtype=np.uint8)
    area[0:300, 0:600] = 1
    area[300:330, 280:320] = 1
    source = (285, 305, 315, 325)
    free = fit_text("Chào cậu", area, FONT)
    anchored = fit_text("Chào cậu", area, FONT, anchor=source)
    assert anchored.font_size == free.font_size


def test_stray_source_punctuation_inside_a_bubble_is_taken_into_the_interior():
    import cv2

    image = np.full((200, 300, 3), 120, dtype=np.uint8)
    cv2.ellipse(image, (150, 100), (120, 70), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (150, 100), (120, 70), 0, 0, 360, (0, 0, 0), 2)
    image[95:105, 110:150] = 0                 # the lettering, masked
    image[98:102, 170:174] = 0                 # a stray period the mask missed
    cv2.circle(image, (150, 60), 9, (0, 0, 0), -1)   # a drawing in the bubble
    page_mask = np.zeros((200, 300), dtype=np.uint8)
    page_mask[93:107, 108:152] = 255
    interior = bubble_interior(image, page_mask, BBox(x1=108, y1=93, x2=152, y2=107))
    assert interior is not None
    assert interior[100, 172]                  # speck: erased with the bubble
    assert not interior[60, 150]               # drawing: left alone


def test_a_font_delta_keeps_hand_broken_lines():
    area = np.zeros((300, 400), dtype=np.uint8)
    area[20:280, 20:380] = 1
    lines = ["Em cũng không", "biết phải nói", "với anh"]
    plain = fit_text("x", area, FONT, forced_lines=lines)
    smaller = fit_text("x", area, FONT, forced_lines=lines, font_delta=-3)
    assert smaller.lines == tuple(lines)
    assert smaller.font_size == plain.font_size - 3


def test_a_separate_ellipsis_elsewhere_in_the_bubble_is_left_alone():
    import cv2

    image = np.full((300, 300, 3), 120, dtype=np.uint8)
    cv2.ellipse(image, (150, 150), (130, 130), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (150, 150), (130, 130), 0, 0, 360, (0, 0, 0), 2)
    image[195:205, 110:190] = 0                  # the region's lettering
    for x in (140, 150, 160):                    # a lone "..." on a line above it
        image[178:182, x:x + 4] = 0
    page_mask = np.zeros((300, 300), dtype=np.uint8)
    page_mask[193:207, 108:192] = 255
    interior = bubble_interior(image, page_mask, BBox(x1=108, y1=193, x2=192, y2=207))
    assert interior is not None
    assert not interior[180, 152]


def test_inset_keeps_type_off_every_edge_of_the_bubble():
    # Reader feedback: a full bubble set its first and last lines against the
    # outline, because PADDING_EM only narrows lines. `inset` margins all sides.
    import cv2

    area = np.zeros((300, 420), np.uint8)
    cv2.ellipse(area, (210, 150), (166, 101), 0, 0, 360, 1, -1)
    text = "Hai người đi dạo xa cỡ nào vậy?"
    plain = fit_text(text, area, FONT, max_size=60)
    inset = fit_text(text, area, FONT, max_size=60, inset=0.10)
    margin = int(round(202 * 0.10))
    assert inset.y >= 150 - 101 + margin
    assert inset.y + inset.height <= 150 + 101 - margin
    assert inset.font_size <= plain.font_size


def test_inset_never_erases_a_small_area():
    area = np.zeros((40, 40), np.uint8)
    area[10:16, 5:35] = 1
    assert fit_text("ừ", area, FONT, inset=0.9).lines


def test_pad_em_keeps_a_margin_above_and_below_the_block():
    # Piekos: the space inside a balloon is about one capital O. The built-in
    # padding only narrowed lines, so a full box set text on its top edge.
    area = np.zeros((120, 400), np.uint8)
    area[10:110, 10:390] = 1
    text = "một hai ba bốn năm sáu bảy tám chín mười một hai ba bốn năm sáu"
    plain = fit_text(text, area, FONT, max_size=60)
    padded = fit_text(text, area, FONT, max_size=60, pad_em=0.6)
    pad = int(round(padded.font_size * 0.6))
    assert padded.y >= 10 + pad - 1
    assert padded.y + padded.height <= 110 - pad + 1
    assert padded.x >= 10 + pad - 1
    assert padded.font_size <= plain.font_size


def test_missing_punctuation_is_substituted_not_tofu():
    from comic.typesetting.coverage import check_font
    from comic.typesetting.render import printable

    ace = Path("assets/fonts/000AnimeAce3-TeddyBear.ttf")
    if not ace.exists():
        pytest.skip("AnimeAce3 not installed")
    assert check_font(ace).ok
    assert printable("Khoan — đã «nhé»", ace) == 'Khoan - đã "nhé"'
    # A face that has the dash keeps it.
    assert printable("Khoan — đã", Path(FONT)) == "Khoan — đã"


def test_voices_load_from_the_profile_and_win_over_the_kind(tmp_path):
    from comic.schemas import ChapterTranslation, RegionOverride, RenderPlan, TranslationEntry
    from comic.typesetting.render import layout_region, load_styles, voice_uses

    yaml_path = tmp_path / "fonts.yaml"
    yaml_path.write_text(
        "pad_em: 0.5\n"
        "styles:\n  dialogue: {font: BeVietnamPro-Regular.ttf, uppercase: true}\n"
        "voices:\n  stammer: {font: BeVietnamPro-Italic.ttf, use: \"hesitant, trailing off\"}\n",
        encoding="utf-8",
    )
    styles = load_styles(yaml_path)
    assert voice_uses(yaml_path) == {"stammer": "hesitant, trailing off"}
    # A voice inherits the dialogue style (case) and the shared spacing.
    assert styles["stammer"].font == "BeVietnamPro-Italic.ttf"
    assert styles["stammer"].uppercase and styles["stammer"].pad_em == 0.5

    region = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=10, y1=10, x2=190, y2=90),
                        kind=TextKind.DIALOGUE)
    area = np.zeros((100, 200), np.uint8)
    area[5:95, 5:195] = 1
    _, plain = layout_region(region, "ừ thì", area, Path("assets/fonts"), None, styles)
    _, voiced = layout_region(region, "ừ thì", area, Path("assets/fonts"),
                              RegionOverride(voice="stammer"), styles)
    assert plain.font.startswith("BeVietnamPro-Regular") and voiced.font == "BeVietnamPro-Italic.ttf"

    # A translation-time voice is folded in for rendering; a QA restyle wins.
    translation = ChapterTranslation(series="s", chapter="c", entries=[
        TranslationEntry(region_id="p001_r01", source_text="", target_text="ừ", voice="stammer"),
        TranslationEntry(region_id="p001_r02", source_text="", target_text="à", voice="stammer"),
    ])
    plan = RenderPlan(series="s", chapter="c")
    plan.edit("p001_r02").voice = "other"
    voiced_plan = plan.with_voices(translation)
    assert voiced_plan.for_region("p001_r01").voice == "stammer"
    assert voiced_plan.for_region("p001_r02").voice == "other"
    assert plan.for_region("p001_r01").voice is None  # the saved plan is untouched


def test_a_voice_may_not_shadow_a_text_kind(tmp_path):
    from comic.typesetting.render import load_styles

    yaml_path = tmp_path / "fonts.yaml"
    yaml_path.write_text("voices:\n  shout: {font: Anton-Regular.ttf}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="shadows"):
        load_styles(yaml_path)


def test_a_no_break_space_keeps_a_word_on_one_line():
    from comic.typesetting.fit import NBSP

    font = load_font(FONT, 20)
    width = font.getbbox("chúng ta đi")[2] + 2
    free = wrap("chúng ta đi hôm nay", font, width)
    bound = wrap(f"chúng ta đi hôm{NBSP}nay", font, width)
    assert free == ["chúng ta đi", "hôm nay"] or "hôm" in free[-2]
    assert any("hôm nay" in line for line in bound)
    assert all(NBSP not in line for line in bound)


def test_a_caption_box_gets_balanced_lines_not_a_long_line_and_a_stub():
    # Piekos ch. 6: break caption text "in the most rectangular shape you can",
    # minimising the gap after each line. Among wraps that fit at one size, the
    # one whose emptiest line fills most of its row wins.
    from comic.typesetting.fit import _try_size

    area = np.zeros((200, 520), bool)
    area[20:180, 20:500] = True
    text = "ngày hôm ấy trời mưa rất to và tôi đã chờ anh ở đó"
    font = load_font(FONT, 26)
    layout = _try_size(area, text, FONT, 26)
    assert layout is not None and len(layout.lines) >= 2
    widths = [font.getbbox(line)[2] for line in layout.lines]
    assert min(widths) >= 0.5 * max(widths)


def test_a_voice_missing_a_letter_falls_back_for_that_line_only(tmp_path, monkeypatch):
    import comic.typesetting.render as render
    from comic.schemas import RegionOverride

    italic = "BeVietnamPro-Italic.ttf"
    real = render._charset

    def lacking(path):  # pretend the voice face has no "ỉ"/"Ỉ"
        chars = real(path)
        return chars - {ord("ỉ"), ord("Ỉ")} if path.endswith(italic) else chars

    monkeypatch.setattr(render, "_charset", lacking)
    yaml_path = tmp_path / "fonts.yaml"
    yaml_path.write_text("voices:\n  soft: {font: " + italic + "}\n", encoding="utf-8")
    styles = render.load_styles(yaml_path)
    region = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=10, y1=10, x2=190, y2=90),
                        kind=TextKind.DIALOGUE)
    area = np.zeros((100, 200), np.uint8)
    area[5:95, 5:195] = 1
    fonts = Path("assets/fonts")
    _, kept = render.layout_region(region, "đến rồi", area, fonts, RegionOverride(voice="soft"), styles)
    _, fell = render.layout_region(region, "chỉ thế", area, fonts, RegionOverride(voice="soft"), styles)
    assert kept.font == italic
    assert fell.font != italic


def test_a_voice_keeps_its_own_size_scale_over_the_shared_one(tmp_path):
    from comic.typesetting.render import load_styles

    yaml_path = tmp_path / "fonts.yaml"
    yaml_path.write_text("size_scale: 0.9\nleading: auto\n"
                         "voices:\n  whisper: {font: BeVietnamPro-Regular.ttf, size_scale: 0.68}\n",
                         encoding="utf-8")
    styles = load_styles(yaml_path)
    assert styles["whisper"].size_scale == 0.68
    assert styles[TextKind.DIALOGUE].size_scale == 0.9
    assert styles["whisper"].auto_leading


def test_auto_leading_clears_the_accents_of_a_tall_face():
    from comic.typesetting.fit import LINE_HEIGHT_EM, leading_em

    assert leading_em("assets/fonts/Bangers-Regular.ttf") > 1.5
    area = np.zeros((300, 300), np.uint8)
    area[10:290, 10:290] = 1
    text = "ỖN ĐỘI ỄNH ỮNG ỘC ẬY ỖN ĐỘI"
    fixed = fit_text(text, area, "assets/fonts/Bangers-Regular.ttf", max_size=30)
    auto = fit_text(text, area, "assets/fonts/Bangers-Regular.ttf", max_size=30, auto_leading=True)
    assert fixed.line_em == LINE_HEIGHT_EM
    assert auto.line_height > fixed.line_height * 1.2 or auto.font_size < fixed.font_size


def test_a_series_inherits_its_format_preset_and_the_mood_library(tmp_path):
    from comic.typesetting.render import load_styles, read_profile, voice_uses

    presets = tmp_path / "_presets"
    presets.mkdir()
    (presets / "voices.yaml").write_text(
        "voices:\n  soft: {font: BeVietnamPro-Italic.ttf, use: gentle}\n"
        "  angry: {font: Anton-Regular.ttf, use: yelling}\n", encoding="utf-8")
    (presets / "manhwa.yaml").write_text(
        "preset: voices\npad_em: 0.6\nstyles:\n  dialogue: {font: BeVietnamPro-Regular.ttf, uppercase: true}\n",
        encoding="utf-8")
    series = tmp_path / "Some Series"
    series.mkdir()
    (series / "fonts.yaml").write_text(
        "preset: manhwa\nvoices:\n  angry: {font: Bangers-Regular.ttf, use: furious}\n", encoding="utf-8")

    raw = read_profile(series / "fonts.yaml")
    assert raw["pad_em"] == 0.6
    assert voice_uses(series / "fonts.yaml") == {"soft": "gentle", "angry": "furious"}
    styles = load_styles(series / "fonts.yaml")
    assert styles["angry"].font == "Bangers-Regular.ttf"   # the series overrides one voice
    assert styles["soft"].uppercase                         # voices inherit the preset's case


def test_the_shipped_presets_load():
    from comic.typesetting.render import load_styles, voice_uses

    for name in ("manga", "manhwa", "manhua", "western"):
        path = Path("profiles/_presets") / f"{name}.yaml"
        styles = load_styles(path)
        assert len(voice_uses(path)) >= 6
        for style in styles.values():
            assert (Path("assets/fonts") / style.font).exists(), (name, style.font)


def test_a_restyle_to_a_kind_beats_the_translation_voice():
    from comic.schemas import ChapterTranslation, RenderPlan, TextKind, TranslationEntry

    translation = ChapterTranslation(series="s", chapter="c", entries=[
        TranslationEntry(region_id="p001_r01", source_text="", target_text="ừ", voice="whisper")])
    plan = RenderPlan(series="s", chapter="c")
    plan.edit("p001_r01").style_kind = TextKind.DIALOGUE
    assert plan.with_voices(translation).for_region("p001_r01").voice is None


def test_an_aside_on_the_art_follows_its_source_case(tmp_path):
    from comic.typesetting.render import layout_region, load_styles

    yaml_path = tmp_path / "fonts.yaml"
    yaml_path.write_text("sentence_font: Mali-Regular.ttf\n"
                         "styles:\n  dialogue: {font: 000AnimeAce3-TeddyBear.ttf, uppercase: true}\n",
                         encoding="utf-8")
    styles = load_styles(yaml_path)
    box = BBox(x1=10, y1=10, x2=110, y2=40)
    own = np.zeros((100, 200), np.uint8)
    own[10:40, 10:110] = 1                       # no bubble: the text's own box
    bubble = np.zeros((100, 200), np.uint8)
    bubble[2:98, 2:198] = 1
    aside = TextRegion(id="p001_r01", page=1, bbox=box, kind=TextKind.DIALOGUE, source_text="Yeah yeah.")
    spoken = TextRegion(id="p001_r02", page=1, bbox=box, kind=TextKind.DIALOGUE, source_text="YEAH YEAH.")
    fonts = Path("assets/fonts")
    layout, style = layout_region(aside, "rồi rồi", own, fonts, None, styles)
    assert not style.uppercase and style.font == "Mali-Regular.ttf" and layout.lines[0] == "rồi rồi"
    _, style = layout_region(spoken, "rồi rồi", own, fonts, None, styles)
    assert style.uppercase                       # source was caps
    _, style = layout_region(aside, "rồi rồi", bubble, fonts, None, styles)
    assert style.uppercase                       # inside a bubble: caps


def test_white_lettering_on_a_dark_box_gets_the_whole_box():
    # bubble_interior grows from light pixels; on a black caption those are
    # the letters, and the area came back letter-shaped (Super Lovers ch.0).
    image = np.full((200, 300, 3), 30, np.uint8)
    mask = np.zeros((200, 300), np.uint8)
    for x in range(60, 240, 30):                 # white "letters"
        image[80:120, x:x + 12] = 245
        mask[80:120, x:x + 12] = 255
    region = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=50, y1=70, x2=250, y2=130),
                        kind=TextKind.CAPTION, source_text="NO WAY.")
    area = fit_areas(image, mask, [region])["p001_r01"]
    assert area[70:130, 50:250].all()


def test_cleaning_keeps_the_screentone_and_shading_of_a_bubble():
    # Erase the letters only. Filling the whole bubble wiped dot tone and
    # left "a white frame ... sometimes with polka dots" (Super Lovers ch.0).
    image = np.full((200, 300, 3), 250, np.uint8)
    yy, xx = np.mgrid[0:200, 0:300]
    dots = ((yy % 8 < 2) & (xx % 8 < 2))
    image[dots] = 120                                # screentone inside the bubble
    mask = np.zeros((200, 300), np.uint8)
    mask[90:110, 100:200] = 255
    image[mask > 0] = 15                             # the lettering
    region = TextRegion(id="r", page=1, bbox=BBox(x1=95, y1=85, x2=205, y2=115),
                        kind=TextKind.DIALOGUE, source_text="HELLO")
    area = np.zeros((200, 300), np.uint8)
    area[40:160, 40:260] = 1                         # the whole bubble
    cleaned, touched, _ = clean_page(image, mask, [region], {"r": area})
    far = (area > 0) & (touched == 0)
    assert far.any() and np.array_equal(cleaned[far], image[far])   # dots kept
    assert touched.sum() < 0.3 * area.sum()                         # letters only


def test_plain_white_letters_take_the_exact_colour_behind_them():
    image = np.full((100, 200, 3), 253, np.uint8)
    mask = np.zeros((100, 200), np.uint8)
    mask[40:60, 50:150] = 255
    image[mask > 0] = 10
    region = TextRegion(id="r", page=1, bbox=BBox(x1=45, y1=35, x2=155, y2=65),
                        kind=TextKind.DIALOGUE, source_text="HELLO")
    area = np.zeros((100, 200), np.uint8)
    area[10:90, 10:190] = 1
    cleaned, _, report = clean_page(image, mask, [region], {"r": area})
    assert report.tiers["r"] == "flat"
    assert (cleaned[40:60, 50:150] == 253).all()


def test_type_over_screentone_gets_a_halo_and_plain_bubbles_do_not():
    from comic.typesetting.render import Style, haloed

    area = np.zeros((100, 100), np.uint8)
    area[10:90, 10:90] = 1
    plain = np.full((100, 100, 3), 250, np.uint8)
    dotted = plain.copy()
    dotted[::4, ::4] = 60
    style = Style("BeVietnamPro-Regular.ttf")
    assert haloed(style, plain, area).stroke_em == 0
    assert haloed(style, dotted, area).stroke_em > 0


def test_a_screentoned_caption_box_is_found_whole():
    # A dotted grey box gives the light-bubble search nothing to grow from.
    image = np.full((300, 300, 3), 255, np.uint8)
    yy, xx = np.mgrid[0:300, 0:300]
    box = (yy >= 50) & (yy < 250) & (xx >= 50) & (xx < 250)
    image[box & (yy % 4 < 2) & (xx % 4 < 2)] = 90      # dot tone inside the box
    image[48:50, 48:252] = 0; image[250:252, 48:252] = 0   # drawn border
    image[48:252, 48:50] = 0; image[48:252, 250:252] = 0
    mask = np.zeros((300, 300), np.uint8)
    mask[140:160, 120:180] = 255
    image[mask > 0] = 10
    region = TextRegion(id="r", page=1, bbox=BBox(x1=115, y1=135, x2=185, y2=165),
                        kind=TextKind.CAPTION, source_text="ALL THE TIME.")
    area = fit_areas(image, mask, [region])["r"]
    assert area[60:240, 60:240].mean() > 0.9
    assert not area[10:40, 10:290].any()


def test_a_short_line_in_a_roomy_balloon_grows_but_keeps_its_margin():
    from comic.typesetting.render import MAX_FILL, layout_region, load_styles
    import cv2

    area = np.zeros((300, 400), np.uint8)
    cv2.ellipse(area, (200, 150), (180, 130), 0, 0, 360, 1, -1)
    region = TextRegion(id="r", page=1, bbox=BBox(x1=170, y1=135, x2=230, y2=165),
                        kind=TextKind.DIALOGUE, source_text="OK.")
    styles = load_styles(Path("profiles/_presets/manhwa.yaml"))
    small, _ = layout_region(region, "được rồi", area, Path("assets/fonts"), None, styles, max_size=20)
    assert small.font_size > 20                                   # grew with the room
    assert small.width * small.height <= MAX_FILL * area.sum()    # but not to the outline


def test_a_model_bubble_limits_a_leaked_fill_and_replaces_a_failed_one():
    """Kimi ch.1, the operator 2026-10-28: dashed thought bubbles leaked
    (p.8 r07: 280x363 px for a ~130 px bubble) or found nothing (r04, r05:
    squeezed into the text box). The segmentation model's bubble clips the
    leak and stands in for the failed fill; a clean fill is left alone."""
    from comic.image_processing.mask import _limit_by_bubble

    shape = (200, 200)
    box = BBox(x1=90, y1=90, x2=110, y2=110)
    bubble = np.zeros(shape, bool)
    bubble[60:140, 60:140] = True
    leaked = np.zeros(shape, np.uint8)
    leaked[20:180, 20:180] = 1
    clipped = _limit_by_bubble(leaked, False, bubble, box)
    assert clipped[100, 100] and not clipped[25, 25]

    own_box = np.zeros(shape, np.uint8)
    own_box[90:110, 90:110] = 1
    filled = _limit_by_bubble(own_box, True, bubble, box)
    assert filled.sum() > own_box.sum() * 5 and not filled[61, 61]   # shrunk off the outline

    exact = np.zeros(shape, np.uint8)
    exact[62:138, 62:138] = 1
    assert _limit_by_bubble(exact, False, bubble, box) is exact


def test_an_erase_region_clears_its_whole_box():
    """A group's picture logo has no glyphs for the detector to mark."""
    from comic.image_processing.mask import with_erase_boxes

    page_mask = np.zeros((50, 50), np.uint8)
    logo = TextRegion(id="p001_r90", page=1, bbox=BBox(x1=5, y1=5, x2=25, y2=20),
                      kind=TextKind.ERASE, source_text="[erase]")
    line = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=30, y1=30, x2=45, y2=45),
                      kind=TextKind.DIALOGUE, source_text="x")
    out = with_erase_boxes(page_mask, [logo, line])
    assert out[5:20, 5:25].min() == 255 and out[30:45, 30:45].max() == 0
    assert page_mask.max() == 0                      # the original is not modified


def test_a_studio_watermark_with_no_reading_is_still_erased():
    """Kawaii ch.4 p.2 (the operator, 2026-11-12): watermark boxes drawn in
    Studio carry no OCR text, and cleaning skipped every one of them -- the
    jmcomic and mw123.cc marks survived. The label is what says erase."""
    from comic.image_processing.clean import clean_page
    from comic.image_processing.mask import fit_areas, with_erase_boxes

    image = np.full((60, 80, 3), 255, np.uint8)
    image[40:50, 10:40] = 90                         # faint grey site line
    page_mask = np.zeros((60, 80), np.uint8)         # the detector saw nothing
    mark = TextRegion(id="p001_r11", page=1, bbox=BBox(x1=8, y1=38, x2=42, y2=52),
                      kind=TextKind.ERASE, tag="watermark", source_text="", edited=True)
    page_mask = with_erase_boxes(page_mask, [mark])
    areas = fit_areas(image, page_mask, [mark])
    cleaned, touched, _ = clean_page(image, page_mask, [mark], areas)
    assert touched[40:50, 10:40].all()
    assert cleaned[40:50, 10:40].min() > 200


def test_an_sfx_marked_for_lama_is_erased_whole_and_never_flat_filled():
    """Studio key L (the operator, 2026-11-14): the box goes to LaMa like a
    group mark -- whole, a little wider -- even on a plain background where a
    translated line would get a flat fill."""
    from comic.image_processing.clean import clean_page
    from comic.image_processing.mask import fit_areas, with_erase_boxes

    image = np.full((60, 80, 3), 255, np.uint8)
    image[20:40, 15:60] = 30                          # a heavy drawn SFX
    page_mask = np.zeros((60, 80), np.uint8)
    sfx = TextRegion(id="p001_r05", page=1, bbox=BBox(x1=12, y1=18, x2=62, y2=42),
                     kind=TextKind.SFX, tag="lama", source_text="BAM")
    plain = sfx.model_copy(update={"id": "p001_r06", "tag": None,
                                   "bbox": BBox(x1=0, y1=50, x2=20, y2=58)})
    page_mask = with_erase_boxes(page_mask, [sfx, plain])
    assert not page_mask.any()                        # the mask keeps the letters' own shape
    areas = fit_areas(image, page_mask, [sfx, plain])
    cleaned, touched, report = clean_page(image, page_mask, [sfx, plain], areas)
    assert touched[18:42, 12:62].all()                # the whole box, not the strokes
    assert cleaned[20:40, 15:60].min() > 200
    assert report.tiers.get("p001_r05") != "flat"      # LaMa, never a flat fill
    assert "p001_r06" not in report.tiers             # a plain SFX is still Photoshop's


def test_a_coloured_logo_does_not_make_a_grey_page_colour():
    """Ku Ku ch.1.1 p.2 (the operator, 2026-11-12): a blue group logo on a
    black-and-white page made the per-spot test see colour, so LaMa's colour
    repair was kept and the logo came back as a blue smear."""
    from comic.image_processing.clean import page_is_grey

    page = np.full((200, 200, 3), 128, np.uint8)
    page[40:120, 120:180] = (90, 160, 230)              # the glowing logo
    logo = TextRegion(id="p002_r09", page=2, bbox=BBox(x1=125, y1=45, x2=175, y2=115),
                      kind=TextKind.ERASE, tag="logo")
    assert page_is_grey(page, [logo])
    assert not page_is_grey(page, [])


def test_a_mark_hole_takes_its_glow_and_edge_holes_get_mirrored_context():
    """Ku Ku ch.1.1 p.2: the glow reached past the drawn box and came back as a
    blue rim. Kawaii ch.4 p.2: a watermark at the page edge left LaMa no
    context on that side, and it streaked."""
    from comic.image_processing.clean import mark_hole, pad_edges, unpad

    page = np.full((200, 200, 3), 128, np.uint8)
    page[30:130, 110:190] = (90, 160, 230)                  # glow, wider than the box
    logo = TextRegion(id="p002_r09", page=2, bbox=BBox(x1=130, y1=50, x2=170, y2=110),
                      kind=TextKind.ERASE, tag="logo")
    hole = mark_hole(page, logo, grey=True)
    assert hole[30:130, 110:190].all()                      # the whole glow
    assert not hole[150:, :100].any()                       # nothing else
    assert mark_hole(page, logo, grey=False)[30:45, 110:125].sum() == 0

    patch = np.arange(60 * 80 * 3, dtype=np.uint8).reshape(60, 80, 3)
    hidden = np.zeros((60, 80), np.uint8)
    hidden[40:, 10:30] = 1                                  # touches the bottom edge
    padded, mask, pads = pad_edges(patch, hidden)
    assert pads[1] > 0 and pads[0] == pads[2] == pads[3] == 0
    assert padded.shape[0] == 60 + pads[1] and mask.shape == padded.shape[:2]
    assert (unpad(padded, pads) == patch).all()


def test_an_empty_reading_still_means_leave_it_unless_the_operator_drew_it():
    """"" at ocr_review drops a detector box (front matter stays as drawn);
    a box drawn in Studio was drawn because there is lettering in it."""
    dropped = TextRegion(id="p001_r01", page=1, bbox=BBox(x1=0, y1=0, x2=9, y2=9),
                         kind=TextKind.DIALOGUE, source_text="")
    drawn = dropped.model_copy(update={"id": "p001_r02", "edited": True})
    kept = dropped.model_copy(update={"id": "p001_r03", "kind": TextKind.KEEP, "edited": True})
    assert not dropped.cleanable and drawn.cleanable and not kept.cleanable


def test_a_column_of_lettering_is_not_taken_for_an_outline():
    """Kimi ch.1 p.20: '当然可以' was one large mask component beside small
    '!' specks and was dropped as an 'outline', so the line stayed under tiny
    type. An outline is a thin ring around its hull; lettering fills it."""
    import cv2

    from comic.image_processing.mask import _ring_like

    ring = np.zeros((100, 100), np.uint8)
    cv2.rectangle(ring, (5, 5), (94, 94), 1, 2)
    hull = np.zeros_like(ring)
    hull[5:95, 5:95] = 1
    assert _ring_like(ring, hull)

    column = np.zeros_like(ring)
    column[10:90, 40:60] = 1
    col_hull = np.zeros_like(ring)
    col_hull[10:90, 40:60] = 1
    assert not _ring_like(column, col_hull)


def test_an_erase_region_is_never_typeset_even_with_a_translation():
    from comic.typesetting.render import layout_page

    image, mask = bubble_page()
    regions = [region("a", (60, 66, 140, 82)),
               region("b", (60, 106, 140, 122), kind=TextKind.ERASE)]
    areas = fit_areas(image, mask, regions)
    out = layout_page(image, regions, {"a": "XIN CHÀO", "b": "STALE"},
                      areas, Path("assets/fonts"), page_mask=mask)
    assert [r.id for r, _l, _s in out] == ["a"]


def test_a_narrow_vertical_balloon_keeps_only_a_thin_margin():
    """Kawaii ch.2 p.13: a 66 px wide vertical balloon lost a third of its
    width to the one-O margin and set "À NÀY, TOKI" at 14 px."""
    area = np.zeros((140, 120), np.uint8)
    area[20:120, 30:96] = 1                     # 66 x 100, a vertical balloon
    text = "À NÀY, TOKI"
    full = fit_text(text, area, FONT, max_size=40, pad_em=None)
    padded = fit_text(text, area, FONT, max_size=40, pad_em=0.6)
    assert padded.font_size >= full.font_size - 2


def test_a_tall_narrow_balloon_condenses_the_line_and_a_wide_one_does_not():
    from comic.typesetting.render import Style, layout_region

    style = Style("BeVietnamPro-Regular.ttf", uppercase=True, condense=0.8)
    tall = np.zeros((220, 160), np.uint8)
    tall[20:200, 50:110] = 1                      # 60 x 180
    narrow, _ = layout_region(region("a", (60, 40, 100, 180)), "TÔI THÍCH TRUYỆN THIẾU NỮ",
                              tall, Path("assets/fonts"), styles={TextKind.DIALOGUE: style})
    plain, _ = layout_region(region("a", (60, 40, 100, 180)), "TÔI THÍCH TRUYỆN THIẾU NỮ",
                             tall, Path("assets/fonts"),
                             styles={TextKind.DIALOGUE: Style("BeVietnamPro-Regular.ttf", uppercase=True)})
    assert narrow.hscale < 1.0 and narrow.font_size > plain.font_size
    assert 50 <= narrow.x and narrow.x + narrow.width <= 110

    wide = np.zeros((200, 400), np.uint8)
    wide[20:180, 20:380] = 1
    roomy, _ = layout_region(region("b", (150, 80, 250, 120)), "XIN CHÀO",
                             wide, Path("assets/fonts"), styles={TextKind.DIALOGUE: style})
    assert roomy.hscale == 1.0


def test_a_condensed_block_is_drawn_inside_its_layout():
    from PIL import Image
    from comic.typesetting.fit import Layout
    from comic.typesetting.render import Style, draw_layout

    full = load_font(FONT, 30).getbbox("XIN CHÀO")
    natural = full[2] - full[0]
    width = int(natural * 0.8) + 4
    page = Image.new("RGB", (300, 100), "white")
    lay = Layout(("XIN CHÀO",), 30, 40, 20, width, 40, True, hscale=0.8)
    draw_layout(page, lay, Style("BeVietnamPro-Regular.ttf"), Path("assets/fonts"))
    ink = np.array(page.convert("L")) < 128
    cols = np.flatnonzero(ink.any(axis=0))
    assert cols.size and cols[0] >= 38 and cols[-1] <= 40 + width + 2
    assert abs((cols[-1] - cols[0]) - natural * 0.8) <= 4


def test_bubble_masks_are_cached_on_disk(tmp_path, monkeypatch):
    """The bubble model costs ~8 s a page on the CPU (2026-11-13): masks made once
    -- here or on the T4 -- are read back for every later stage and run."""
    from comic.vision import bubbles

    masks = [np.zeros((40, 30), bool), np.zeros((40, 30), bool)]
    masks[0][5:10, 3:7] = True
    masks[1][20:35, 10:29] = True
    f = tmp_path / "k.npz"
    bubbles.save_masks(f, masks, (40, 30, 3))
    back = bubbles.load_masks(f)
    assert len(back) == 2 and all((a == b).all() for a, b in zip(masks, back))
    bubbles.save_masks(f, [], (40, 30, 3))
    assert bubbles.load_masks(f) == []

    image = np.full((40, 30, 3), 200, np.uint8)
    monkeypatch.setattr(bubbles, "disk_cache", lambda: tmp_path)
    monkeypatch.setattr(bubbles, "model_path", lambda: tmp_path / "model.onnx")
    (tmp_path / "model.onnx").write_bytes(b"x")
    bubbles._CACHE.clear()
    bubbles.save_masks(tmp_path / f"{bubbles.page_key(image)}.npz", masks, image.shape)
    monkeypatch.setattr(bubbles, "_detect", lambda *a: (_ for _ in ()).throw(AssertionError("model ran")))
    assert len(bubbles.bubble_masks(image)) == 2


def test_a_round_balloon_gets_a_stack_shaped_like_the_balloon():
    # The operator, 2026-11-15: shape the text block to follow the bubble's
    # contour -- short lines where an oval is narrow, the longest in the middle.
    # Measured: one wrap width gave 28px in a rectangle-ish 322/316/244 stack;
    # the contour wrap sets 31px as 195/286/309/192.
    from comic.typesetting.fit import fit_text

    yy, xx = np.mgrid[0:260, 0:420]
    area = ((xx - 210) / 200) ** 2 + ((yy - 130) / 120) ** 2 <= 1
    text = "tôi đã nói với cậu rồi mà sao cậu vẫn không chịu nghe lời tôi vậy hả"
    layout = fit_text(text, area, FONT, max_size=80, pad_em=0.6)
    assert layout.fits and len(layout.lines) >= 3
    font = load_font(FONT, layout.font_size)
    widths = [font.getbbox(line)[2] for line in layout.lines]
    middle = max(widths[1:-1])
    assert widths[0] < middle and widths[-1] < middle
