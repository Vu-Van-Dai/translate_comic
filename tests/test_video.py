"""`comic video`: the source rule, panels, plan validation, timing and frames."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from comic.paths import ChapterPaths
from comic.video.music import snap_cuts
from comic.video.panels import page_panels, reading_order
from comic.video.render import Timeline, camera, prepare, text_layer, wrap, _font
from comic.video.schemas import HEIGHT, WIDTH, Motion, Shot, TextRole, VideoPlan
from comic.video.source import ArtSource, ForbiddenSource, art_file, check_allowed
from comic.video.styles import styles


# -- the operator's rule: never translated dialogue -------------------------

@pytest.mark.parametrize("bad", [
    "workspace/S/Chapter_001/translated/003.webp",
    "library/S/Chapter_001/003.webp",
    "workspace/S/Chapter_001/photoshop/003.png",
])
def test_translated_pages_are_refused(bad):
    with pytest.raises(ForbiddenSource):
        check_allowed(Path(bad))


@pytest.mark.parametrize("good", [
    "workspace/S/Chapter_001/photoshop/003_art.png",
    "workspace/S/Chapter_001/cleaned/003.webp",
    "workspace/S/Chapter_001/original/003.jpg",
])
def test_art_cleaned_and_raw_pages_are_allowed(good):
    assert check_allowed(Path(good)) == Path(good)


def _page(path: Path, colour=(90, 90, 90)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 80), colour).save(path)


def test_art_file_prefers_text_free_art_and_falls_back(tmp_path):
    paths = ChapterPaths("S", "1", root=tmp_path)
    _page(paths.original / "001.png")
    _page(paths.translated / "001.png")              # never a candidate
    assert art_file(paths, 1)[1] is ArtSource.ORIGINAL
    _page(paths.cleaned / "001.png")
    assert art_file(paths, 1)[1] is ArtSource.CLEANED
    _page(paths.workspace / "photoshop" / "001.png")  # the preview: not art
    assert art_file(paths, 1)[1] is ArtSource.CLEANED
    _page(paths.workspace / "photoshop" / "001_art.png")
    path, kind = art_file(paths, 1)
    assert kind is ArtSource.ART and path.name == "001_art.png"
    assert art_file(paths, 1, ArtSource.ORIGINAL)[1] is ArtSource.ORIGINAL


# -- panels -----------------------------------------------------------------

def _manga_page() -> np.ndarray:
    """White page, three dark panels: a wide one on top, two below (RTL)."""
    page = np.full((1000, 700, 3), 255, np.uint8)
    page[20:400, 20:680] = 60          # k1: top, full width
    page[420:980, 360:680] = 80        # k2: bottom right (read first in RTL)
    page[420:980, 20:340] = 100        # k3: bottom left
    return page


def test_panels_found_in_manga_reading_order():
    panels = page_panels(_manga_page(), 5, rtl=True)
    assert panels[0].id == "p005_k0" and panels[0].box == (0, 0, 700, 1000)
    boxes = [p.box for p in panels[1:]]
    assert len(boxes) == 3
    assert boxes[0][1] < 30 and boxes[0][2] - boxes[0][0] > 600   # top first
    assert boxes[1][0] > boxes[2][0]                                # right before left


def test_reading_order_left_to_right_for_western_pages():
    boxes = [(0, 0, 100, 100), (200, 0, 300, 100)]
    assert reading_order(boxes, rtl=False)[0] == (0, 0, 100, 100)
    assert reading_order(boxes, rtl=True)[0] == (200, 0, 300, 100)


def test_full_bleed_page_is_one_panel():
    page = np.full((1000, 700, 3), 70, np.uint8)
    assert [p.id for p in page_panels(page, 1)] == ["p001_k0"]


# -- the plan ---------------------------------------------------------------

def _plan(**over) -> dict:
    shots = [{"panel": f"p00{i}_k0", "seconds": 7.0} for i in range(1, 5)]
    return {"style": "story", "shots": shots, "caption": "x"} | over


def test_plan_rejects_unknown_motion_and_short_lists():
    with pytest.raises(ValidationError):
        VideoPlan.model_validate(_plan(shots=[{"panel": "p001_k0", "seconds": 3, "motion": "spin"}] * 4))
    with pytest.raises(ValidationError):
        VideoPlan.model_validate(_plan(shots=[{"panel": "p001_k0", "seconds": 3}] * 3))


def test_plan_length_must_be_25_to_40_seconds():
    plan = VideoPlan.model_validate(_plan())
    plan.check_length()                                  # 28 s
    short = VideoPlan.model_validate(_plan(shots=[{"panel": "p001_k0", "seconds": 2}] * 5))
    with pytest.raises(ValueError):
        short.check_length()


def test_hashtags_are_normalised():
    plan = VideoPlan.model_validate(_plan(hashtags=["#fyp", "manga"]))
    assert plan.hashtags == ["fyp", "manga"]
    with pytest.raises(ValidationError):
        VideoPlan.model_validate(_plan(hashtags=["two words"]))


def test_styles_include_the_requested_moods():
    names = set(styles())
    assert {"melancholic", "emotional", "drama", "comedy", "story", "beat"} <= names
    assert styles()["romantic"].adults_only


# -- timing -----------------------------------------------------------------

def test_cuts_snap_to_nearby_beats_and_keep_the_length():
    beats = [i * 0.6 for i in range(100)]
    out = snap_cuts([3.0, 3.1, 2.9], beats, start=0.0)
    assert abs(sum(out) - 9.0) < 1e-6
    cuts = np.cumsum(out)[:-1]
    assert all(min(abs(c - b) for b in beats) < 1e-6 for c in cuts)


def test_snap_never_collapses_a_shot():
    # Both cuts would land on the beat at 2.3 s, leaving the middle shot empty.
    assert snap_cuts([2.0, 0.4, 3.0], [2.3], start=0.0) == [2.0, 0.4, 3.0]


def test_camera_moves_and_settles():
    assert camera(Motion.STATIC, 1, 1.0, 3.0, 0) == (1.0, 0.0, 0.0)
    z0, _, _ = camera(Motion.ZOOM_IN, 0.5, 0.0, 3.0, 0)
    z1, _, _ = camera(Motion.ZOOM_IN, 0.5, 3.0, 3.0, 0)
    assert z0 == pytest.approx(1.0) and z1 > 1.05
    _, x0, _ = camera(Motion.PAN_RIGHT, 0.5, 0.0, 3.0, 0)
    _, x1, _ = camera(Motion.PAN_RIGHT, 0.5, 3.0, 3.0, 0)
    assert x0 == pytest.approx(1.0) and x1 == pytest.approx(-1.0)


# -- frames -----------------------------------------------------------------

def test_vietnamese_text_never_breaks_a_word():
    font = _font("BeVietnamPro-Bold.ttf", 60)
    lines = wrap("Đang yên đang lành, tự dưng có thêm một đứa em trai", font, 840)
    assert lines and all(font.getlength(l) <= 840 for l in lines)
    assert " ".join(lines) == "Đang yên đang lành, tự dưng có thêm một đứa em trai"
    layer = text_layer("Anh xin lỗi, Ren.", TextRole.QUOTE, "BeVietnamPro-Bold.ttf")
    assert layer.width <= WIDTH and np.asarray(layer)[..., 3].max() == 255


def test_frames_are_full_size_and_refuse_translated_pages(tmp_path):
    art = tmp_path / "photoshop" / "001_art.png"
    _page(art, (200, 120, 40))
    shot = Shot(panel="p001_k0", seconds=2.0, motion="zoom_in")
    p = prepare(shot, art, (0, 0, 60, 80), 0.0, 2.0, [], "BeVietnamPro-Bold.ttf", 0)
    frame = Timeline([p], []).frame(1.0)
    assert frame.size == (WIDTH, HEIGHT)
    bad = tmp_path / "translated" / "001.png"
    _page(bad)
    with pytest.raises(ForbiddenSource):
        prepare(shot, bad, (0, 0, 60, 80), 0.0, 2.0, [], "BeVietnamPro-Bold.ttf", 0)


def test_split_shot_draws_two_images_and_other_chapters_resolve(tmp_path):
    from comic.video.gate import PanelIndex

    for ch, colour in (("2", (200, 30, 30)), ("5", (30, 30, 200))):
        p = ChapterPaths("S", ch, root=tmp_path)
        _page(p.original / "001.png", colour)
    index = PanelIndex(ChapterPaths("S", "2", root=tmp_path))
    info5, _ = index.find("p001_k0", "5")
    assert "Chapter_005" in info5["source"]
    shot = Shot(panel="p001_k0", seconds=2.0, panel2="p001_k0", chapter2="5")
    half = (WIDTH, HEIGHT // 2)
    info2, _ = index.find("p001_k0")
    top = prepare(shot, Path(info2["source"]), (0, 0, 60, 80), 0.0, 2.0, [], "BeVietnamPro-Bold.ttf", 0, "white", half)
    top.partner = prepare(shot, Path(info5["source"]), (0, 0, 60, 80), 0.0, 2.0, [], "BeVietnamPro-Bold.ttf", 1,
                          "white", half)
    frame = np.asarray(Timeline([top], []).frame(1.0))
    assert frame.shape == (HEIGHT, WIDTH, 3)
    assert frame[HEIGHT // 4, WIDTH // 2, 0] > 150        # top: chapter 2's red page
    assert frame[3 * HEIGHT // 4, WIDTH // 2, 2] > 150    # bottom: chapter 5's blue page


def test_translated_pages_only_where_the_series_allows(tmp_path, monkeypatch):
    import comic.video.source as source
    from comic.video.styles import SeriesVideo

    allowed = {"Open"}
    monkeypatch.setattr("comic.video.styles.series_video",
                        lambda s: SeriesVideo(allow_translated=s in allowed))
    assert check_allowed(Path("library/Open/Chapter_001/003.webp"))
    with pytest.raises(ForbiddenSource):
        check_allowed(Path("library/Closed/Chapter_001/003.webp"))
    assert source.default_source("Open") is ArtSource.TRANSLATED
    assert source.default_source("Closed") is ArtSource.ORIGINAL
    for series in ("Open", "Closed"):
        paths = ChapterPaths(series, "1", root=tmp_path)
        _page(paths.original / "001.png")
        _page(paths.library / "001.webp")
        assert art_file(paths, 1, ArtSource.TRANSLATED)[1] is (
            ArtSource.TRANSLATED if series == "Open" else ArtSource.ORIGINAL)


def test_named_videos_share_panels_but_not_gates(tmp_path):
    from comic.video.gate import VideoPaths

    paths = ChapterPaths("S", "1", root=tmp_path)
    plain, arc = VideoPaths(paths), VideoPaths(paths, "Arc_01-10")
    assert plain.panels == arc.panels
    assert arc.response("video_plan") != plain.response("video_plan")
    assert arc.final.name == "Arc_01-10" and plain.final.name == "Chapter_001"
