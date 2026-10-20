"""Rendering pages, checking them, and applying the agent's fixes.

The render is deterministic and repeatable: every round rebuilds each page
from the **original**, applying the stored `RenderPlan`. Nothing is edited on
top of a previous render, so a fix can be changed or withdrawn and the page
comes back exactly as if it had never been applied.

What this module cannot do is decide whether a page reads well. Overflow and
leftover ink are measurable; "this line sits awkwardly over her face" is not.
So the pre-checks below are deliberately narrow -- they find what arithmetic
can find, and hand the page to the eye that can judge the rest.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

from comic.image_processing.clean import series_clean_mode
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from comic.handoff.schemas import (
    FixAction,
    QaFix,
    QaPreCheck,
    VisualQaPageItem,
    VisualQaRequest,
)
from comic.image_processing.clean import clean_page
from comic.image_processing.mask import fit_areas, region_glyphs
from comic.paths import ChapterPaths, SeriesPaths
from comic.schemas import (
    ChapterState,
    ChapterTranslation,
    Gate,
    PageAnalysis,
    RenderPlan,
    TextKind,
)
from comic.stages import analysis_for
from comic.state.store import read_json, write_json
from comic.typesetting.fit import Layout
from comic.typesetting.render import (
    DARK_BACKGROUND,
    background_luma,
    load_styles,
    render_page,
)
from comic.vision.analyze import load_mask, write_crop
from comic.vision.detector import load_page

#: A page's type should not vary wildly; this much smaller than the page's
#: median is worth a look even when it technically fits.
TINY_FONT_RATIO = 0.6
#: And below this in absolute terms it is simply too small to read.
TINY_FONT_PX = 11
#: Source ink still inside a region's area after cleaning, as a share of what
#: was there before. Above this, the erase did not take.
MAX_INK_RESIDUE = 0.15
#: Rounds of fixes before the chapter escalates to the operator.
MAX_QA_ROUNDS = 3


@dataclass
class RenderOutcome:
    pages: list[int] = field(default_factory=list)
    layouts: dict[str, Layout] = field(default_factory=dict)
    checks: list[QaPreCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    flat: int = 0
    inpainted: int = 0
    #: True while every page's artwork outside the masks is bit-identical.
    artwork_intact: bool = True


def plan_file(paths: ChapterPaths) -> Path:
    return paths.workspace / "render_plan.json"


def load_plan(paths: ChapterPaths, state: ChapterState) -> RenderPlan:
    path = plan_file(paths)
    if path.exists():
        return read_json(path, RenderPlan)  # type: ignore[return-value]
    return RenderPlan(series=state.series, chapter=state.chapter)


def save_plan(paths: ChapterPaths, plan: RenderPlan) -> None:
    write_json(plan_file(paths), plan)


def _ink(image: np.ndarray, inverted: bool = False) -> np.ndarray:
    """Pixels that contrast with the page: the lettering.

    Which way round depends on the panel. On an inverted panel the lettering is
    light and the background dark, so counting dark pixels measures the
    background instead -- that is how a cleaned region reported "159% residue".
    """
    grey = image if image.ndim == 2 else image.mean(axis=2)
    return grey > 128 if inverted else grey < 128


def _page_checks(
    analysis: PageAnalysis,
    layouts: dict[str, Layout],
    original: np.ndarray,
    cleaned: np.ndarray,
    areas: dict[str, np.ndarray],
    page_mask: np.ndarray,
) -> list[QaPreCheck]:
    """The findings arithmetic can produce, without an opinion about taste."""
    checks: list[QaPreCheck] = []
    sizes = [layout.font_size for layout in layouts.values()]
    median = sorted(sizes)[len(sizes) // 2] if sizes else 0

    for region_id, layout in sorted(layouts.items()):
        if not layout.fits:
            checks.append(
                QaPreCheck(
                    region_id=region_id,
                    code="overflow",
                    detail=(
                        f"does not fit: {len(layout.lines)} line(s) at "
                        f"{layout.font_size}px still overrun the bubble"
                    ),
                    severity="error",
                )
            )
        elif layout.font_size < TINY_FONT_PX:
            checks.append(
                QaPreCheck(
                    region_id=region_id,
                    code="tiny_font",
                    detail=(
                        f"{layout.font_size}px is below the {TINY_FONT_PX}px "
                        "floor. This is already the largest size that fits, so "
                        "font_delta upward will only overflow -- the bubble is "
                        "small. Use retranslate_shorter to buy room, or accept"
                    ),
                    severity="warning",
                )
            )
        elif median and layout.font_size < median * TINY_FONT_RATIO:
            checks.append(
                QaPreCheck(
                    region_id=region_id,
                    code="uneven_font",
                    detail=(
                        f"{layout.font_size}px against a page median of {median}px"
                    ),
                    severity="info",
                )
            )

    for region in analysis.regions:
        if region.id not in areas:
            continue
        # Decide which way round "ink" is from the region's own background.
        inverted = background_luma(cleaned, areas[region.id]) < DARK_BACKGROUND
        before = _ink(original, inverted)
        after = _ink(cleaned, inverted)
        # Ask about the pixels cleaning was told to erase, not the whole
        # bubble: the outline and any artwork beside it are meant to survive.
        spot = region_glyphs(page_mask, region) > 0
        was = int(before[spot].sum())
        if was < 40:
            continue
        left = int(after[spot].sum())
        if left > was * MAX_INK_RESIDUE:
            checks.append(
                QaPreCheck(
                    region_id=region.id,
                    code="ink_residue",
                    detail=(
                        f"{left / was:.0%} of the source lettering is still "
                        "visible after cleaning"
                    ),
                    severity="error",
                )
            )
    return checks


def render_one(
    paths: ChapterPaths,
    page: int,
    filename: str,
    text: dict,
    plan: RenderPlan,
    fonts: Path,
    model: Path | None,
) -> dict:
    """Clean and typeset one page. Self-contained, so it can run anywhere.

    It loads everything it needs from disk and returns plain data. That is what
    lets pages run in parallel: they share no state and never touch each
    other's pixels.
    """
    result: dict = {
        "page": page, "layouts": {}, "checks": [], "notes": [],
        "flat": 0, "inpainted": 0, "intact": True, "rendered": False,
    }
    analysis = analysis_for(paths, page)
    if analysis is None:
        return result
    if not any(text.get(r.id, "").strip() for r in analysis.regions):
        return result

    source = paths.source_pages / filename
    mask_path = paths.mask_file(page)
    if not source.exists() or not mask_path.exists():
        result["notes"].append("page %d: missing original or mask" % page)
        return result

    image = load_page(source)
    page_mask = load_mask(mask_path)
    areas = fit_areas(image, page_mask, analysis.regions)

    cleaned, touched, report = clean_page(
        image, page_mask, analysis.regions, areas,
        model=model, overrides=plan.overrides,
        inpaint_all=series_clean_mode(SeriesPaths(paths.series).fonts_file) == "lama",
    )
    outside = touched == 0
    if not bool((cleaned[outside] == image[outside]).all()):
        result["intact"] = False
        result["notes"].append(
            "page %d: artwork changed outside the cleaning mask" % page
        )
    result["flat"] = report.count("flat")
    result["inpainted"] = report.count("inpaint")
    result["notes"].extend(report.notes)

    # Lettering is a per-series choice, so the faces and the single ink colour
    # come from profiles/<series>/fonts.yaml when one exists.
    rendered, layouts = render_page(
        cleaned, analysis.regions, text, areas, fonts, plan.overrides,
        styles=load_styles(SeriesPaths(paths.series).fonts_file),
        page_mask=page_mask,
    )
    write_crop(cleaned, paths.cleaned / filename)
    write_crop(rendered, paths.translated / filename)

    result["layouts"] = layouts
    result["checks"] = _page_checks(
        analysis, layouts, image, cleaned, areas, page_mask
    ) + _placement_checks(analysis, text, image, page_mask, rendered, cleaned, areas)
    result["rendered"] = True
    return result


def _placement_checks(
    analysis: PageAnalysis,
    text: dict,
    image: np.ndarray,
    page_mask: np.ndarray,
    rendered: np.ndarray,
    cleaned: np.ndarray,
    areas: dict[str, np.ndarray],
) -> list[QaPreCheck]:
    """The centre and spill checks, on the pixels this render just produced.

    They used to be two scripts run by hand after every render; the findings
    are the same (``comic/qa/placement.py``). Warnings, not errors: dot-tone
    captions, SFX areas and shared bubbles raise false alarms, so each one is
    for the eye to judge.
    """
    from comic.qa.placement import check_page

    off, spills = check_page(
        image, page_mask, rendered, cleaned, analysis.regions, text, areas=areas
    )
    checks = [
        QaPreCheck(region_id=f.region_id, code="off_centre", detail=f.detail(),
                   severity="warning")
        for f in off
    ]
    checks += [
        QaPreCheck(region_id=s.region_id, code="spill", detail=s.detail(),
                   severity="warning")
        for s in spills
    ]
    return checks


def _job(payload: tuple) -> dict:
    """Process-pool entry point; must be importable, so it lives at module level."""
    series, chapter, page, filename, text, plan_json, fonts, model = payload
    return render_one(
        ChapterPaths(series, chapter), page, filename, text,
        RenderPlan.model_validate_json(plan_json),
        Path(fonts), Path(model) if model else None,
    )


def render_chapter(
    paths: ChapterPaths,
    state: ChapterState,
    plan: RenderPlan,
    only: list[int] | None = None,
    fonts_dir: Path | None = None,
    model: Path | None = None,
    progress=None,
    workers: int | None = None,
) -> RenderOutcome:
    """Clean and typeset every translated page, from the originals.

    Pages are independent, so they run in parallel. Measured at ~13s a page,
    a 40-page chapter is about nine minutes in sequence.
    """
    outcome = RenderOutcome()
    if not paths.translation_file.exists():
        outcome.notes.append("no translation.json; run the translate gate first")
        return outcome

    translation = read_json(paths.translation_file, ChapterTranslation)
    text = {e.region_id: e.target_text for e in translation.entries}
    plan = plan.with_voices(translation)
    fonts = fonts_dir or (paths.root / "assets" / "fonts")

    paths.cleaned.mkdir(parents=True, exist_ok=True)
    paths.translated.mkdir(parents=True, exist_ok=True)

    todo = [
        r for r in state.pages
        if not r.excluded and (not only or r.page in only)
    ]
    if not todo:
        return outcome

    if workers is None:
        from comic.stages import cpu_budget

        workers = min(len(todo), cpu_budget(), 8)

    if workers <= 1 or len(todo) == 1:
        results = [
            render_one(paths, r.page, r.filename, text, plan, fonts, model)
            for r in todo
        ]
    else:
        plan_json = plan.model_dump_json()
        payloads = [
            (
                paths.series, paths.chapter, r.page, r.filename, text,
                plan_json, str(fonts), str(model) if model else None,
            )
            for r in todo
        ]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_job, payloads))

    for result in sorted(results, key=lambda r: r["page"]):
        outcome.notes.extend(result["notes"])
        if not result["rendered"]:
            continue
        outcome.pages.append(result["page"])
        outcome.layouts.update(result["layouts"])
        outcome.checks.extend(result["checks"])
        outcome.flat += result["flat"]
        outcome.inpainted += result["inpainted"]
        if not result["intact"]:
            outcome.artwork_intact = False
        if progress:
            progress(result["page"], len(result["layouts"]))

    return outcome


def open_visual_qa(
    paths: ChapterPaths,
    state: ChapterState,
    outcome: RenderOutcome,
    plan: RenderPlan,
    previous_error: str | None = None,
    unchanged: list[int] | None = None,
) -> Path:
    """Write the rendered pages and the findings for the agent to judge.

    ``unchanged`` names pages a later round did not re-render because no fix
    touched them. Every round renders from the original plus the stored plan,
    so such a page is pixel-identical to the one already approved, and the
    request says so instead of showing it again.
    """
    gate_dir = paths.gate_dir(Gate.VISUAL_QA.value)
    gate_dir.mkdir(parents=True, exist_ok=True)

    # A page shown in an earlier round and not re-rendered in this one must not
    # sit in the gate folder looking like part of this round.
    for stale in gate_dir.glob("page_*_rendered.png"):
        number = stale.stem.split("_")[1]
        if number.isdigit() and int(number) not in outcome.pages:
            stale.unlink()

    # Every check, not one per region: a region can have several findings (an
    # overflow and a spill), and keying them by region kept only the last.
    items: list[VisualQaPageItem] = []
    for page in outcome.pages:
        record = next((p for p in state.pages if p.page == page), None)
        if record is None:
            continue
        rendered = paths.translated / record.filename
        target = gate_dir / f"page_{page:03d}_rendered.png"
        target.write_bytes(rendered.read_bytes())
        analysis = analysis_for(paths, page)
        ids = {r.id for r in analysis.regions} if analysis else set()
        items.append(
            VisualQaPageItem(
                page=page,
                rendered_path=str(target),
                original_path=str(paths.source_pages / record.filename),
                pre_checks=[c for c in outcome.checks if c.region_id in ids],
            )
        )

    shown = {item.page for item in items}
    unchanged = sorted(set(unchanged or []) - shown)
    request = VisualQaRequest(
        series=state.series,
        chapter=state.chapter,
        round=plan.round,
        max_rounds=MAX_QA_ROUNDS,
        pages=items,
        unchanged_pages=unchanged,
        previous_error=previous_error,
        instructions=(
            "Read each rendered page next to its original. Judge whether it "
            "reads correctly: no source lettering left behind, no line "
            "overflowing or covering a face, type large enough to read and "
            "consistent across the page, and the artwork intact. The "
            "off_centre and spill pre-checks are the centre and spill checks "
            "(formerly run by hand with scripts/centre_check.py and "
            "scripts/spill_check.py); they raise false alarms on dot-tone "
            "captions, SFX and shared bubbles, so judge each by eye. Answer "
            "with fixes from the closed vocabulary only -- accept, "
            "font_delta, nudge, force_wrap, expand_mask, shrink_mask, "
            "force_inpaint, retranslate_shorter, restyle. Set passed=true "
            "only when every page is right, and never alongside an "
            "actionable fix. Then run `comic resume`."
        ) + (
            (
                " Only the pages listed were re-rendered this round. The "
                "pages in `unchanged_pages` were approved in an earlier "
                "round and no fix touched them, so they are pixel-identical "
                "to what was approved: do not read them again. passed=true "
                "publishes the whole chapter, those pages included."
            )
            if unchanged
            else ""
        ),
    )
    request_path = paths.gate_request(Gate.VISUAL_QA.value)
    write_json(request_path, request)
    return request_path


def pages_of(paths: ChapterPaths, state: ChapterState, region_ids) -> set[int]:
    """The pages these regions sit on, read from each page's analysis.

    From the analysis rather than from the digits of the id, which only
    happen to encode the page.
    """
    wanted = set(region_ids)
    pages: set[int] = set()
    if not wanted:
        return pages
    for record in state.pages:
        analysis = analysis_for(paths, record.page)
        if analysis is not None and any(r.id in wanted for r in analysis.regions):
            pages.add(record.page)
    return pages


def touched_pages(paths: ChapterPaths, state: ChapterState, fixes: list[QaFix]) -> set[int]:
    """Pages a visual_qa answer changes: every fix but ``accept``.

    A fix to one region can move others on its page -- a page's captions share
    one size ceiling (``render.layout_page``) -- so the unit that is
    re-rendered and shown again is the whole page.
    """
    return pages_of(
        paths, state, [f.region_id for f in fixes if f.action is not FixAction.ACCEPT]
    )


def apply_fixes(plan: RenderPlan, fixes: list[QaFix]) -> tuple[int, list[str]]:
    """Fold the agent's fixes into the render plan.

    Returns (fixes applied, regions needing a shorter translation). Validation
    already rejected anything outside the vocabulary, so this only has to
    decide what each action means mechanically.
    """
    applied = 0
    retranslate: list[str] = []

    for fix in fixes:
        if fix.action is FixAction.ACCEPT:
            continue
        override = plan.edit(fix.region_id)
        value = fix.value or ""

        if fix.action is FixAction.FONT_DELTA:
            override.font_delta += int(value)
        elif fix.action is FixAction.NUDGE:
            dx, dy = (int(part.strip()) for part in value.split(","))
            override.dx += dx
            override.dy += dy
        elif fix.action is FixAction.FORCE_WRAP:
            override.forced_lines = [
                line.strip() for line in value.splitlines() if line.strip()
            ]
        elif fix.action is FixAction.EXPAND_MASK:
            override.mask_grow += int(value)
        elif fix.action is FixAction.SHRINK_MASK:
            override.mask_grow -= int(value)
        elif fix.action is FixAction.FORCE_INPAINT:
            override.force_inpaint = True
        elif fix.action is FixAction.RESTYLE:
            if value in {k.value for k in TextKind}:
                override.style_kind = TextKind(value)
                override.voice = None
            else:
                override.voice = value
        elif fix.action is FixAction.RETRANSLATE_SHORTER:
            override.max_chars = int(value)
            retranslate.append(fix.region_id)
        applied += 1

    plan.round += 1
    return applied, retranslate


def publish(paths: ChapterPaths, state: ChapterState) -> tuple[int, list[str]]:
    """Copy the finished pages into the library.

    Only pages that actually rendered are published, and the library holds
    nothing else -- a partial chapter there would look complete.
    """
    translated: set[str] = set()
    if paths.translation_file.exists():
        chapter = read_json(paths.translation_file, ChapterTranslation)
        translated = {
            e.region_id for e in chapter.entries if e.target_text.strip()  # type: ignore[union-attr]
        }

    problems: list[str] = []
    ready = []
    for record in state.pages:
        # An uploader's insert -- a group credits card, a "read it on <host>"
        # notice -- is not a page of the comic, so it is dropped rather than
        # published untranslated. It stays in `original/` either way.
        if record.excluded:
            continue

        rendered = paths.translated / record.filename
        if rendered.exists():
            ready.append((record, rendered))
            continue

        # A page with nothing to translate -- a cover, a table of contents, a
        # splash with no lettering -- is not a failure. It still belongs in the
        # library, as the original, or the chapter reads with holes in it and
        # the page numbers stop matching the source.
        analysis = analysis_for(paths, record.page)
        expected = (
            {r.id for r in analysis.regions} & translated if analysis else set()
        )
        original = paths.source_pages / record.filename
        if not expected and original.exists():
            ready.append((record, original))
        else:
            problems.append(f"page {record.page}: not rendered")

    if not ready:
        return 0, problems or ["nothing rendered yet"]

    paths.library.mkdir(parents=True, exist_ok=True)
    for record, rendered in ready:
        (paths.library / record.filename).write_bytes(rendered.read_bytes())
    return len(ready), problems
