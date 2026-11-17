"""`comic clean`: erase the lettering of a whole chapter, LaMa on the Colab T4.

The human-in-the-loop workflow (2026-11-08) needs only the cleaned pages --
the operator typesets in Photoshop -- so this is the cleaning half of
``comic render`` without the preview render, the layout or the QA checks.

Dialogue is cleaned exactly as before (``clean_page``): letters on a plain
colour are filled with that colour on the spot. Letters over artwork used to go
to LaMa one at a time on this PC's CPU. Here they are collected for the whole
chapter -- each with its context window and every nearby letter hidden -- and
sent to the Colab T4 in **one** job: a Drive round trip costs ~45 s, so one job
per spot would be slower than the CPU. Without a live worker the same spots
are inpainted locally, as before.

SFX, logos and watermarks are not erased here: SFX go to Photoshop's
Content-Aware Fill in ``comic ps build``; regions kept as drawn stay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from comic.paths import ChapterPaths, SeriesPaths, project_root


@dataclass
class Spot:
    page: int
    region_id: str
    top: int
    left: int
    patch: np.ndarray
    hidden: np.ndarray
    hole: np.ndarray


@dataclass
class CleanOutcome:
    pages: int = 0
    flat: int = 0
    inpainted: int = 0
    where: str = ""
    notes: list[str] = field(default_factory=list)


def _worker_alive() -> bool:
    try:
        from comic.colab.jobs import worker_status

        status = worker_status()
        return bool(status and status["alive"])
    except Exception:  # noqa: BLE001 - no Drive, no worker: clean locally
        return False


def clean_chapter(paths: ChapterPaths, pages: list[int] | None = None,
                  use_colab: bool = True, progress=None) -> CleanOutcome:
    from comic.image_processing.clean import (
        _inpaint, _is_grey, clean_page, pad_edges, page_is_grey, series_clean_mode, unpad)
    from comic.image_processing.mask import fit_areas, refine_page_mask, with_erase_boxes
    from comic.schemas import RenderPlan
    from comic.stages import analysis_for
    from comic.state.store import ChapterStore, read_json
    from comic.vision.analyze import load_mask, write_crop
    from comic.vision.detector import load_page

    import cv2

    say = progress or (lambda _m: None)
    state = ChapterStore(paths).load()
    plan_path = paths.workspace / "render_plan.json"
    overrides = read_json(plan_path, RenderPlan).overrides if plan_path.exists() else {}
    inpaint_all = series_clean_mode(SeriesPaths(paths.series).fonts_file) == "lama"
    model = project_root() / "models" / "inpainting_lama.onnx"
    paths.cleaned.mkdir(parents=True, exist_ok=True)

    out = CleanOutcome()
    spots: list[Spot] = []
    pages_done: dict[int, tuple[Path, np.ndarray]] = {}
    grey_pages: set[int] = set()
    for record in state.pages:
        # A page held for its content with --cleanup is cleaned like any other
        # page (lettering, marks): the operator letters it by hand. Only the
        # gates skip it -- the agent never transcribes or translates it (the
        # operator, 2026-11-13: JJ ch.3).
        worked = record.excluded and record.held and record.hold_cleanup
        if (record.excluded and not worked) or (pages and record.page not in pages):
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        if not any(r.cleanable for r in analysis.regions):
            continue
        source = paths.source_pages / record.filename
        mask_path = paths.mask_file(record.page)
        if not source.exists() or not mask_path.exists():
            out.notes.append(f"page {record.page}: missing original or mask")
            continue
        image = load_page(source)
        page_mask = with_erase_boxes(
            refine_page_mask(image, load_mask(mask_path), analysis.regions), analysis.regions)
        areas = fit_areas(image, page_mask, analysis.regions)
        if page_is_grey(image, analysis.regions):
            grey_pages.add(record.page)

        def collect(rid, top, left, patch, hidden, hole, _page=record.page):
            spots.append(Spot(_page, rid, top, left, patch, hidden, hole))

        cleaned, _touched, report = clean_page(
            image, page_mask, analysis.regions, areas, model=model,
            overrides=overrides, inpaint_all=inpaint_all, defer=collect)
        out.flat += report.count("flat")
        out.notes.extend(f"page {record.page}: {n}" for n in report.notes)
        target = paths.cleaned / record.filename
        pages_done[record.page] = (target, cleaned)
        out.pages += 1
        say(f"page {record.page:03d}: {report.count('flat')} flat, {report.count('inpaint')} to LaMa")

    # LaMa: one Colab job for the chapter, or locally.
    repaired: dict[int, np.ndarray] = {}
    if spots and use_colab and _worker_alive():
        from comic.colab.tasks import inpaint

        say(f"{len(spots)} spot(s) over artwork -> Colab T4, one job")
        padded = [pad_edges(s.patch, s.hidden) for s in spots]
        result = inpaint([(f"s{i:04d}", pp, hh) for i, (pp, hh, _) in enumerate(padded)],
                         progress=say)
        repaired = {i: unpad(result[f"s{i:04d}"], padded[i][2]) for i in range(len(spots))}
        out.where = "Colab T4"
    elif spots:
        say(f"{len(spots)} spot(s) over artwork -> LaMa on this PC (no live Colab worker)")
        repaired = {i: _inpaint(s.patch, s.hidden, model) for i, s in enumerate(spots)}
        out.where = "local CPU"

    for i, s in enumerate(spots):
        fixed = repaired[i]
        # LaMa paints in colour; a grey page gets grey back (Super Lovers ch.1).
        if fixed.ndim == 3 and (s.page in grey_pages or _is_grey(s.patch, s.hidden)):
            fixed = cv2.cvtColor(cv2.cvtColor(fixed, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
        _target, page_img = pages_done[s.page]
        h, w = s.hole.shape
        window = page_img[s.top:s.top + h, s.left:s.left + w]
        spot = s.hole > 0
        window[spot] = fixed[spot]
        out.inpainted += 1

    for target, page_img in pages_done.values():
        write_crop(page_img, target)
    return out
