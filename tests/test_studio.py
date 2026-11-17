"""Studio's region review: edits land in analysis/, the detector's copy survives."""

from __future__ import annotations

import pytest

from comic.paths import ChapterPaths
from comic.schemas import BBox, PageAnalysis, ReadingDirection, TextKind, TextRegion
from comic.state.store import read_json, write_json
from comic.studio import store


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    paths.analysis.mkdir(parents=True)
    regions = [
        TextRegion(id="p001_r01", page=1, bbox=BBox(x1=10, y1=10, x2=100, y2=50),
                   kind=TextKind.DIALOGUE, source_text="HELLO", ocr_confidence=0.9,
                   polygon=[]),
        TextRegion(id="p001_r02", page=1, bbox=BBox(x1=10, y1=200, x2=100, y2=260),
                   kind=TextKind.SFX, source_text="BAM", reading_order=1),
    ]
    write_json(paths.analysis_file(1), PageAnalysis(
        page=1, width=400, height=600, reading_direction=ReadingDirection.TTB, regions=regions))
    return paths


def _payload(page):
    return [{"id": r["id"], "label": r["label"], "bbox": r["bbox"], "text": r["text"]}
            for r in page["regions"]]


def test_unchanged_save_writes_nothing(chapter):
    page = store.load_page("S", "Chapter_001", 1)
    store.save_page("S", "Chapter_001", 1, {"regions": _payload(page)})
    assert not (chapter.analysis / "_detected").exists()


def test_edit_add_relabel_delete(chapter):
    page = store.load_page("S", "Chapter_001", 1)
    regions = _payload(page)
    regions[0]["bbox"] = [12, 10, 120, 55]               # resized
    regions[1]["label"] = "watermark"                     # relabelled
    regions.append({"id": None, "label": "dialogue", "bbox": [10, 100, 90, 150], "text": ""})
    out = store.save_page("S", "Chapter_001", 1, {"regions": regions, "reviewed": True})

    assert out["reviewed"] is True
    assert (chapter.analysis / "_detected" / "page_001.json").exists()
    saved = read_json(chapter.analysis_file(1), PageAnalysis)
    by_id = {r.id: r for r in saved.regions}
    assert by_id["p001_r01"].edited and by_id["p001_r01"].source_text == "HELLO"
    assert by_id["p001_r02"].kind is TextKind.ERASE and by_id["p001_r02"].tag == "watermark"
    new = by_id["p001_r03"]
    assert new.edited and new.kind is TextKind.DIALOGUE
    # re-ordered top to bottom: the new box sits between the two
    assert [r.id for r in sorted(saved.regions, key=lambda r: r.reading_order)] == [
        "p001_r01", "p001_r03", "p001_r02"]

    # deleting one never reuses its id, even against the detector's copy
    regions = [r for r in _payload(out) if r["id"] != "p001_r03"]
    regions.append({"id": None, "label": "keep", "bbox": [200, 300, 300, 340], "text": "SHOP"})
    out = store.save_page("S", "Chapter_001", 1, {"regions": regions})
    ids = {r["id"] for r in out["regions"]}
    assert "p001_r03" not in ids and "p001_r04" in ids
    keep = next(r for r in out["regions"] if r["id"] == "p001_r04")
    assert keep["label"] == "keep"
    region = next(r for r in read_json(chapter.analysis_file(1), PageAnalysis).regions
                  if r.id == "p001_r04")
    assert not region.translatable and not region.erasable and region.ocr_reviewed


def test_sfx_for_lama_stays_an_sfx_that_cleaning_erases(chapter):
    """Key L (the operator, 2026-11-14): an SFX Content-Aware Fill would botch.
    It is still checked, translated and set like any SFX -- stored as ``sfx``
    with tag ``lama`` -- but cleaning erases it whole with LaMa."""
    page = store.load_page("S", "Chapter_001", 1)
    regions = _payload(page)
    regions[1]["label"] = "sfx_lama"
    regions.append({"id": None, "label": "sfx_lama", "bbox": [200, 300, 300, 380], "text": ""})
    out = store.save_page("S", "Chapter_001", 1, {"regions": regions})

    saved = {r.id: r for r in read_json(chapter.analysis_file(1), PageAnalysis).regions}
    for rid in ("p001_r02", "p001_r03"):
        r = saved[rid]
        assert r.kind is TextKind.SFX and r.tag == "lama"
        assert r.lama_sfx and r.erased_whole and r.cleanable
    labels = {r["id"]: r["label"] for r in out["regions"]}
    assert labels["p001_r02"] == "sfx_lama"

    # back to a plain SFX: the tag goes, and cleaning leaves it to Photoshop again
    regions = _payload(out)
    regions[1]["label"] = "sfx"
    store.save_page("S", "Chapter_001", 1, {"regions": regions})
    r = {r.id: r for r in read_json(chapter.analysis_file(1), PageAnalysis).regions}["p001_r02"]
    assert r.kind is TextKind.SFX and r.tag is None and not r.lama_sfx and not r.erasable


