"""The transcription check: corrects text, holds regions, never deletes one."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from comic import check
from comic.paths import ChapterPaths
from comic.schemas import BBox, PageAnalysis, PageRecord, ReadingDirection, TextKind, TextRegion
from comic.state.store import ChapterStore, read_json, write_json


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    store = ChapterStore(paths)
    state = store.create(source_language="en")
    state.pages = [PageRecord(page=1, filename="001.png")]
    state.page_count = 1
    store.save(state)
    Image.fromarray(np.full((400, 300, 3), 255, np.uint8)).save(paths.original / "001.png")
    regions = [
        TextRegion(id="p001_r01", page=1, bbox=BBox(x1=10, y1=10, x2=120, y2=60),
                   kind=TextKind.DIALOGUE, source_text="HELO", ocr_confidence=0.6),
        TextRegion(id="p001_r02", page=1, bbox=BBox(x1=10, y1=100, x2=120, y2=150),
                   kind=TextKind.SFX, source_text="BAM", reading_order=1),
        TextRegion(id="p001_r03", page=1, bbox=BBox(x1=10, y1=200, x2=120, y2=250),
                   kind=TextKind.ERASE, tag="logo", source_text="SITE", reading_order=2),
        TextRegion(id="p001_r04", page=1, bbox=BBox(x1=10, y1=300, x2=120, y2=350),
                   kind=TextKind.DIALOGUE, source_text="", edited=True, reading_order=3),
    ]
    write_json(paths.analysis_file(1), PageAnalysis(
        page=1, width=300, height=400, reading_direction=ReadingDirection.TTB, regions=regions))
    return paths, store


def _answer(paths, items):
    paths.gate_response(check.GATE).write_text(json.dumps({"items": items}), encoding="utf-8")


def test_needs_studio_review_first(chapter):
    paths, store = chapter
    assert check.unreviewed_pages(paths, store.load()) == [1]
    (paths.workspace / "studio.json").write_text('{"reviewed": [1]}')
    assert check.unreviewed_pages(paths, store.load()) == []


def test_request_skips_logos_and_flags_empty_boxes(chapter):
    paths, store = chapter
    check.open_check(paths, store.load())
    request = json.loads(paths.gate_request(check.GATE).read_text(encoding="utf-8"))
    ids = [i["region_id"] for i in request["items"]]
    assert ids == ["p001_r01", "p001_r02", "p001_r04"]      # the logo is not checked
    assert "no text yet" in request["items"][2]["note"]
    assert all(__import__("os").path.exists(s) for s in request["sheets"])


def test_apply_corrects_and_holds_without_deleting(chapter):
    paths, store = chapter
    state = store.load()
    check.open_check(paths, state)
    _answer(paths, [
        {"region_id": "p001_r01", "text": "HELLO"},
        {"region_id": "p001_r02", "kind": "shout"},
        {"region_id": "p001_r04", "skip": "left for the operator"},
        {"region_id": "p001_r03", "skip": "no lettering in this box, only art"},
    ])
    corrected, held, problems = check.apply_check(paths, state)
    assert (corrected, held, problems) == (1, 2, [])
    regions = {r.id: r for r in read_json(paths.analysis_file(1), PageAnalysis).regions}
    assert len(regions) == 4                                   # nothing deleted
    assert regions["p001_r01"].source_text == "HELLO" and regions["p001_r01"].ocr_reviewed
    assert regions["p001_r02"].kind is TextKind.SHOUT
    assert regions["p001_r04"].hold_reason == "left for the operator"
    assert regions["p001_r04"].kind is not TextKind.KEEP
    assert regions["p001_r03"].kind is TextKind.KEEP              # art only: never cleaned
    assert not paths.gate_response(check.GATE).exists()      # cannot be applied twice


@pytest.mark.parametrize("items, needle", [
    ([{"region_id": "p001_r01", "text": "  "}], "empty text"),
    ([{"region_id": "p009_r01", "text": "X"}], "unknown region"),
    ([{"region_id": "p001_r01"}], "nothing to change"),
    ([{"region_id": "p001_r01", "kind": "erase"}], "set in Studio"),
    ([{"region_id": "p001_r01", "text": "A"}, {"region_id": "p001_r01", "text": "B"}], "twice"),
])
def test_bad_answers_change_nothing(chapter, items, needle):
    paths, store = chapter
    state = store.load()
    before = paths.analysis_file(1).read_text(encoding="utf-8")
    _answer(paths, items)
    _, _, problems = check.apply_check(paths, state)
    assert any(needle in p for p in problems)
    assert paths.analysis_file(1).read_text(encoding="utf-8") == before
