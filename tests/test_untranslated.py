"""Which regions the translate gate asks for, and what untranslated.txt lists."""

from __future__ import annotations

import pytest

from comic.paths import ChapterPaths
from comic.schemas import (
    BBox, ChapterTranslation, PageAnalysis, PageRecord, ReadingDirection, TextKind,
    TextRegion, TranslationEntry,
)
from comic.state.store import ChapterStore, write_json
from comic.translation.gate import missing_translations, wants_translation
from comic.translation.untranslated import write_chapter, write_series


def _r(i, kind, text="X", **kw):
    return TextRegion(id=f"p001_r{i:02d}", page=1, bbox=BBox(x1=0, y1=i * 20, x2=50, y2=i * 20 + 15),
                      kind=kind, source_text=text, reading_order=i, **kw)


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    store = ChapterStore(paths)
    state = store.create(source_language="en")
    state.pages = [PageRecord(page=1, filename="001.png")]
    store.save(state)
    regions = [
        _r(1, TextKind.DIALOGUE, "HELLO"),
        _r(2, TextKind.SFX, "BAM"),
        _r(3, TextKind.SFX, "HA"),
        _r(4, TextKind.DIALOGUE, "SECRET", hold_reason="left for the operator"),
        _r(5, TextKind.ERASE, "SITE.COM", tag="watermark"),
        _r(6, TextKind.KEEP, "SHOP"),
        _r(7, TextKind.NARRATION, "LATER"),
    ]
    write_json(paths.analysis_file(1), PageAnalysis(
        page=1, width=100, height=200, reading_direction=ReadingDirection.TTB, regions=regions))
    return paths, store, regions


def test_gate_asks_for_dialogue_and_sfx_but_not_held_or_erased(chapter):
    _, _, regions = chapter
    assert [wants_translation(r) for r in regions] == [True, True, True, False, False, False, True]


def test_untranslated_lists_what_was_not_translated(chapter):
    paths, store, _ = chapter
    write_json(paths.translation_file, ChapterTranslation(series="S", chapter="Chapter_001", entries=[
        TranslationEntry(region_id="p001_r01", source_text="HELLO", target_text="CHÀO"),
        TranslationEntry(region_id="p001_r02", source_text="BAM", target_text="RẦM"),
    ]))
    # an SFX left as drawn is not "missing"; untranslated narration is
    assert missing_translations(paths, store.load()) == ["p001_r07"]
    f, n = write_chapter(paths)
    text = f.read_text(encoding="utf-8")
    assert n == 3
    for rid in ("p001_r03", "p001_r04", "p001_r07"):
        assert rid in text
    for rid in ("p001_r01", "p001_r02", "p001_r05", "p001_r06"):
        assert rid not in text
    assert "left for the operator" in text and "original: SECRET" in text
    assert "page 001" in text and "x=0 y=80 w=50 h=15" in text
    _, total = write_series("S")
    assert total == 3