def test_text_edit_marks_reviewed(chapter):
    page = store.load_page("S", "Chapter_001", 1)
    regions = _payload(page)
    regions[0]["text"] = "HELLO!"
    store.save_page("S", "Chapter_001", 1, {"regions": regions})
    r = read_json(chapter.analysis_file(1), PageAnalysis).regions[0]
    assert r.source_text == "HELLO!" and r.ocr_reviewed and r.ocr_provider == "operator"


@pytest.mark.parametrize("bad", [
    {"regions": [{"id": None, "label": "nonsense", "bbox": [0, 0, 50, 50], "text": ""}]},
    {"regions": [{"id": None, "label": "dialogue", "bbox": [0, 0, 2, 2], "text": ""}]},
    {"regions": "x"},
])
def test_rejects_bad_input(chapter, bad):
    with pytest.raises(store.StudioError):
        store.save_page("S", "Chapter_001", 1, bad)


def test_refuses_paths_outside_workspace(chapter):
    with pytest.raises(store.StudioError):
        store.chapter_paths("..", "Chapter_001")
    with pytest.raises(store.StudioError):
        store.chapter_paths("S", "../../etc")


def test_whole_chapter_reviewed_at_once(chapter):
    out = store.set_chapter_reviewed("S", "Chapter_001", True)
    assert out["reviewed"] == [1]
    assert store.chapter_info("S", "Chapter_001")["pages"][0]["reviewed"]
    assert store.set_chapter_reviewed("S", "Chapter_001", False)["reviewed"] == []


def test_tilted_box_round_trips_and_stays_put(chapter):
    page = store.load_page("S", "Chapter_001", 1)
    regions = _payload(page)
    regions[0]["bbox"] = [100, 100, 200, 140]
    regions[0]["angle"] = 30
    store.save_page("S", "Chapter_001", 1, {"regions": regions})
    saved = {r.id: r for r in read_json(chapter.analysis_file(1), PageAnalysis).regions}["p001_r01"]
    assert saved.angle == 30 and len(saved.polygon) == 4 and saved.edited
    # upright bounds cover the turned box, which is wider than 40 px tall now
    assert saved.bbox.y2 - saved.bbox.y1 > 60

    again = store.load_page("S", "Chapter_001", 1)
    r0 = next(r for r in again["regions"] if r["id"] == "p001_r01")
    assert r0["angle"] == 30 and abs(r0["bbox"][0] - 100) <= 1 and abs(r0["bbox"][3] - 140) <= 1
    before = chapter.analysis_file(1).read_text(encoding="utf-8")
    payload = [{**{k: r[k] for k in ("id", "label", "bbox", "text")}, "angle": r["angle"]}
               for r in again["regions"]]
    store.save_page("S", "Chapter_001", 1, {"regions": payload})
    assert chapter.analysis_file(1).read_text(encoding="utf-8") == before   # rounding is not an edit

    payload[[p["id"] for p in payload].index("p001_r01")]["angle"] = 0     # straightened
    store.save_page("S", "Chapter_001", 1, {"regions": payload})
    r = {r.id: r for r in read_json(chapter.analysis_file(1), PageAnalysis).regions}["p001_r01"]
    assert r.angle == 0 and r.polygon == []


def test_tilted_box_cleans_and_crops_along_its_tilt():
    import numpy as np

    from comic.image_processing.mask import region_glyphs
    from comic.schemas import rotated_corners
    from comic.vision.analyze import crop_region

    corners = [tuple(round(v) for v in c) for c in rotated_corners((100, 100, 300, 140), 45)]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    region = TextRegion(id="p001_r01", page=1, kind=TextKind.DIALOGUE, angle=45, polygon=corners,
                        bbox=BBox(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys)))
    mask = np.zeros((400, 400), np.uint8)
    mask[region.bbox.y1:region.bbox.y2, region.bbox.x1:region.bbox.x2] = 255   # ink everywhere in the bounds
    glyphs = region_glyphs(mask, region, dilation=0)
    # the bounds' corners (art beside a tilted line) are not erased
    assert glyphs[region.bbox.y1 + 2, region.bbox.x1 + 2] == 0
    assert glyphs[120, 200] == 1
    image = np.full((400, 400, 3), 255, np.uint8)
    crop = crop_region(image, region, pad=4)
    assert abs(crop.shape[0] - 48) <= 1 and abs(crop.shape[1] - 208) <= 1   # level: own size + pad
