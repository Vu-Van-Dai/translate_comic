"""The translate gate: hand the chapter to the agent, take back Vietnamese.

This is the one gate that always opens. Everything else in the pipeline is a
tool doing something measurable; the wording is judgement, and judgement is the
agent's job (see CLAUDE.md).

What goes out: a numbered overlay per page so regions can be referred to
unambiguously, a crop per region so the text is legible, and a markdown
briefing of series context. What comes back is validated before it is stored,
and what the agent *learned* -- new terms, character observations, a summary --
is folded into the series so the next chapter inherits it.
"""

from __future__ import annotations

from pathlib import Path

from comic.paths import ChapterPaths
from comic.schemas import ChapterState, ChapterTranslation, Gate, TranslationEntry
from comic.stages import analysis_for
from comic.state.store import write_json
from comic.vision.analyze import crop_region, overlay_regions, write_crop
from comic.vision.detector import load_page


#: Average Vietnamese glyph advance, as a fraction of the font size.
LATIN_CHAR_EM = 0.52
#: Line spacing. Vietnamese stacks diacritics, so lines cannot sit tight.
LINE_HEIGHT_EM = 1.25
#: How far below the source lettering the typesetter may go. Vietnamese runs
#: much longer than CJK for the same line, so setting it smaller than the
#: original is normal practice, not a failure.
SHRINK_ALLOWANCE = 0.7
#: Nothing legible goes below this, whatever the arithmetic says.
MIN_FONT_PX = 9.0


def suggested_chars(width: int, height: int, source_chars: int = 0) -> int:
    """Rough Vietnamese character budget for a bubble.

    Sized from the *source lettering*, which the geometry reveals: CJK glyphs
    are square and tile their box, so a region holding ``n`` characters in
    ``width x height`` was set at roughly ``sqrt(width * height / n)``. Scaling
    off that keeps the budget honest at any page resolution, where a fixed
    pixel assumption cannot.

    An earlier version pinned the font to ``height / 3.2``, which forces about
    three lines however much text there is. It offered 4 characters for a line
    whose Vietnamese needs 36 -- a budget wrong by that much is worse than
    none, because it argues for mangling the translation.

    Precision is not the point; the typesetter measures properly later. The
    point is to flag at *translation* time when a line cannot fit, because
    shortening it is a translation decision, not a rendering one.
    """
    area = float(width) * float(height)
    if area <= 0:
        return 1
    if source_chars > 0:
        source_font = (area / source_chars) ** 0.5
    else:
        # No source to measure: assume lettering sized to about six lines.
        source_font = height / 6.0
    font = max(MIN_FONT_PX, source_font * SHRINK_ALLOWANCE)
    per_line = max(1, int(width / (font * LATIN_CHAR_EM)))
    lines = max(1, int(height / (font * LINE_HEIGHT_EM)))
    return per_line * lines


