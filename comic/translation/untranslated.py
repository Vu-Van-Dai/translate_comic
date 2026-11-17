"""``untranslated.txt``: every line the pipeline did not translate (the operator, 2026-11-08).

The operator fills these in by hand (Photoshop, Typer Tools), so each entry
says exactly where the line is and what it says:

    Series | Chapter_008 | page 012 | p012_r03 | dialogue | x=120 y=340 w=210 h=96
      original: YOU ARE TRULY VERY CLOSE ...
      reason:   <why it is not translated>

Written per chapter (``workspace/<series>/<chapter>/untranslated.txt``) and
collected per series (``workspace/<series>/untranslated.txt``), rebuilt from
the current state every time, so a line translated later disappears from it.
Cleaning is separate: every one of these regions is still cleaned.
"""

from __future__ import annotations

from pathlib import Path

from comic.paths import ChapterPaths, SeriesPaths, chapter_sort_key
from comic.schemas import ChapterTranslation, TextKind
from comic.state.store import ChapterStore, StateError, read_json

FILE = "untranslated.txt"


def _entries(paths: ChapterPaths) -> list[str]:
    from comic.stages import analysis_for

    try:
        state = ChapterStore(paths).load()
    except StateError:
        return []
    done: set[str] = set()
    if paths.translation_file.exists():
        done = set(read_json(paths.translation_file, ChapterTranslation).by_region())  # type: ignore[union-attr]

    out: list[str] = []
    for record in state.pages:
        if record.excluded:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for r in sorted(analysis.regions, key=lambda r: r.reading_order):
            if r.kind in (TextKind.ERASE, TextKind.KEEP) or r.id in done:
                continue
            if r.hold_reason:
                reason = f"left for the operator: {r.hold_reason}"
            elif not r.source_text.strip():
                reason = "no source text"
            elif r.lama_sfx:
                reason = "SFX marked for LaMa (Studio L): erased at cleaning, not translated yet"
            elif r.kind is TextKind.SFX:
                reason = "SFX left as drawn (crying sound, bare ?/!, duplicate, or not translated yet)"
            else:
                reason = "not translated yet"
            b = r.bbox
            out.append(
                f"{state.series} | {paths.chapter} | page {record.page:03d} | {r.id} | "
                f"{r.kind.value} | x={b.x1} y={b.y1} w={b.width} h={b.height}\n"
                f"  original: {r.source_text.strip() or '-'}\n"
                f"  reason:   {reason}\n"
            )
    return out


def write_chapter(paths: ChapterPaths) -> tuple[Path, int]:
    entries = _entries(paths)
    target = paths.workspace / FILE
    header = (f"# Untranslated lines -- {paths.series} / {paths.chapter}\n"
              f"# {len(entries)} line(s). Every one is still cleaned from the page.\n\n")
    target.write_text(header + "\n".join(entries), encoding="utf-8")
    return target, len(entries)


def write_series(series: str) -> tuple[Path, int]:
    sp = SeriesPaths(series)
    blocks: list[str] = []
    total = 0
    for label in sorted(sp.chapters(), key=chapter_sort_key):
        paths = ChapterPaths(series, label)
        if not paths.translation_file.exists():
            continue   # not translated at all yet: nothing to report
        entries = _entries(paths)
        total += len(entries)
        if entries:
            blocks.append(f"## {label}  ({len(entries)})\n\n" + "\n".join(entries))
    target = sp.workspace_dir / FILE
    target.write_text(f"# Untranslated lines -- {sp.series}: {total} line(s)\n\n"
                      + "\n".join(blocks), encoding="utf-8")
    return target, total
