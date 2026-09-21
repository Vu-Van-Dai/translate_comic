"""Stage implementations: detect, OCR, and the ocr_review gate.

Each stage reads and writes documents under ``workspace/``, so any one of them
can be re-run for a single page without touching the rest. Where a stage cannot
decide something safely it opens a gate rather than guessing -- see CLAUDE.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from comic.handoff.schemas import OcrReviewItem, OcrReviewRequest
from comic.ocr.registry import (
    LOW_CONFIDENCE,
    OcrError,
    read_region,
    unread_mask_fraction,
)
from comic.paths import ChapterPaths
from comic.schemas import (
    ChapterState,
    Gate,
    PageAnalysis,
    PageRecord,
    Stage,
    StageStatus,
    TextRegion,
)
from comic.state.store import read_json, write_json
from comic.vision.analyze import (
    analyze_page,
    contact_sheet,
    crop_region,
    load_mask,
    overlay_regions,
    save_mask,
    write_crop,
)
from comic.vision.detector import load_page

log = logging.getLogger(__name__)

#: Upscale used for crops handed to the agent, which must be legible by eye.
REVIEW_CROP_SCALE = 4


@dataclass
class StageOutcome:
    pages_done: int = 0
    regions: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)


def _pages_to_process(
    state: ChapterState, stage: Stage, only: list[int] | None, force: bool
) -> list[PageRecord]:
    pages = state.pages
    if only:
        pages = [p for p in pages if p.page in only]
    if not force:
        pages = [p for p in pages if p.status_of(stage) is not StageStatus.DONE]
    return pages


def analysis_for(paths: ChapterPaths, page: int) -> PageAnalysis | None:
    path = paths.analysis_file(page)
    if not path.exists():
        return None
    return read_json(path, PageAnalysis)  # type: ignore[return-value]


def run_slice(
    paths: ChapterPaths,
    state: ChapterState,
    force: bool = False,
    progress=None,
):
    """Re-cut a long strip into pages, if this chapter is one.

    Returns the ``SliceReport``, or None when nothing needed cutting -- a paged
    source, or a "longstrip" host that happens to serve page-shaped files.

    This rewrites ``state.pages`` wholesale, because after a re-cut the old
    page numbers describe files that no longer exist. Every per-page stage
    status resets with them; anything already computed against the uncut
    slices would be pointing at the wrong pixels.
    """
    from comic.schemas import PageKind
    from comic.vision.strip import needs_slicing, slice_strip

    if state.page_kind is not PageKind.LONGSTRIP:
        return None

    sources = sorted(paths.original.glob("*.*"))
    if not sources:
        return None
    if not force and not needs_slicing(sources):
        log.info("longstrip source is already page-shaped; not re-cutting")
        return None

    report = slice_strip(sources, paths.pages, progress=progress)
    if not report.spans:
        return report

    from PIL import Image

    pages: list[PageRecord] = []
    for number in range(1, report.page_count + 1):
        filename = f"{number:03d}.png"
        with Image.open(paths.pages / filename) as handle:
            width, height = handle.size
        pages.append(
            PageRecord(page=number, filename=filename, width=width, height=height)
        )
    state.pages = pages
    state.page_count = len(pages)
    return report


def run_detect(
    paths: ChapterPaths,
    state: ChapterState,
    only: list[int] | None = None,
    force: bool = False,
    progress=None,
) -> StageOutcome:
    """Find text regions on every page that still needs them."""
    outcome = StageOutcome()
    todo = _pages_to_process(state, Stage.DETECT, only, force)

    for record in todo:
        source = paths.source_pages / record.filename
        if not source.exists():
            outcome.note(f"page {record.page}: {record.filename} is missing")
            record.stages[Stage.DETECT] = StageStatus.FAILED
            continue

        analysis, mask = analyze_page(source, record.page, state.reading_direction)
        write_json(paths.analysis_file(record.page), analysis)
        save_mask(mask, paths.mask_file(record.page))

        record.width, record.height = analysis.width, analysis.height
        record.stages[Stage.DETECT] = StageStatus.DONE
        outcome.pages_done += 1
        outcome.regions += len(analysis.regions)
        if progress:
            progress(record.page, len(analysis.regions))

    return outcome


def run_ocr(
    paths: ChapterPaths,
    state: ChapterState,
    only: list[int] | None = None,
    force: bool = False,
    progress=None,
) -> tuple[StageOutcome, list[tuple[PageAnalysis, TextRegion]]]:
    """Read every translatable region. Returns the readings that look wrong."""
    outcome = StageOutcome()
    suspicious: list[tuple[PageAnalysis, TextRegion]] = []
    todo = _pages_to_process(state, Stage.OCR, only, force)

    for record in todo:
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            outcome.note(f"page {record.page}: not detected yet")
            continue

        image = load_page(paths.source_pages / record.filename)
        # The detector already found where the text is. Comparing that against
        # what OCR actually covered is the only way to notice a reading that is
        # confident and incomplete.
        mask_file = paths.mask_file(record.page)
        mask = load_mask(mask_file) if mask_file.exists() else None
        unread = 0

        for region in analysis.regions:
            # SFX is left in the artwork, so there is nothing to read (spec 27).
            if not region.translatable:
                continue
            if region.source_text and not force:
                continue
            # A reading the agent checked by eye outranks anything an engine
            # produces, so --force re-reads the machine's work, not the
            # agent's. Without this the region keeps its reviewed flag while
            # its text is quietly replaced by an unverified guess.
            if region.ocr_reviewed:
                continue

            crop = crop_region(image, region)
            # The mask is handed over as well as measured against: an engine
            # that has to find its own lines otherwise finds the bubble outline
            # too, and reads it as punctuation.
            region_mask = crop_region(mask, region) if mask is not None else None
            try:
                result = read_region(
                    crop,
                    state.source_language,
                    vertical=region.is_vertical,
                    mask=region_mask,
                )
            except OcrError as exc:
                outcome.note(str(exc))
                return outcome, suspicious

            region.source_text = result.text
            region.source_language = state.source_language
            region.ocr_confidence = result.confidence
            region.ocr_provider = result.provider
            region.ocr_unread = (
                unread_mask_fraction(region_mask, result.boxes)
                if region_mask is not None
                else None
            )
            outcome.regions += 1
            if result.suspicious or region.reading_looks_truncated:
                unread += 1
                suspicious.append((analysis, region))

        write_json(paths.analysis_file(record.page), analysis)
        record.stages[Stage.OCR] = StageStatus.DONE
        outcome.pages_done += 1
        if progress:
            progress(record.page, len(analysis.regions), unread)

    # The gate must reflect the chapter's current state, not just this run's
    # work. Without this a re-run reports "all sound" while regions from an
    # earlier run are still sitting unreviewed.
    already = {region.id for _analysis, region in suspicious}
    for record in state.pages:
        if only and record.page not in only:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for region in analysis.regions:
            if not region.translatable or region.ocr_reviewed:
                continue
            if region.id in already:
                continue
            if region.ocr_confidence is None:
                continue
            from comic.ocr.registry import OcrOutcome

            outcome_for = OcrOutcome(
                region.source_text, region.ocr_confidence, region.ocr_provider or ""
            )
            if outcome_for.suspicious or region.reading_looks_truncated:
                suspicious.append((analysis, region))

    return outcome, suspicious


def _review_reason(region: TextRegion) -> str:
    """Why this reading is being shown to the agent.

    Worth stating precisely. A truncated reading arrives at full confidence, so
    calling it "low confidence (1.00)" would send the agent looking for the
    wrong problem -- the text that is there is right, and the point is the text
    that is missing.
    """
    if not region.source_text.strip():
        return "engine returned nothing"
    reasons = []
    if region.reading_looks_truncated:
        share = (region.ocr_unread or 0.0) * 100
        reasons.append(
            f"{share:.0f}% of the detected text was never read -- "
            "look for words the engine missed entirely"
        )
    confidence = region.ocr_confidence
    if confidence is not None and confidence < LOW_CONFIDENCE:
        reasons.append(f"low confidence ({confidence:.2f})")
    return "; ".join(reasons) or "flagged for review"


def open_ocr_review(
    paths: ChapterPaths,
    state: ChapterState,
    suspicious: list[tuple[PageAnalysis, TextRegion]],
    previous_error: str | None = None,
) -> Path:
    """Write the crops and the request the agent needs to fix bad readings."""
    gate_dir = paths.gate_dir(Gate.OCR_REVIEW.value)
    gate_dir.mkdir(parents=True, exist_ok=True)

    items: list[OcrReviewItem] = []
    sheets: dict[int, str] = {}
    by_page: dict[int, list[TextRegion]] = {}
    for analysis, region in suspicious:
        by_page.setdefault(analysis.page, []).append(region)

    for page, regions in sorted(by_page.items()):
        image = load_page(paths.source_pages / f"{page:03d}.png")
        sheet_crops: list[tuple[str, object]] = []
        for region in regions:
            crop = crop_region(image, region, pad=10, scale=REVIEW_CROP_SCALE)
            crop_path = gate_dir / f"{region.id}.png"
            write_crop(crop, crop_path)
            sheet_crops.append((region.id, crop))

            items.append(
                OcrReviewItem(
                    region_id=region.id,
                    page=page,
                    crop_path=str(crop_path),
                    kind=region.kind,
                    is_vertical=region.is_vertical,
                    source_language=region.source_language,
                    engine_text=region.source_text,
                    engine_confidence=region.ocr_confidence,
                    reason=_review_reason(region),
                )
            )

        # One labelled sheet per page. Reading twenty crops as twenty separate
        # images costs twenty times the attention of reading one sheet, and the
        # review gate is now the most expensive stage in the pipeline.
        sheet_path = gate_dir / f"page_{page:03d}_sheet.png"
        write_crop(contact_sheet(sheet_crops), sheet_path)
        sheets[page] = str(sheet_path)

    request = OcrReviewRequest(
        series=state.series,
        chapter=state.chapter,
        items=items,
        sheets=sheets,
        previous_error=previous_error,
        instructions=(
            "Read `sheets[page]` -- one labelled image holding every crop on "
            "that page -- and transcribe exactly what is printed, in the "
            "source language -- do not translate. Return an empty string for a "
            "region that holds no readable text; it will be dropped. Write your "
            "answer to the response file, then run `comic resume`."
        ),
    )
    request_path = paths.gate_request(Gate.OCR_REVIEW.value)
    write_json(request_path, request)
    return request_path


def write_page_overlays(paths: ChapterPaths, pages: list[int]) -> list[Path]:
    """Numbered region overlays, so a whole page can be checked at a glance."""
    out: list[Path] = []
    target = paths.gate_dir("overview")
    target.mkdir(parents=True, exist_ok=True)
    for page in pages:
        analysis = analysis_for(paths, page)
        if analysis is None:
            continue
        image = load_page(paths.source_pages / f"{page:03d}.png")
        path = target / f"page_{page:03d}.png"
        write_crop(overlay_regions(image, analysis), path)
        out.append(path)
    return out


def apply_ocr_review(
    paths: ChapterPaths, state: ChapterState
) -> tuple[int, int, list[str]]:
    """Apply the agent's transcriptions.

    Returns (corrected, dropped, problems). A region answered with an empty
    string holds no readable text and is removed, which is the honest outcome
    for a mask artefact that only looked like lettering.
    """
    from pydantic import ValidationError

    from comic.handoff.schemas import OcrReviewResponse

    path = paths.gate_response(Gate.OCR_REVIEW.value)
    if not path.exists():
        return 0, 0, [f"no response at {path}"]

    try:
        response = OcrReviewResponse.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValidationError as exc:
        return 0, 0, [f"response does not match the schema:\n{exc}"]

    answers = {item.region_id: item for item in response.items}
    corrected = dropped = 0
    problems: list[str] = []
    seen: set[str] = set()

    for record in state.pages:
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        keep: list[TextRegion] = []
        changed = False

        for region in analysis.regions:
            answer = answers.get(region.id)
            if answer is None:
                keep.append(region)
                continue
            seen.add(region.id)

            if not answer.text.strip():
                dropped += 1
                changed = True
                continue    # drop it entirely

            region.source_text = answer.text.strip()
            region.ocr_reviewed = True
            # A human reading is authoritative, so it stops being suspicious.
            region.ocr_confidence = 1.0
            region.ocr_provider = "agent"
            if answer.kind is not None:
                region.kind = answer.kind
            if answer.is_vertical is not None:
                region.is_vertical = answer.is_vertical
            keep.append(region)
            corrected += 1
            changed = True

        if changed:
            analysis.regions = keep
            write_json(paths.analysis_file(record.page), analysis)

    unknown = sorted(set(answers) - seen)
    if unknown:
        problems.append(f"response names unknown region(s): {', '.join(unknown[:6])}")
    return corrected, dropped, problems
