"""Reading an SFX's look: second stroke, shadow, glow, pieces, vertical lines."""

from __future__ import annotations

import cv2
import numpy as np

from comic.photoshop import sfx_style as st

BLACK, WHITE, GREY = (0, 0, 0), (255, 255, 255), (128, 128, 128)


def _letters(shape=(200, 300)) -> np.ndarray:
    """Two blocky 'letters' as a mask."""
    m = np.zeros(shape, np.uint8)
    m[60:140, 80:110] = 1
    m[60:140, 150:180] = 1
    return m


def _paint(img, mask, colour):
    img[mask > 0] = colour


def _ring(mask, r):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(mask, k)


def _zone(mask):
    return _ring(mask, 6) > 0


def test_black_letters_white_outline_black_outer_stroke():
    letters = _letters()
    img = np.full(letters.shape + (3,), GREY, np.uint8)
    _paint(img, _ring(letters, 7), BLACK)       # outer stroke: 3 px past the outline
    _paint(img, _ring(letters, 4), WHITE)       # outline: 4 px
    _paint(img, letters, BLACK)
    fx = st.effects(img, _zone(letters), BLACK, WHITE, True)
    assert fx.stroke2_colour == BLACK and 2 <= fx.stroke2_px <= 4
    assert fx.glow_px == 0 and fx.shadow_colour is None


def test_drop_shadow_found_on_one_side_only():
    letters = _letters()
    img = np.full(letters.shape + (3,), GREY, np.uint8)
    outline = _ring(letters, 3)
    shadow = np.roll(np.roll(outline, 7, axis=0), 7, axis=1)
    _paint(img, shadow, (20, 20, 90))
    _paint(img, outline, WHITE)
    _paint(img, letters, BLACK)
    fx = st.effects(img, _zone(letters), BLACK, WHITE, True)
    assert fx.shadow_colour is not None and fx.shadow_dx > 0 and fx.shadow_dy > 0
    assert fx.stroke2_px == 0


def test_soft_glow():
    letters = _letters()
    img = np.full(letters.shape + (3,), GREY, np.uint8).astype(np.float32)
    halo = cv2.GaussianBlur(_ring(letters, 4).astype(np.float32), (0, 0), 4)
    halo = np.clip(halo * 1.6, 0, 1)[..., None]
    img = img * (1 - halo) + np.array((255, 230, 60), np.float32) * halo     # yellow glow
    img = img.astype(np.uint8)
    _paint(img, _ring(letters, 2), WHITE)
    _paint(img, letters, BLACK)
    fx = st.effects(img, _zone(letters), BLACK, WHITE, True)
    assert fx.glow_px > 0 and fx.stroke2_px == 0


def test_plain_letters_have_no_effects():
    letters = _letters()
    img = np.full(letters.shape + (3,), WHITE, np.uint8)
    _paint(img, letters, BLACK)
    fx = st.effects(img, _zone(letters), BLACK, WHITE, False)
    assert (fx.stroke2_px, fx.glow_px, fx.shadow_colour) == (0, 0, None)


def test_pieces_and_split():
    two = np.zeros((100, 400), bool)
    two[30:70, 20:120] = True
    two[30:70, 260:360] = True
    assert len(st.pieces(two, gap=8)) == 2
    one = np.zeros((100, 400), bool)
    one[30:70, 20:120] = True
    one[30:70, 126:200] = True                    # a letter gap, not a piece gap
    assert st.pieces(one, gap=8) == []
    assert st.split_text("QUẪY", [1.0, 1.0]) == ["QU", "ẪY"]
    assert st.split_text("BÙM BÙM", [1.0, 1.0]) == ["BÙM", "BÙM"]
    assert st.split_text("RẦM", [1.0, 1.0, 1.0]) == ["R", "Ầ", "M"]
    assert st.split_text("A", [1.0, 1.0]) == []


def test_vertical_lines_and_slant():
    assert st.vertical_text("RẮC RẮC") == "R\nẮ\nC\nR\nẮ\nC"
    assert abs(st.slant_of(90)) < 1e-6 and abs(st.slant_of(-90)) < 1e-6
    assert st.slant_of(80) > 0 > st.slant_of(-80)


def test_gradient_fill_and_texture():
    letters = _letters()
    img = np.full(letters.shape + (3,), GREY, np.uint8)
    _paint(img, _ring(letters, 3), BLACK)                       # dark outline
    ys = np.arange(letters.shape[0])[:, None]
    t = np.clip((ys - 60) / 80.0, 0, 1)
    grad = (np.array((250, 220, 40)) * (1 - t)[..., None] + np.array((220, 30, 30)) * t[..., None]).astype(np.uint8)
    img[letters > 0] = np.broadcast_to(grad, img.shape)[letters > 0]   # yellow top -> red bottom
    fx = st.effects(img, _zone(letters), (230, 120, 35), BLACK, True)
    assert fx.gradient_top is not None and fx.gradient_top[1] > fx.gradient_bottom[1]   # greener at the top
    assert not fx.textured

    hatch = img.copy()
    stripes = ((np.indices(letters.shape).sum(axis=0) // 3) % 2).astype(bool) & (letters > 0)
    hatch[letters > 0] = WHITE
    hatch[stripes] = (60, 40, 120)
    fx = st.effects(hatch, _zone(letters), WHITE, BLACK, True)
    assert fx.textured and fx.gradient_top is None


def test_colour_mode_erases_strokes_not_art_in_the_box():
    """A box-mode SFX erase follows the letters, not the rectangle (2026-11-10)."""
    from comic.photoshop import sfx
    from comic.schemas import BBox

    image = np.full((200, 300, 3), 255, np.uint8)
    cv2.putText(image, "UF", (60, 130), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 12)
    cv2.circle(image, (250, 40), 8, (0, 0, 0), -1)          # art inside the box
    detector = np.zeros((200, 300), np.uint8)
    detector[50:140, 55:200] = 255                          # rough blob over the letters
    m = sfx.measure(image, detector, BBox(x1=20, y1=10, x2=290, y2=190), by_colour=True)
    glyphs = m["glyphs"] > 0
    letters = (image[..., 0] < 128)
    assert glyphs[letters & (detector > 0)].all()           # every letter pixel erased
    assert not glyphs[30:50, 240:260].any()                 # the dot stays
    assert glyphs.sum() < 0.5 * (270 * 180)                 # far from the whole box
