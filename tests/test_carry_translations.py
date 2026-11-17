"""Re-cut pages get their old Vietnamese back; the gate then asks only for the rest."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from comic.paths import ChapterPaths
from comic.schemas import (
    BBox, ChapterTranslation, PageAnalysis, PageRecord, ReadingDirection, TextKind,
    TextRegion, TranslationEntry,
)
from comic.state.store import ChapterStore, read_json, write_json
from comic.translation.gate import open_translate_gate

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _r(page, i, text):
    return TextRegion(id=f"p{page:03d}_r{i:02d}", page=page, kind=TextKind.DIALOGUE,
                      bbox=BBox(x1=0, y1=i * 20, x2=50, y2=i * 20 + 15), source_text=text,
                      reading_order=i)


def test_carry_matches_by_english_and_leaves_new_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    carry = _load("carry_translations")
    paths = ChapterPaths("S", "Chapter_001")
    store = ChapterStore(paths)
    state = store.create(source_language="en")
    state.pages = [PageRecord(page=1, filename="001.png"), PageRecord(page=2, filename="002.png")]
    store.save(state)
    paths.source_pages.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        Image.fromarray(np.full((200, 100, 3), 255, np.uint8)).save(paths.source_pages / f"00{n}.png")
    # page 1 was kept by the fixer (its translation stayed); page 2 was re-cut
    write_json(paths.analysis_file(1), PageAnalysis(
        page=1, width=100, height=200, reading_direction=ReadingDirection.TTB,
        regions=[_r(1, 1, "HELLO.")]))
    write_json(paths.analysis_file(2), PageAnalysis(
        page=2, width=100, height=200, reading_direction=ReadingDirection.TTB,
        regions=[_r(2, 1, "WHERE ARE YOU GOING?"), _r(2, 2, "I MISSED YOU SO MUCH, IDIOT."),
                 _r(2, 3, "HELLO.")]))
    write_json(paths.translation_file, ChapterTranslation(series="S", chapter="Chapter_001", entries=[
        TranslationEntry(region_id="p001_r01", source_text="HELLO.", target_text="CHÀO.")]))
    backup = paths.workspace / "_backup_before_reorder"
    backup.mkdir()
    write_json(backup / "translation.json", ChapterTranslation(series="S", chapter="Chapter_001", entries=[
        TranslationEntry(region_id="p001_r01", source_text="HELLO.", target_text="CHÀO."),
        TranslationEntry(region_id="p003_r04", source_text="WHERE ARE YOU GOlNG?",   # OCR misread
                         target_text="EM ĐI ĐÂU ĐẤY?", speaker="Ryu", addressee="Ha"),
        TranslationEntry(region_id="p003_r05", source_text="I MISSED", target_text="ANH NHỚ"),
        TranslationEntry(region_id="p003_r06", source_text="YOU SO MUCH, IDIOT.",
                         target_text="EM LẮM, ĐỒ NGỐC."),
    ]))

    assert carry.main("S", "1") == 0
    by = read_json(paths.translation_file, ChapterTranslation).by_region()
    assert by["p002_r01"].target_text == "EM ĐI ĐÂU ĐẤY?" and by["p002_r01"].speaker == "Ryu"
    assert by["p002_r01"].source_text == "WHERE ARE YOU GOING?"
    # the bubble the old cut had split in two reads as one line now: translate it again
    assert "p002_r02" not in by
    # the kept page's HELLO was not offered a second time
    assert "p002_r03" not in by

    _, count = open_translate_gate(paths, store.load(), missing_only=True)
    request = json.loads(paths.gate_request("translate").read_text(encoding="utf-8"))
    assert count == 2
    assert [i["region_id"] for p in request["pages"] for i in p["items"]] == ["p002_r02", "p002_r03"]


def test_renumber_rewrites_ids_and_page():
    fix = _load("fix_slice_order")
    a = PageAnalysis(page=7, width=10, height=10, reading_direction=ReadingDirection.TTB,
                     regions=[_r(7, 1, "A"), _r(7, 12, "B")])
    out = PageAnalysis.model_validate_json(fix.renumber(a.model_dump_json(), 7, 5))
    assert out.page == 5 and [r.id for r in out.regions] == ["p005_r01", "p005_r12"]
    assert {r.page for r in out.regions} == {5}


def test_paged_chapter_is_a_permutation_nothing_redetected(tmp_path, monkeypatch, capsys):
    from comic.schemas import PageKind

    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    fix = _load("fix_slice_order")
    paths = ChapterPaths("S", "Chapter_001")
    store = ChapterStore(paths)
    state = store.create(source_language="en", page_kind=PageKind.PAGED)
    colours = {1: 10, 2: 120, 3: 240}                       # saved order: pages 2 and 3 swapped
    paths.original.mkdir(parents=True, exist_ok=True)
    for n, c in colours.items():
        Image.fromarray(np.full((40, 30, 3), c, np.uint8)).save(paths.original / f"00{n}.png")
        write_json(paths.analysis_file(n), PageAnalysis(
            page=n, width=30, height=40, reading_direction=ReadingDirection.TTB,
            regions=[_r(n, 1, f"TEXT {c}")]))
    state.pages = [PageRecord(page=n, filename=f"00{n}.png", width=30, height=40,
                              stages={"detect": "done", "ocr": "done"}) for n in colours]
    store.save(state)
    write_json(paths.translation_file, ChapterTranslation(series="S", chapter="Chapter_001", entries=[
        TranslationEntry(region_id=f"p00{n}_r01", source_text=f"TEXT {c}", target_text=f"VI {c}")
        for n, c in colours.items()]))
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    for n, c in ((1, 10), (2, 240), (3, 120)):              # the true order
        Image.fromarray(np.full((40, 30, 3), c, np.uint8)).save(fresh / f"00{n}.png")

    assert fix.main("S", "1", str(fresh)) == 1
    assert "REDETECT \n" in capsys.readouterr().out + "\n"   # nothing to re-detect
    a2 = PageAnalysis.model_validate_json(paths.analysis_file(2).read_text(encoding="utf-8"))
    assert a2.regions[0].source_text == "TEXT 240" and a2.regions[0].id == "p002_r01"
    by = read_json(paths.translation_file, ChapterTranslation).by_region()
    assert by["p002_r01"].target_text == "VI 240" and by["p003_r01"].target_text == "VI 120"
    assert store.load().pages[1].stages == {"detect": "done", "ocr": "done"}