def open_translate_gate(
    paths: ChapterPaths,
    state: ChapterState,
    only: list[int] | None = None,
    previous_error: str | None = None,
    limits: dict[str, int] | None = None,
) -> tuple[Path, int]:
    """Write everything the agent needs to translate the chapter.

    ``limits`` carries `retranslate_shorter` verdicts from `visual_qa`: region
    id -> the character count the line must come under. When it is given, the
    gate narrows to exactly those regions, because asking for a whole chapter
    again in order to shorten three lines invites the rest to drift.
    """
    from comic.context.builder import build_context
    from comic.handoff.schemas import (
        TranslateItem,
        TranslatePageContext,
        TranslateRequest,
    )

    gate_dir = paths.gate_dir(Gate.TRANSLATE.value)
    gate_dir.mkdir(parents=True, exist_ok=True)

    pages: list[TranslatePageContext] = []
    total = 0

    for record in state.pages:
        # An uploader's insert is not the comic. Its lettering reads as real
        # text at full confidence -- a "do not repost" banner is words on a
        # card -- so nothing upstream drops it and it arrives here looking
        # exactly like narration.
        if record.excluded:
            continue
        if only and record.page not in only:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        translatable = [
            r for r in analysis.regions if r.translatable and r.source_text.strip()
        ]
        if limits:
            translatable = [r for r in translatable if r.id in limits]
        if not translatable:
            continue

        image = load_page(paths.source_pages / record.filename)

        overlay_path = gate_dir / f"page_{record.page:03d}_context.png"
        write_crop(overlay_regions(image, analysis), overlay_path)

        items: list[TranslateItem] = []
        for region in translatable:
            crop_path = gate_dir / f"{region.id}.png"
            write_crop(crop_region(image, region, pad=10, scale=2), crop_path)
            items.append(
                TranslateItem(
                    region_id=region.id,
                    page=record.page,
                    panel_index=region.panel_index,
                    reading_order=region.reading_order,
                    kind=region.kind,
                    source_text=region.source_text,
                    crop_path=str(crop_path),
                    fit_width=region.bbox.width,
                    fit_height=region.bbox.height,
                    suggested_max_chars=suggested_chars(
                        region.bbox.width,
                        region.bbox.height,
                        len(region.source_text.strip()),
                    ),
                    must_fit_chars=(limits or {}).get(region.id),
                    shorten_reason=(
                        "visual_qa: the rendered line overran its bubble"
                        if (limits or {}).get(region.id)
                        else None
                    ),
                )
            )
            total += 1

        pages.append(
            TranslatePageContext(
                page=record.page, overlay_path=str(overlay_path), items=items
            )
        )

    context_path = paths.workspace / "translate_context.md"
    context_path.write_text(
        build_context(
            state.series, state.chapter,
            state.source_language, state.target_language,
        ),
        encoding="utf-8",
    )

    request = TranslateRequest(
        series=state.series,
        chapter=state.chapter,
        source_language=state.source_language,
        target_language=state.target_language,
        context_path=str(context_path),
        pages=pages,
        previous_error=previous_error,
        instructions=(
            (
                "Some lines did not fit when rendered. Rewrite ONLY the "
                "regions listed, each under its `must_fit_chars` limit, "
                "keeping the meaning and the established forms of address. "
            )
            if limits
            else ""
        ) + (
            "Read the context file first, then translate every region into "
            "Vietnamese. The overlay for each page shows the numbered regions "
            "in reading order; the per-region crops show the text itself. "
            "Translate what each line is doing, not word for word. Write your "
            "answer to the response file, then run `comic resume`."
        ),
    )
    request_path = paths.gate_request(Gate.TRANSLATE.value)
    write_json(request_path, request)
    return request_path, total


def merge_addresses(characters: dict, pairs: list) -> int:
    """Pin the forms of address this chapter used. Returns how many were new.

    An established pair wins over a newly reported one. Pronouns drifting
    between chapters reads as a different relationship, which is the most
    noticeable failure a series can have, so changing a pinned pair is an
    explicit profile edit rather than something a later chapter does on its
    own (spec 17).
    """
    from comic.memory.profile import Address, Character

    pinned = 0
    for pair in pairs:
        character = characters.get(pair.speaker) or Character(name=pair.speaker)
        if pair.addressee not in character.addresses:
            character.addresses[pair.addressee] = Address(
                self_term=pair.self_term,
                other_term=pair.other_term,
                formality=pair.formality,
                note=pair.note,
            )
            pinned += 1
        characters[pair.speaker] = character
    return pinned


def _source_text_for(paths: ChapterPaths, state: ChapterState) -> dict[str, str]:
    """region id -> source text, read once rather than per entry."""
    lookup: dict[str, str] = {}
    for record in state.pages:
        if record.excluded:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for region in analysis.regions:
            lookup[region.id] = region.source_text
    return lookup


