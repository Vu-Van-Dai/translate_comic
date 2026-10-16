"""The Photoshop route's planning -- everything it decides before Photoshop runs.

None of this needs Photoshop: the plan is plain data, and these tests pin it to
the pipeline it must agree with.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from comic.image_processing.mask import fit_areas
from comic.photoshop.fonts import font_info
from comic.photoshop.plan import (
    PagePlan,
    baselines_of,
    inpainted_glyphs,
    leading_of,
    page_layers,
)
from comic.schemas import RegionOverride
from comic.typesetting.fit import Layout, load_font
from comic.typesetting.render import STYLES, render_page
from tests.test_render import bubble_page, region

FONTS = Path("assets/fonts")
FONT = FONTS / "BeVietnamPro-Regular.ttf"


def test_text_layers_use_exactly_the_layouts_the_pipeline_renders():
    # The Photoshop route must not make its own typesetting decisions: same
    # page, same text, same size, wrap and block position as render_page.
    image, mask = bubble_page()
    regions = [region("r", (60, 70, 140, 118))]
    text = {"r": "Anh thích em nhiều lắm, thật đấy."}
    areas = fit_areas(image, mask, regions)
    _rendered, layouts = render_page(image, regions, text, areas, FONTS,
                                     {}, STYLES, page_mask=mask)
    layers = page_layers(image, mask, regions, text, areas, FONTS, {}, STYLES)
    assert len(layers) == 1
    layout, layer = layouts["r"], layers[0]
    assert layer.lines == list(layout.lines)
    assert layer.size == layout.font_size
    assert layer.x == layout.x + layout.width / 2


def test_overrides_reach_the_photoshop_plan():
    image, mask = bubble_page()
    regions = [region("r", (60, 70, 140, 118))]
    text = {"r": "Chào cậu"}
    areas = fit_areas(image, mask, regions)
    plain = page_layers(image, mask, regions, text, areas, FONTS, {}, STYLES)[0]
    smaller = page_layers(image, mask, regions, text, areas, FONTS,
                          {"r": RegionOverride(font_delta=-4)}, STYLES)[0]
    assert smaller.size < plain.size


def test_first_baseline_is_where_pil_draws_it():
    from comic.typesetting.render import line_tops

    layout = Layout(("Chào cậu",), 30, 10, 50, 120, 37, True)
    font = load_font(str(FONT), 30)
    ascent, _ = font.getmetrics()
    assert baselines_of(layout, FONT)[0] == line_tops(layout, font)[0] + ascent


def test_lines_share_one_baseline_grid_and_the_ink_is_centred():
    # A line with stacked accents and one without must not drift apart, and
    # the block's ink sits in the middle of its box (off-centre text, the
    # operator, 2026-10-15).
    from comic.typesetting.render import line_tops

    layout = Layout(("ARATA-KUN", "VẪN LẠNH", "MỌI KHI"), 24, 0, 100, 200, 90, True)
    font = load_font(str(FONT), 24)
    tops = line_tops(layout, font)
    assert abs((tops[1] - tops[0]) - (tops[2] - tops[1])) < 1e-6
    ink_top = tops[0] + font.getbbox(layout.lines[0])[1]
    ink_bottom = tops[-1] + font.getbbox(layout.lines[-1])[3]
    assert abs((ink_top + ink_bottom) / 2 - (100 + 90 / 2)) < 1e-6


def test_leading_lands_the_last_line_where_pil_puts_it():
    lines = ("Ăn chưa", "ừ", "Ổn rồi ấy")
    layout = Layout(lines, 28, 0, 0, 200, 100, True)
    marks = baselines_of(layout, FONT)
    lead = leading_of(layout, FONT)
    assert abs(marks[0] + lead * (len(lines) - 1) - marks[-1]) < 1e-6


def test_only_regions_over_artwork_are_handed_to_content_aware_fill():
    image, mask = bubble_page()
    # One region in the white bubble (flat fill), one over the textured panel.
    image[170:190, 10:60] = 30
    mask[172:188, 12:58] = 255
    regions = [region("bubble", (60, 70, 140, 118)), region("art", (10, 170, 60, 190))]
    areas = fit_areas(image, mask, regions)
    fill = inpainted_glyphs(image, mask, regions, areas, {})
    assert fill[180, 30]            # lettering over art: Photoshop fills it
    assert not fill[74, 100]        # lettering in the bubble: left to the flat fill
    forced = inpainted_glyphs(image, mask, regions, areas,
                              {"bubble": RegionOverride(force_inpaint=True)})
    assert forced[74, 100]


def test_photoshop_addresses_fonts_by_postscript_name():
    assert font_info(FONT).postscript == "BeVietnamPro-Regular"


def test_script_paths_use_forward_slashes():
    plan = PagePlan(page=1, width=10, height=10, original=r"C:\a\001.png",
                    cleaned=r"C:\a\c\001.png", psd=r"C:\out\001.psd", png=r"C:\out\001.png")
    data = plan.to_script()
    assert "\\" not in data["original"] + data["cleaned"] + data["psd"] + data["png"]


# ------------------------------------------------------------------ sound effects


def _sfx_page(background: int, ink: int, outline: int | None, angle: float = 0.0):
    """A word-shaped block of ink, optionally outlined, on a flat panel."""
    import cv2

    image = np.full((200, 300, 3), background, dtype=np.uint8)
    letters = np.zeros((200, 300), dtype=np.uint8)
    for x in range(70, 230, 32):                     # five chunky "letters"
        cv2.rectangle(letters, (x, 80), (x + 20, 120), 255, -1)
    if angle:
        rot = cv2.getRotationMatrix2D((150, 100), angle, 1.0)
        letters = cv2.warpAffine(letters, rot, (300, 200), flags=cv2.INTER_NEAREST)
    if outline is not None:
        image[cv2.dilate(letters, np.ones((9, 9), np.uint8)) > 0] = outline
    image[letters > 0] = ink
    mask = cv2.dilate(letters, np.ones((5, 5), np.uint8))
    return image, mask


def test_sfx_ink_is_the_inside_colour_even_when_the_outline_is_brighter():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # White-outlined black letters on a *dark* panel: the outline is the
    # colour furthest from the background, but it is not the ink.
    image, mask = _sfx_page(background=60, ink=10, outline=250)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140))
    assert max(m["ink"]) < 60
    assert m["outlined"] and min(m["edge"]) > 200


def test_sfx_without_an_outline_gets_none():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    image, mask = _sfx_page(background=230, ink=200 // 10, outline=None)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140))
    assert max(m["ink"]) < 60
    # The outline is reported in the background's own colour: it draws nothing.
    assert min(m["edge"]) > 200


def test_sfx_angle_follows_the_lettering():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    image, mask = _sfx_page(background=230, ink=10, outline=None, angle=15)
    m = measure(image, mask, BBox(x1=30, y1=30, x2=270, y2=170))
    # cv2 rotates counter-clockwise for positive angles with y pointing down.
    assert 10 < abs(m["angle"]) < 20
    assert m["width"] > m["height"]


def test_anti_aliased_edges_are_not_read_as_an_outline():
    import cv2
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # Black letters on cream with a soft grey edge, and no real outline.
    image, mask = _sfx_page(background=235, ink=10, outline=None)
    image = cv2.GaussianBlur(image, (5, 5), 1.5)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140))
    assert max(m["ink"]) < 60
    assert min(m["edge"]) > 200     # not the grey of the anti-aliased edge


def test_sfx_colour_keeps_red_and_blue_apart():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # Pages are RGB (load_page): a red SFX must come out red, not blue.
    image, mask = _sfx_page(background=235, ink=0, outline=None)
    image[(image == 0).all(axis=2)] = (200, 20, 20)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140))
    r, g, b = m["ink"]
    assert r > 150 and b < 60


def test_black_letters_on_a_black_panel_stay_black():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # THROB (ch.54): the ink is the panel's own colour, set off by an outline.
    image, mask = _sfx_page(background=3, ink=3, outline=245)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140))
    assert max(m["ink"]) < 60 and min(m["edge"]) > 200


def test_a_light_object_beside_white_letters_does_not_flip_them():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # SQUEEZE (ch.46): white letters, black outline, a pale hand in the corner.
    image, mask = _sfx_page(background=80, ink=242, outline=5)
    image[120:200, 0:120] = (240, 220, 210)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140))
    assert min(m["ink"]) > 200 and max(m["edge"]) < 60


def test_a_series_rule_fixes_the_ink_where_thickness_would_guess_wrong():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # Black letters with a white outline *thicker* than the strokes, on a dark
    # panel (THROB, ch.54): thickness reads it backwards; the series rule not.
    image, mask = _sfx_page(background=40, ink=3, outline=245)
    m = measure(image, mask, BBox(x1=50, y1=60, x2=250, y2=140), ink_rule="dark")
    assert max(m["ink"]) < 60 and min(m["edge"]) > 200


def test_letter_height_is_one_letter_not_the_whole_staggered_word():
    import cv2
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # Five 40px letters stepping down 12px each (CHIRP, ch.18): the word's
    # box is ~90px tall; the letters are still 40px.
    image = np.full((200, 300, 3), 235, dtype=np.uint8)
    mask = np.zeros((200, 300), dtype=np.uint8)
    for i, x in enumerate(range(70, 230, 32)):
        y = 50 + 12 * i
        cv2.rectangle(image, (x, y), (x + 20, y + 40), (10, 10, 10), -1)
        cv2.rectangle(mask, (x - 2, y - 2), (x + 22, y + 42), 255, -1)
    m = measure(image, mask, BBox(x1=50, y1=30, x2=260, y2=170), angle=0)
    assert m["height"] > 80
    assert 36 <= m["letter_height"] <= 44


def test_plain_letters_strip_every_vietnamese_mark():
    from comic.photoshop.sfx import plain_letters

    assert plain_letters("GIẬT MÌNH") == "GIAT MINH"
    assert plain_letters("ĐỎ MẶT") == "DO MAT"
    assert plain_letters("ỰC") == "UC"


def test_parts_erase_each_piece_and_nothing_between_them():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    # A word the detector split in two, with a mark between the pieces that
    # belongs to something else (ch.5: a "HUH?" bubble inside BURST).
    image, mask = _sfx_page(background=235, ink=10, outline=None)
    image[95:105, 145:155] = 10          # a mark in the gap, outside the mask
    mask[:, 140:160] = 0
    left, right = BBox(x1=60, y1=70, x2=140, y2=130), BBox(x1=160, y1=70, x2=240, y2=130)
    m = measure(image, mask, left, parts=[left, right])
    assert m["glyphs"][100, 100] and m["glyphs"][100, 200]
    assert not m["glyphs"][100, 150]


def test_whole_takes_the_rest_of_a_letter_the_mask_only_grazed():
    import cv2
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    image, mask = _sfx_page(background=235, ink=10, outline=None)
    # The detector caught only the top of the first letter.
    mask[100:, 60:95] = 0
    box = BBox(x1=50, y1=60, x2=250, y2=140)
    plain = measure(image, mask, box)
    whole = measure(image, mask, box, whole=True)
    assert not plain["glyphs"][118, 80]
    assert whole["glyphs"][118, 80]
    # ...but not a dark panel the letters touch: it runs off the window.
    image2, mask2 = _sfx_page(background=235, ink=10, outline=None)
    image2 = np.pad(image2, ((0, 400), (0, 0), (0, 0)), constant_values=235)
    mask2 = np.pad(mask2, ((0, 400), (0, 0)))
    image2[125:, :] = 10
    cv2.rectangle(image2, (70, 118), (90, 126), (10, 10, 10), -1)
    m2 = measure(image2, mask2, box, whole=True)
    assert not m2["glyphs"][190, 150]


def test_a_polygon_keeps_out_lettering_beside_a_slanted_word():
    from comic.photoshop.sfx import measure
    from comic.schemas import BBox

    image, mask = _sfx_page(background=235, ink=10, outline=None)
    poly = [(60, 70), (160, 70), (160, 130), (60, 130)]
    m = measure(image, mask, BBox(x1=60, y1=70, x2=160, y2=130), by_colour=True, poly=poly)
    assert m["glyphs"][100, 80]
    assert not m["glyphs"][100, 210]


def test_refill_is_retired_and_changes_nothing():
    from comic.photoshop.plan import refill_flat

    image, mask = bubble_page()
    regions = [region("r", (60, 70, 140, 118))]
    regions[0].source_text = "HELLO"
    areas = fit_areas(image, mask, regions)
    cleaned = image.copy()
    assert refill_flat(image, cleaned, mask, regions, areas, {}) is cleaned


def test_exact_modal_colour_keeps_pure_white():
    from comic.photoshop.plan import exact_modal_colour

    # The pipeline's fill is the quantised bin's centre: 252 for white.
    image = np.full((20, 20, 3), 255, dtype=np.uint8)
    image[5:8, 5:8] = 250
    assert exact_modal_colour(image, np.ones((20, 20), np.uint8)).tolist() == [255, 255, 255]


def test_an_erase_only_item_erases_and_sets_nothing():
    from comic.photoshop.sfx import SfxItem, sfx_layers
    from comic.schemas import BBox

    image, mask = _sfx_page(background=235, ink=10, outline=None)
    item = SfxItem(region_id="p001_r80", erase_only=True, letters="area",
                   bbox=BBox(x1=60, y1=70, x2=160, y2=130), angle=0.0)
    layers, erase, erase_local, _ = sfx_layers(image, mask, 1, [], {"p001_r80": item}, "X")
    assert not layers and erase[100, 100]


def test_a_note_leaves_the_sfx_alone_and_sets_small_text_in_its_box():
    # A difficult SFX gets a translator's note in a corner instead of a
    # replacement (the operator, 2026-10-15): nothing is erased.
    import pytest

    from comic.photoshop.sfx import NOTE_INK, SfxItem, sfx_layers
    from comic.schemas import BBox

    image, mask = _sfx_page(background=235, ink=10, outline=None)
    item = SfxItem(region_id="p001_r81", kind="note", text="*Rắc rắc",
                   bbox=BBox(x1=10, y1=10, x2=90, y2=30))
    layers, erase, erase_local, _ = sfx_layers(image, mask, 1, [], {"p001_r81": item}, "X",
                                               note_font="NOTE")
    assert not erase.any() and not erase_local.any()
    (layer,) = layers
    assert layer.text == "*Rắc rắc" and layer.font == "NOTE" and layer.colour == NOTE_INK
    assert (layer.cx, layer.cy, layer.width, layer.height) == (50, 20, 80, 20)
    with pytest.raises(ValueError):
        SfxItem(region_id="p001_r82", kind="note", text="x", erase_only=True)


def test_a_note_without_a_box_goes_to_empty_space_not_onto_the_art():
    # The operator's marked-up pages: notes sit in gutters and margins.
    from comic.photoshop.notes import find_spot, occupied_mask

    page = np.full((400, 300), 255, np.uint8)
    page[20:380, 20:280] = 90          # one big panel of art
    page[150:250, 140:160] = 255       # nothing blank inside it but a slot
    busy = occupied_mask(page)
    spot = find_spot(busy, (150, 200), "Rắc rắc", str(FONT), 300)
    assert spot is not None
    assert not busy[spot.y1:spot.y2, spot.x1:spot.x2].any()
    # Off the art: in the margin, or in the blank slot.
    on_art = page[spot.y1:spot.y2, spot.x1:spot.x2] < 200
    assert not on_art.any()
