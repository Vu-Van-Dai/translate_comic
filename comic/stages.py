"""Stage implementations: detect, OCR, and the ocr_review gate.

Each stage reads and writes documents under ``workspace/``, so any one of them
can be re-run for a single page without touching the rest. Where a stage cannot
decide something safely it opens a gate rather than guessing -- see CLAUDE.md.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from comic.handoff.schemas import OcrReviewItem, OcrReviewRequest
from comic.ocr.registry import (
    LOW_CONFIDENCE,
    OcrError,
    language_mismatch,
    read_region,
    reading_looks_fused,
    unread_mask_fraction,
)
from comic.paths import ChapterPaths
from comic.schemas import (
    ChapterState,
    Gate,
    PageAnalysis,
    PageRecord,
    ReadingDirection,
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

#: Distinct pages a reading must appear on before it is treated as a watermark
#: rather than dialogue.
#:
#: Measured on a real scanlation chapter: the site's watermark read identically
#: on **11 different pages**, while no genuine line repeated on more than 2 --
#: a clean separation, so 3 has margin on both sides. It is only a *flag*: the
#: agent decides, because a catchphrase could in principle recur.
#:
#: This matters beyond tidiness. A watermark is baked into the artwork, so
#: translating it means the cleaner erases part of the art and the typesetter
#: sets Vietnamese over the hole. Dropping it at the gate leaves it untouched.
WATERMARK_PAGES = 3


def _repeated_readings(paths: ChapterPaths, state: ChapterState) -> set[str]:
    """Normalised readings that occur on ``WATERMARK_PAGES`` or more pages."""
    pages: dict[str, set[int]] = {}
    for record in _included(state):
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for region in analysis.regions:
            key = region.source_text.strip().upper().replace(" ", "")
            if key:
                pages.setdefault(key, set()).add(record.page)
    return {k for k, v in pages.items() if len(v) >= WATERMARK_PAGES}


@dataclass
class StageOutcome:
    pages_done: int = 0
    regions: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)


def _included(state: ChapterState) -> list[PageRecord]:
    """The pages that are part of the comic.

    Excluded pages are the uploader's, not the author's -- a credits card, a
    "read it on <host>" notice. They must not reach a gate (the agent would be
    asked to transcribe a banner), must not count toward watermark detection,
    and must not vote in the declared-language check, where a page of English
    boilerplate in a Korean chapter is exactly the wrong evidence.
    """
    return [p for p in state.pages if not p.excluded]


def _pages_to_process(
    state: ChapterState, stage: Stage, only: list[int] | None, force: bool
) -> list[PageRecord]:
    pages = [p for p in state.pages if not p.excluded]
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

    # Already cut, and the state agrees with what is on disk. Slicing again
    # would rewrite ``state.pages`` and reset every per-page stage status, so
    # a second ``comic analyze`` would silently discard the whole chapter's
    # detection and OCR -- ~10 minutes for a 55-page webtoon, and it breaks
    # the resumability every other stage relies on. ``--force`` re-cuts.
    done = sorted(p.name for p in paths.pages.glob("*.png"))
    if not force and done and done == sorted(p.filename for p in state.pages):
        log.info("already sliced into %d page(s); not re-cutting", len(done))
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


def detect_one(
    paths: ChapterPaths, page: int, filename: str, direction: ReadingDirection
) -> dict:
    """Detect one page and write its analysis and mask. Returns a summary.

    Kept separate from ``run_detect`` so the same code serves the serial path
    and the worker pool, and so nothing in here touches shared state: every
    write goes to a path derived from the page number, which no other page
    can collide with.
    """
    source = paths.source_pages / filename
    if not source.exists():
        return {"page": page, "ok": False,
                "note": f"page {page}: {filename} is missing"}
    analysis, mask = analyze_page(source, page, direction)
    write_json(paths.analysis_file(page), analysis)
    save_mask(mask, paths.mask_file(page))
    return {"page": page, "ok": True, "note": None,
            "width": analysis.width, "height": analysis.height,
            "regions": len(analysis.regions)}


def _detect_job(payload: tuple) -> dict:
    """Pool entry point. Takes only picklable values and rebuilds the paths."""
    series, chapter, page, filename, direction = payload
    return detect_one(
        ChapterPaths(series, chapter), page, filename, ReadingDirection(direction)
    )


def detect_workers(pages: int, workers: int | None = None) -> int:
    """How many detector processes to run at once.

    Bounded by memory rather than cores: each worker loads its own copy of the
    detector, measured at ~1.1 GB resident, so eight of them would need more
    than this machine has free. The cap keeps the pool inside a sane footprint
    while still using the cores that were sitting idle -- detection ran
    single-threaded on an 8-core box, which is why a chapter took 45s a page.
    """
    if workers is not None:
        return max(1, workers)
    return max(1, min(pages, cpu_budget(), DETECT_MAX_WORKERS))


def cpu_budget() -> int:
    """Worker processes a stage may start; see ``comic.cpu``."""
    from comic.cpu import workers

    return workers()


#: Ceiling on concurrent detector processes; see ``detect_workers``.
DETECT_MAX_WORKERS = 4


def run_detect(
    paths: ChapterPaths,
    state: ChapterState,
    only: list[int] | None = None,
    force: bool = False,
    progress=None,
    workers: int | None = None,
) -> StageOutcome:
    """Find text regions on every page that still needs them."""
    outcome = StageOutcome()
    todo = _pages_to_process(state, Stage.DETECT, only, force)
    if not todo:
        return outcome

    count = detect_workers(len(todo), workers)
    if count <= 1 or len(todo) == 1:
        results = [
            detect_one(paths, r.page, r.filename, state.reading_direction)
            for r in todo
        ]
    else:
        payloads = [
            (paths.series, paths.chapter, r.page, r.filename,
             state.reading_direction.value)
            for r in todo
        ]
        with ProcessPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(_detect_job, payloads))

    by_page = {r.page: r for r in todo}
    for result in sorted(results, key=lambda r: r["page"]):
        record = by_page[result["page"]]
        if not result["ok"]:
            outcome.note(result["note"])
            record.stages[Stage.DETECT] = StageStatus.FAILED
            continue
        record.width, record.height = result["width"], result["height"]
        record.stages[Stage.DETECT] = StageStatus.DONE
        outcome.pages_done += 1
        outcome.regions += result["regions"]
        if progress:
            progress(result["page"], result["regions"])

    return outcome


def ocr_one(
    paths: ChapterPaths, page: int, filename: str, language: str, force: bool
) -> dict:
    """Read one page's regions and write its analysis. Returns a summary.

    Shared by the serial path and the worker pool. Nothing here touches state
    another page can see: the only write is this page's own analysis file.

    ``flagged`` carries the ids this page thought looked wrong. The caller
    pairs them back up with the analysis on disk -- returning whole
    PageAnalysis objects through the pool would copy every region twice.
    """
    read_count = 0
    unread = 0
    flagged: list[str] = []
    analysis = analysis_for(paths, page)
    if analysis is None:
        return {"page": page, "ok": False,
                "note": f"page {page}: not detected yet",
                "regions": 0, "unread": 0, "flagged": []}

    image = load_page(paths.source_pages / filename)
    # The detector already found where the text is. Comparing that against
    # what OCR actually covered is the only way to notice a reading that is
    # confident and incomplete.
    mask_file = paths.mask_file(page)
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
                language,
                vertical=region.is_vertical,
                mask=region_mask,
            )
        except OcrError as exc:
            return {"page": page, "ok": False, "note": str(exc),
                    "regions": read_count, "unread": unread, "flagged": flagged}

        region.source_text = result.text
        region.source_language = language
        region.ocr_confidence = result.confidence
        region.ocr_provider = result.provider
        region.ocr_unread = (
            unread_mask_fraction(region_mask, result.boxes)
            if region_mask is not None
            else None
        )
        read_count += 1
        if (
            result.suspicious
            or region.reading_looks_truncated
            or reading_looks_fused(result.text)
        ):
            unread += 1
            flagged.append(region.id)

    write_json(paths.analysis_file(page), analysis)
    return {"page": page, "ok": True, "note": None,
            "regions": read_count, "unread": unread, "flagged": flagged}


def _ocr_job(payload: tuple) -> dict:
    series, chapter, page, filename, language, force = payload
    return ocr_one(ChapterPaths(series, chapter), page, filename, language, force)


def run_ocr(
    paths: ChapterPaths,
    state: ChapterState,
    only: list[int] | None = None,
    force: bool = False,
    progress=None,
    workers: int | None = None,
) -> tuple[StageOutcome, list[tuple[PageAnalysis, TextRegion]]]:
    """Read every translatable region. Returns the readings that look wrong."""
    outcome = StageOutcome()
    suspicious: list[tuple[PageAnalysis, TextRegion]] = []
    todo = _pages_to_process(state, Stage.OCR, only, force)

    count = detect_workers(len(todo), workers)
    if count <= 1 or len(todo) <= 1:
        results = [
            ocr_one(paths, r.page, r.filename, state.source_language, force)
            for r in todo
        ]
    else:
        payloads = [
            (paths.series, paths.chapter, r.page, r.filename,
             state.source_language, force)
            for r in todo
        ]
        with ProcessPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(_ocr_job, payloads))

    by_page = {r.page: r for r in todo}
    for result in sorted(results, key=lambda r: r["page"]):
        record = by_page[result["page"]]
        analysis = analysis_for(paths, result["page"])
        if analysis is None:
            outcome.note(f"page {result['page']}: not detected yet")
            continue
        if result["note"]:
            outcome.note(result["note"])
        if not result["ok"]:
            record.stages[Stage.OCR] = StageStatus.FAILED
            continue
        outcome.regions += result["regions"]
        by_id = {r.id: r for r in analysis.regions}
        suspicious += [
            (analysis, by_id[rid]) for rid in result["flagged"] if rid in by_id
        ]
        record.stages[Stage.OCR] = StageStatus.DONE
        outcome.pages_done += 1
        if progress:
            progress(result["page"], len(analysis.regions), result["unread"])

    # The gate must reflect the chapter's current state, not just this run's
    # work. Without this a re-run reports "all sound" while regions from an
    # earlier run are still sitting unreviewed.
    # A wrong-language engine produces confident nonsense, and the guard in
    # ``provider_for`` cannot catch the case where the *adapter* is wrong: it
    # is told `ko`, loads a Korean recogniser and aims it at English. Compare
    # the script actually read against the one declared.
    readings: list[str] = []
    for record in _included(state):
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        readings += [r.source_text for r in analysis.regions if r.translatable]
    warning = language_mismatch(readings, state.source_language)
    if warning:
        outcome.note(warning)

    # Deliberately NOT scoped by ``only``. ``--page`` limits which pages are
    # *read*; it must not limit what the gate reports, because the gate is the
    # chapter's outstanding-work list and closing it lets the pipeline move on.
    # Measured: after `analyze -p 12 --force`, the request listed 3 regions
    # while 56 were still unreviewed -- answering those 3 and resuming would
    # have carried 53 unchecked readings, 14 of them empty, into translation.
    already = {region.id for _analysis, region in suspicious}
    for record in _included(state):
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
            if (
                outcome_for.suspicious
                or region.reading_looks_truncated
                or reading_looks_fused(region.source_text)
            ):
                suspicious.append((analysis, region))

    # A reading that recurs on many pages is the site's watermark or a credit
    # line baked into the artwork, not dialogue -- and it arrives at *full*
    # confidence, so nothing above catches it.
    repeated = _repeated_readings(paths, state)
    if repeated:
        flagged = {region.id for _analysis, region in suspicious}
        for record in _included(state):
            analysis = analysis_for(paths, record.page)
            if analysis is None:
                continue
            for region in analysis.regions:
                if not region.translatable or region.ocr_reviewed:
                    continue
                if region.id in flagged:
                    continue
                key = region.source_text.strip().upper().replace(" ", "")
                if key in repeated:
                    suspicious.append((analysis, region))

    return outcome, suspicious


def _review_reason(region: TextRegion, repeated_on: int = 0) -> str:
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
    if repeated_on >= WATERMARK_PAGES:
        reasons.append(
            f"this exact reading appears on {repeated_on} pages -- almost "
            "certainly the site's watermark or a credit baked into the art, "
            'not dialogue. Answer "" to drop it, so cleaning leaves the '
            "artwork alone"
        )
    if reading_looks_fused(region.source_text):
        reasons.append(
            "words are run together -- the recogniser dropped spaces inside a "
            "line, so type the words as they are printed"
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

    # How many distinct pages each reading occurs on, so the request can say
    # *why* a perfectly confident line is being questioned.
    spread: dict[str, set[int]] = {}
    for record in _included(state):
        page_analysis = analysis_for(paths, record.page)
        if page_analysis is None:
            continue
        for candidate in page_analysis.regions:
            key = candidate.source_text.strip().upper().replace(" ", "")
            if key:
                spread.setdefault(key, set()).add(record.page)

    for page, regions in sorted(by_page.items()):
        image = load_page(paths.page_file(page))
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
                    reason=_review_reason(
                        region,
                        len(spread.get(
                            region.source_text.strip().upper().replace(" ", ""),
                            set(),
                        )),
                    ),
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
        image = load_page(paths.page_file(page))
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