def apply_translation(
    paths: ChapterPaths, state: ChapterState
) -> tuple[int, list[str], list[str]]:
    """Store the translation and fold what was learned back into the series.

    Returns (entries stored, things learned, problems). Any problem means
    nothing is written: a half-applied translation is worse than none, because
    the missing lines are invisible downstream.
    """
    from pydantic import ValidationError

    from comic.handoff.schemas import TranslateResponse
    from comic.memory.profile import (
        ChapterSummary,
        Character,
        GlossaryEntry,
        SeriesMemory,
    )

    path = paths.gate_response(Gate.TRANSLATE.value)
    if not path.exists():
        return 0, [], [f"no response at {path}"]

    try:
        response = TranslateResponse.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValidationError as exc:
        return 0, [], [f"response does not match the schema:\n{exc}"]

    sources = _source_text_for(paths, state)
    problems: list[str] = []

    # A typo in a region id would silently drop a line from the chapter.
    unknown = sorted({i.region_id for i in response.items} - set(sources))
    if unknown:
        problems.append(f"unknown region(s): {', '.join(unknown[:6])}")

    from comic.paths import SeriesPaths
    from comic.typesetting.render import voice_uses

    voices = voice_uses(SeriesPaths(state.series).fonts_file)
    entries: list[TranslationEntry] = []
    for item in response.items:
        if item.region_id in unknown:
            continue
        if item.voice and item.voice not in voices:
            known = ", ".join(sorted(voices)) or "none defined"
            problems.append(f"{item.region_id}: unknown voice {item.voice!r} ({known})")
            continue
        if not item.target_text.strip():
            problems.append(f"{item.region_id}: empty translation")
            continue
        entries.append(
            TranslationEntry(
                region_id=item.region_id,
                source_text=sources.get(item.region_id, ""),
                target_text=item.target_text.strip(),
                speaker=item.speaker,
                addressee=item.addressee,
                note=item.note,
                voice=item.voice,
            )
        )

    if problems:
        return 0, [], problems

    # Merge, never replace. A chapter can be translated in several passes --
    # the gate itself reports what is still missing, which only makes sense if
    # coming back to finish the rest keeps what was already done. Overwriting
    # silently discarded an entire earlier pass.
    merged: dict[str, TranslationEntry] = {}
    if paths.translation_file.exists():
        from comic.state.store import read_json

        previous = read_json(paths.translation_file, ChapterTranslation)
        merged = dict(previous.by_region())  # type: ignore[union-attr]
    for entry in entries:
        merged[entry.region_id] = entry

    ordered = sorted(merged.values(), key=lambda e: e.region_id)
    write_json(
        paths.translation_file,
        ChapterTranslation(
            series=state.series, chapter=state.chapter, entries=ordered
        ),
    )

    learned: list[str] = []
    memory = SeriesMemory(state.series)
    memory.ensure_initialised()

    if response.new_glossary:
        added = memory.merge_glossary([
            GlossaryEntry(source=g.source, target=g.target, note=g.note)
            for g in response.new_glossary
        ])
        if added:
            learned.append(f"{added} new glossary term(s)")

    if response.character_notes or response.address_pairs:
        characters = memory.load_characters()
        for note in response.character_notes:
            character = characters.get(note.name) or Character(name=note.name)
            if note.speech_style:
                character.speech_style = note.speech_style
            if note.personality:
                character.personality = note.personality
            if note.note and note.note not in character.notes:
                character.notes.append(note.note)
            characters[note.name] = character

        pinned = merge_addresses(characters, response.address_pairs)
        memory.save_characters(characters)
        if response.character_notes:
            learned.append(f"{len(response.character_notes)} character note(s)")
        if pinned:
            learned.append(f"{pinned} form(s) of address pinned")

    if response.chapter_summary:
        memory.save_summary(
            ChapterSummary(
                chapter=state.chapter,
                summary=response.chapter_summary,
                characters=[n.name for n in response.character_notes],
            )
        )
        learned.append("chapter summary")

    return len(entries), learned, []


def missing_translations(
    paths: ChapterPaths, state: ChapterState
) -> list[str]:
    """Regions that still have no Vietnamese, so a partial run is visible."""
    from comic.state.store import read_json

    if not paths.translation_file.exists():
        done: set[str] = set()
    else:
        translation = read_json(paths.translation_file, ChapterTranslation)
        done = set(translation.by_region())  # type: ignore[union-attr]

    pending: list[str] = []
    for record in state.pages:
        # Excluded pages hold no work. Counting their regions as pending would
        # leave the chapter permanently short of translated, so it could never
        # be marked complete.
        if record.excluded:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for region in analysis.regions:
            if region.translatable and region.source_text.strip():
                if region.id not in done:
                    pending.append(region.id)
    return pending
