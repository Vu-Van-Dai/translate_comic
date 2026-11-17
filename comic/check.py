"""The agent's transcription check (the operator's workflow, 2026-11-08).

Comes after the operator's Studio review and before translation. The operator
has already decided where every box is and what it is; the agent only checks
that the *source text* of each box is right, so translation starts from what is
printed. Logos, watermarks, credits and kept signage are not checked.

Unlike the old ``ocr_review`` gate this never deletes a region -- boxes are the
operator's call, made in Studio. A region the agent will not transcribe is
answered with ``skip``: it keeps the engine's reading, is still cleaned, is
never translated by the pipeline, and is listed in ``untranslated.txt``.

Files (in the chapter's ``handoff/``):

    check_request.json   every region to check, and the sheets to read
    check/page_NNN_sheet_K.png   labelled crops, a dozen per sheet
    check_response.json  the agent's answer (below), applied by `comic resume`

Response::

    {"items": [
        {"region_id": "p003_r02", "text": "WHAT DID YOU SAY?!"},
        {"region_id": "p004_r01", "kind": "shout"},
        {"region_id": "p007_r05", "skip": "reason for leaving it to the operator"}
    ]}

A region the answer does not name keeps its engine reading as correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, ValidationError

from comic.paths import ChapterPaths
from comic.schemas import Base, ChapterState, PageAnalysis, TextKind, TextRegion
from comic.state.store import read_json, write_json

GATE = "check"
#: Crops per sheet: enough to save reads, few enough that each stays legible.
SHEET_CROPS = 12
CROP_SCALE = 2
CHECKED_KINDS = frozenset({
    TextKind.DIALOGUE, TextKind.NARRATION, TextKind.THOUGHT,
    TextKind.SHOUT, TextKind.CAPTION, TextKind.SFX,
})


class CheckItem(Base):
    region_id: str
    page: int
    kind: TextKind
    engine_text: str
    confidence: float | None = None
    note: str = ""
    sheet: str


class CheckRequest(Base):
    series: str
    chapter: str
    items: list[CheckItem]
    sheets: list[str]
    instructions: str
    previous_error: str | None = None


class CheckAnswer(Base):
    region_id: str
    text: str | None = None
    kind: TextKind | None = None
    skip: str | None = None


class CheckResponse(Base):
    items: list[CheckAnswer] = Field(default_factory=list)


def _note(region: TextRegion) -> str:
    notes = []
    if not region.source_text.strip():
        notes.append("no text yet (new box?) -- transcribe it")
    elif region.ocr_provider == "operator":
        notes.append("typed by the operator in Studio")
    if region.edited:
        notes.append("box drawn/reshaped in Studio")
    if region.ocr_confidence is not None and region.ocr_confidence < 0.72:
        notes.append(f"low confidence {region.ocr_confidence:.2f}")
    return "; ".join(notes)


def _pages(paths: ChapterPaths, state: ChapterState,
           only: list[int] | None = None) -> list[tuple[int, PageAnalysis]]:
    out = []
    for record in state.pages:
        if getattr(record, "excluded", False) or (only and record.page not in only):
            continue
        f = paths.analysis_file(record.page)
        if f.exists():
            out.append((record.page, read_json(f, PageAnalysis)))
    return out


def unreviewed_pages(paths: ChapterPaths, state: ChapterState,
                     only: list[int] | None = None) -> list[int]:
    from comic.studio.store import reviewed_pages

    done = reviewed_pages(paths)
    return [page for page, _ in _pages(paths, state, only) if page not in done]


def open_check(paths: ChapterPaths, state: ChapterState, error: str | None = None,
               only: list[int] | None = None) -> Path:
    """Write the sheets and the request; return the request path."""
    from comic.vision.analyze import contact_sheet, crop_region, write_crop
    from comic.vision.detector import load_page

    gate_dir = paths.gate_dir(GATE)
    gate_dir.mkdir(parents=True, exist_ok=True)
    for old in gate_dir.glob("*.png"):
        old.unlink()

    items: list[CheckItem] = []
    sheets: list[str] = []
    for page, analysis in _pages(paths, state, only):
        regions = sorted((r for r in analysis.regions if r.kind in CHECKED_KINDS),
                         key=lambda r: r.reading_order)
        if not regions:
            continue
        image = load_page(paths.page_file(page))
        for k in range(0, len(regions), SHEET_CROPS):
            chunk = regions[k:k + SHEET_CROPS]
            sheet = gate_dir / f"page_{page:03d}_sheet_{k // SHEET_CROPS + 1}.png"
            write_crop(contact_sheet([(r.id, crop_region(image, r, pad=10, scale=CROP_SCALE))
                                      for r in chunk]), sheet)
            sheets.append(str(sheet))
            items.extend(CheckItem(
                region_id=r.id, page=page, kind=r.kind, engine_text=r.source_text,
                confidence=r.ocr_confidence, note=_note(r), sheet=str(sheet),
            ) for r in chunk)

    request = CheckRequest(
        series=state.series, chapter=state.chapter, items=items, sheets=sheets,
        previous_error=error,
        instructions=(
            "Read every sheet and compare each labelled crop with its engine_text. "
            "Write check_response.json naming ONLY regions that need a change: "
            "`text` = exactly what is printed, in the source language (never a "
            "translation); `kind` = a corrected kind; `skip` = a reason, for a "
            "region you leave to the operator (it keeps its text, is cleaned, and "
            "is listed in untranslated.txt). Regions are never deleted here -- "
            "boxes are the operator's call in Studio. Then run `comic resume`."
        ),
    )
    path = paths.gate_request(GATE)
    write_json(path, request)
    return path


def apply_check(paths: ChapterPaths, state: ChapterState) -> tuple[int, int, list[str]]:
    """Apply check_response.json. Returns (corrected, held, problems).

    Validates everything before writing anything: a bad answer changes nothing.
    """
    path = paths.gate_response(GATE)
    if not path.exists():
        return 0, 0, [f"no response at {path}"]
    try:
        response = CheckResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        return 0, 0, [f"response does not match the schema:\n{exc}"]

    answers: dict[str, CheckAnswer] = {}
    problems: list[str] = []
    for a in response.items:
        if a.region_id in answers:
            problems.append(f"{a.region_id} answered twice")
        if a.text is not None and not a.text.strip():
            problems.append(f"{a.region_id}: empty text -- use `skip`, or delete the box in Studio")
        if a.kind is not None and a.kind not in CHECKED_KINDS:
            problems.append(f"{a.region_id}: kind {a.kind.value} is set in Studio, not here")
        if a.text is None and a.kind is None and not (a.skip or "").strip():
            problems.append(f"{a.region_id}: nothing to change")
        answers[a.region_id] = a

    pages = _pages(paths, state)
    known = {r.id for _, analysis in pages for r in analysis.regions}
    unknown = sorted(set(answers) - known)
    if unknown:
        problems.append(f"unknown region(s): {', '.join(unknown[:6])}")
    if problems:
        return 0, 0, problems

    corrected = held = 0
    for page, analysis in pages:
        changed = False
        for i, region in enumerate(analysis.regions):
            a = answers.get(region.id)
            if a is None:
                continue
            update: dict = {}
            if a.text is not None:
                update.update(source_text=a.text.strip(), ocr_reviewed=True,
                              ocr_confidence=1.0, ocr_provider="agent")
                corrected += 1
            if a.kind is not None:
                update["kind"] = a.kind
            if (a.skip or "").strip():
                update["hold_reason"] = a.skip.strip()
                held += 1
                if a.skip.strip().lower().startswith("no lettering"):
                    # Nothing to erase: cleaning a box of pure art would paint over
                    # the drawing. Kept as drawn; the operator sees it in Studio/PS.
                    update["kind"] = TextKind.KEEP
            analysis.regions[i] = region.model_copy(update=update)
            changed = True
        if changed:
            write_json(paths.analysis_file(page), analysis)
    applied = path.with_name(path.stem + ".applied.json")
    path.replace(applied)
    return corrected, held, []


def pending(paths: ChapterPaths) -> bool:
    """Whether this chapter's open gate is the transcription check."""
    return paths.gate_request(GATE).exists()


def summary(paths: ChapterPaths) -> dict:
    data = json.loads(paths.gate_request(GATE).read_text(encoding="utf-8"))
    return {"regions": len(data["items"]), "sheets": len(data["sheets"])}
