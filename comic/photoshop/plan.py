"""What one page's Photoshop document should contain -- decided without Photoshop.

This is the Photoshop route's counterpart to ``qa/visual.py: render_one``, and
it deliberately reuses the same decisions rather than making new ones: the
same bubble areas (``fit_areas``), the same fitted size, wrap and position
(``layout_region`` capped by ``source_size_cap``), the same per-series style.
What changes is only the *medium*: instead of pixels drawn by PIL, the plan
describes editable text layers that Photoshop sets in its own type engine.

Reading only, by design. It loads the chapter's analysis, masks, translation
and render plan, and the ``cleaned/`` pages the pipeline already produced; it
writes nothing. The existing pipeline is untouched by anything in this package.

Two conversions matter, both measured against how ``render.draw_layout``
places text:

* **Position.** PIL draws each line with its glyph-box top at
  ``layout.y + i * line_height``; Photoshop point text is anchored on the
  first line's *baseline*. ``baseline = layout.y - top + ascent`` turns one
  into the other, using the same font file at the same size.
* **Size.** Documents are set to 72 ppi, where one point is one pixel, so the
  fitted pixel size carries over unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from comic.image_processing.clean import erase_mask, plain_behind
from comic.image_processing.mask import GLYPH_DILATION, fit_areas, region_glyphs
from comic.paths import ChapterPaths, SeriesPaths, project_root
from comic.photoshop.fonts import font_info
from comic.photoshop.sfx import (
    dropped_regions,
    load_response,
    measure,
    series_ink_rule,
    sfx_layers,
    to_script,
)
from comic.schemas import ChapterTranslation, RegionOverride, RenderPlan, TextKind, TextRegion
from comic.stages import analysis_for
from comic.state.store import read_json
from comic.typesetting.fit import Layout, load_font
from comic.typesetting.render import (
    line_tops,
    Style,
    contrasting,
    font_file,
    layout_page,
    layout_region,
    load_styles,
    source_size_cap,
)
from comic.vision.analyze import load_mask, write_crop
from comic.vision.detector import load_page


#: Heavy display face for replacement sound effects; must cover Vietnamese.
SFX_FONT = "Bangers-Regular.ttf"


@dataclass
class TextLayer:
    """One editable text layer, in Photoshop's terms."""

    region_id: str
    name: str
    lines: list[str]
    #: PostScript name -- the only name Photoshop addresses a face by.
    font: str
    font_file: str
    size: float
    #: Horizontal centre of the block, and the first line's baseline.
    x: float
    baseline: float
    leading: float
    colour: tuple[int, int, int]
    stroke_px: int = 0
    stroke_colour: tuple[int, int, int] = (255, 255, 255)


@dataclass
class PagePlan:
    page: int
    width: int
    height: int
    original: str
    cleaned: str
    #: Glyph mask of the regions Photoshop should Content-Aware Fill, or "".
    fill_mask: str = ""
    psd: str = ""
    png: str = ""
    layers: list[TextLayer] = field(default_factory=list)
    #: Replacement sound effects (see ``photoshop/sfx.py``), as plain dicts.
    sfx: list[dict] = field(default_factory=list)

    def to_script(self) -> dict:
        """Plain data for the JSX side; paths use forward slashes for File()."""
        data = asdict(self)
        for key in ("original", "cleaned", "fill_mask", "psd", "png"):
            data[key] = data[key].replace("\\", "/")
        return data


def baselines_of(layout: Layout, font_path: Path) -> list[float]:
    """Every line's baseline, exactly as ``draw_layout`` places it.

    ``render.line_tops`` puts every line on one even grid (2026-10-15; it
    used to set each line's glyph-box top on the grid, which needed this to
    reproduce per-line drift), so the leading is now uniform too.
    """
    if not layout.lines:
        return [float(layout.y)]
    font = load_font(str(font_path), layout.font_size)
    ascent, _descent = font.getmetrics()
    return [float(top + ascent) for top in line_tops(layout, font)]


def baseline_of(layout: Layout, font_path: Path) -> float:
    """First-line baseline of a fitted block, as ``draw_layout`` places it."""
    return baselines_of(layout, font_path)[0]


def leading_of(layout: Layout, font_path: Path) -> float:
    """One leading that lands the first and last baselines where PIL put them."""
    marks = baselines_of(layout, font_path)
    if len(marks) < 2:
        return float(layout.line_height)
    return (marks[-1] - marks[0]) / (len(marks) - 1)


def text_layer(region: TextRegion, layout: Layout, style: Style, fonts_dir: Path) -> TextLayer:
    path = font_file(fonts_dir, style.font)
    info = font_info(path)
    first = layout.lines[0] if layout.lines else ""
    return TextLayer(
        region_id=region.id,
        name=f"{region.id}  {first[:24]}",
        lines=list(layout.lines),
        font=info.postscript,
        font_file=path.name,
        size=float(layout.font_size),
        x=layout.x + layout.width / 2,
        baseline=baseline_of(layout, path),
        leading=leading_of(layout, path),
        colour=tuple(style.colour),
        stroke_px=int(round(layout.font_size * style.stroke_em)),
        stroke_colour=tuple(style.stroke_colour),
    )


def page_layers(
    cleaned: np.ndarray,
    page_mask: np.ndarray,
    regions: list[TextRegion],
    translations: dict[str, str],
    areas: dict[str, np.ndarray],
    fonts_dir: Path,
    overrides: dict[str, RegionOverride],
    styles,
) -> list[TextLayer]:
    """Text layers for a page -- the same choices ``render_page`` makes.

    Kept in step with ``typesetting/render.py: render_page`` by a test that
    runs both on the same page and compares every layout.
    """
    layers: list[TextLayer] = []
    for region, layout, style in layout_page(
        cleaned, regions, translations, areas, fonts_dir, overrides, styles, page_mask
    ):
        if layout.lines:
            layers.append(text_layer(region, layout, style, fonts_dir))
    return layers


def inpainted_glyphs(
    image: np.ndarray,
    page_mask: np.ndarray,
    regions: list[TextRegion],
    areas: dict[str, np.ndarray],
    overrides: dict[str, RegionOverride],
) -> np.ndarray:
    """Glyph mask of the regions the pipeline *inpainted* rather than flat-filled.

    Mirrors the tier decision in ``clean_page``. Flat fills are exact -- the
    bubble's own colour, outline kept -- so they are left as they are; only
    lettering over artwork is worth handing to Content-Aware Fill.
    """
    out = np.zeros(image.shape[:2], dtype=np.uint8)
    for region in regions:
        if not region.translatable or not region.source_text.strip():
            continue
        area = areas.get(region.id)
        if area is None or not area.any():
            continue
        override = overrides.get(region.id) or RegionOverride()
        glyphs = erase_mask(page_mask, region, area, max(0, GLYPH_DILATION + override.mask_grow))
        if override.force_inpaint or plain_behind(image, glyphs) is None:
            out |= glyphs
    return out


def exact_modal_colour(image: np.ndarray, area: np.ndarray) -> np.ndarray:
    """The most common colour in ``area`` (quantised to vote), as itself."""
    pixels = image[area > 0]
    if pixels.size == 0:
        return np.array([255, 255, 255], dtype=np.uint8)
    if pixels.ndim == 1:
        pixels = pixels[:, None]
    quantised = (pixels // 8).astype(np.int32)
    keys = quantised[:, 0]
    for channel in range(1, quantised.shape[1]):
        keys = keys * 32 + quantised[:, channel]
    winner = np.bincount(keys).argmax()
    return np.median(pixels[keys == winner], axis=0).clip(0, 255).astype(np.uint8)


def refill_flat(
    image: np.ndarray,
    cleaned: np.ndarray,
    page_mask: np.ndarray,
    regions: list[TextRegion],
    areas: dict[str, np.ndarray],
    overrides: dict[str, RegionOverride],
) -> np.ndarray:
    """Correct the pipeline's flat fills to the bubble's exact colour.

    The pipeline fills a flat bubble with its quantised bin's *centre* --
    252 for a white bubble -- which shows as a pale box around a borderless
    caption on a white page (ch.0). The pipeline is left as it is; here only
    pixels that hold exactly that fill, inside a region the pipeline
    flat-filled, change to the colour the bubble really has.
    """
    # Retired: the pipeline no longer fills a bubble with a quantised
    # colour -- it erases only the glyphs, with the exact colour behind them
    # (``clean_page``) -- so there is no grey box left to correct, and
    # matching pixels by that old colour could recolour real artwork.
    return cleaned


def plan_chapter(
    paths: ChapterPaths,
    pages: list[int] | None,
    fonts_dir: Path,
    clean: str,
) -> tuple[list[PagePlan], list[str]]:
    """A plan per translated page, plus notes on anything skipped.

    ``clean`` is ``pipeline`` (use the pipeline's cleaned page as is),
    ``hybrid`` (pipeline flat fills, Content-Aware Fill over artwork) or
    ``content-aware`` (Content-Aware Fill for every region, from the original).
    """
    import cv2

    from comic.state.store import ChapterStore

    state = ChapterStore(paths).load()
    translation = read_json(paths.translation_file, ChapterTranslation)
    text = {e.region_id: e.target_text for e in translation.entries}  # type: ignore[union-attr]
    plan_path = paths.workspace / "render_plan.json"
    plan = (read_json(plan_path, RenderPlan) if plan_path.exists() else RenderPlan(
        series=state.series, chapter=state.chapter
    )).with_voices(translation)
    styles = load_styles(SeriesPaths(paths.series).fonts_file)
    sfx_answers = load_response(paths)
    ink_rule = series_ink_rule(SeriesPaths(paths.series).fonts_file)
    sfx_font = font_info(font_file(fonts_dir, SFX_FONT)).postscript
    # Notes on difficult SFX read like the series' caption boxes.
    caption_style = styles.get(TextKind.CAPTION) or styles[TextKind.DIALOGUE]
    note_file = font_file(fonts_dir, caption_style.sentence_font or caption_style.font)
    note_font = font_info(note_file).postscript
    out_dir = paths.workspace / "photoshop"
    out_dir.mkdir(parents=True, exist_ok=True)

    plans: list[PagePlan] = []
    notes: list[str] = []
    for record in state.pages:
        if record.excluded or (pages and record.page not in pages):
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        has_text = any(text.get(r.id, "").strip() for r in analysis.regions)
        has_sfx = any(rid.startswith(f"p{record.page:03d}_") for rid in sfx_answers)
        if not has_text and not has_sfx:
            continue
        source = paths.source_pages / record.filename
        mask_path = paths.mask_file(record.page)
        cleaned_path = paths.cleaned / record.filename
        if not source.exists() or not mask_path.exists():
            notes.append(f"page {record.page}: missing original or mask")
            continue
        if has_text and clean != "content-aware" and not cleaned_path.exists():
            notes.append(f"page {record.page}: not rendered yet -- run `comic render` first")
            continue
        # A page whose only change is an SFX was never cleaned by the pipeline.
        use_source = clean == "content-aware" or not cleaned_path.exists()

        image = load_page(source)
        page_mask = load_mask(mask_path)
        stem = f"{record.page:03d}"
        prefix = f"p{record.page:03d}_"
        candidates = dropped_regions(paths, record.page, record.filename) if sfx_answers else []

        # Lettering review dropped by mistake: set it as the pipeline would.
        missed = [i for r, i in sorted(sfx_answers.items())
                  if r.startswith(prefix) and i.kind not in ("sfx", "note")]
        # An SFX item may supersede kept lettering too: a caption drawn onto
        # the art in outlined letters (ch.2 p.2 "At least..."), which a
        # bubble-style setting shrinks to nothing.
        replaced = {r for k, i in sfx_answers.items() if k.startswith(prefix) for r in i.replaces}
        regions = [r for r in analysis.regions if r.id not in replaced]
        areas = fit_areas(image, page_mask, analysis.regions)
        base = image if use_source else load_page(cleaned_path)
        cleaned_file = str(source if use_source else cleaned_path)
        if not use_source:
            fixed = refill_flat(image, base, page_mask, analysis.regions, areas, plan.overrides)
            if fixed is not base:
                base = fixed
                cleaned_file = str(out_dir / f"{stem}_base.png")
                write_crop(base, Path(cleaned_file))
        page_text = dict(text)
        # A superseded region's pipeline erase may reach art the items that
        # replace it do not cover -- ch.2 p.36: a bubble merged with a shop
        # sign took "YAKINIKU" with it; p.10: a phone screen re-set line by
        # line lost the lines no item named. Put the original back first;
        # whatever replaces it erases its own letters afterwards.
        if replaced and not use_source:
            base = base.copy()
            for region in analysis.regions:
                if region.id in replaced and region.id in areas:
                    back = erase_mask(page_mask, region, areas[region.id], GLYPH_DILATION) > 0
                    base[back] = image[back]
            cleaned_file = str(out_dir / f"{stem}_base.png")
            write_crop(base, Path(cleaned_file))
        if missed:
            boxes = {c.region_id: c.bbox for c in candidates}
            extra = []
            for item in missed:
                box = item.bbox or boxes.get(item.region_id)
                if box is None:
                    notes.append(f"page {record.page}: {item.region_id}: no such dropped region -- give it a bbox")
                    continue
                extra.append(TextRegion(
                    id=item.region_id, page=record.page, bbox=box, kind=TextKind(item.kind),
                    source_text="(restored in the Photoshop route)",
                ))
                page_text[item.region_id] = item.text
            # Lettering the detector never marked (a scanlator's small
            # handwritten aside, the "AY?" of an "OKAY?" it cut short) has no
            # mask to erase or fit by. With a bbox, find the letters by colour
            # as an SFX item would, and treat them as mask from here on.
            by_id = {i.region_id: i for i in missed}
            for region in extra:
                item = by_id[region.id]
                if item.bbox is not None and item.letters in ("colour", "ink", "area"):
                    found = measure(
                        image, page_mask, item.bbox, by_colour=True,
                        ink_only=item.letters == "ink", area=item.letters == "area",
                    )["glyphs"]
                    page_mask = np.where(found > 0, 255, page_mask).astype(page_mask.dtype)
            # Their areas, contested only by what will actually be set.
            extra_areas = fit_areas(image, page_mask, regions + extra)
            for region in extra:
                areas[region.id] = extra_areas[region.id]
            # Erase them as clean_page would, from the original -- which also
            # takes back anything the pipeline filled for a superseded fragment.
            from comic.image_processing.clean import clean_page, series_clean_mode

            redone, touched, _report = clean_page(
                image, page_mask, extra, areas,
                model=project_root() / "models" / "inpainting_lama.onnx",
                inpaint_all=series_clean_mode(SeriesPaths(paths.series).fonts_file) == "lama",
            )
            base = base.copy()
            base[touched > 0] = redone[touched > 0]
            regions = regions + extra
            cleaned_file = str(out_dir / f"{stem}_base.png")
            write_crop(base, Path(cleaned_file))

        page_plan = PagePlan(
            page=record.page,
            width=int(image.shape[1]),
            height=int(image.shape[0]),
            original=str(source),
            cleaned=cleaned_file,
            psd=str(out_dir / f"{stem}.psd"),
            png=str(out_dir / f"{stem}.png"),
            layers=page_layers(base, page_mask, regions, page_text, areas,
                               fonts_dir, plan.overrides, styles),
        )

        fill = np.zeros(image.shape[:2], dtype=np.uint8)
        if clean == "hybrid":
            fill |= inpainted_glyphs(image, page_mask, analysis.regions, areas, plan.overrides)
        elif clean == "content-aware":
            for region in analysis.regions:
                if region.translatable and text.get(region.id, "").strip():
                    override = plan.overrides.get(region.id) or RegionOverride()
                    fill |= region_glyphs(page_mask, region, max(0, GLYPH_DILATION + override.mask_grow))

        # Sound effects the agent chose to replace: the pipeline never erased
        # them, so they always go to Content-Aware Fill, whatever the mode.
        if sfx_answers:
            # A note may not cover lettering: the page's translated areas.
            lettering = np.zeros(image.shape[:2], dtype=np.uint8)
            for rid, area in areas.items():
                if text.get(rid, "").strip():
                    lettering |= (area > 0).astype(np.uint8)
            layers, erase, erase_local, sfx_notes = sfx_layers(
                image, page_mask, record.page, candidates, sfx_answers, sfx_font,
                ink_rule,
                fonts={i.font: font_info(font_file(fonts_dir, i.font)).postscript
                       for i in sfx_answers.values() if i.font},
                note_font=note_font, note_font_file=str(note_file),
                occupied=lettering,
            )
            page_plan.sfx = to_script(layers)
            fill |= erase
            if erase_local.any():
                # Erase these here with LaMa and hand Photoshop the result as
                # its "Cleaned" layer.
                from comic.image_processing.clean import _inpaint

                local = load_page(Path(page_plan.cleaned))
                model = project_root() / "models" / "inpainting_lama.onnx"
                count, labels, stats, _ = cv2.connectedComponentsWithStats(erase_local)
                for i in range(1, count):
                    # One crop per hole, with context -- as clean_page does.
                    x, y, w, h, _a = stats[i]
                    y0, x0 = max(0, y - 100), max(0, x - 100)
                    y1, x1 = min(local.shape[0], y + h + 100), min(local.shape[1], x + w + 100)
                    hole = (labels[y0:y1, x0:x1] == i).astype(np.uint8)
                    patch = local[y0:y1, x0:x1]
                    repaired = _inpaint(patch, hole, model)
                    patch[hole > 0] = repaired[hole > 0]
                base_file = out_dir / f"{stem}_base.png"
                write_crop(local, base_file)
                page_plan.cleaned = str(base_file)
            notes.extend(f"page {record.page}: {n}" for n in sfx_notes)

        if fill.any():
            mask_file = out_dir / f"{stem}_fill.png"
            _ok, buf = cv2.imencode(".png", (fill > 0).astype(np.uint8) * 255)
            buf.tofile(str(mask_file))
            page_plan.fill_mask = str(mask_file)
        plans.append(page_plan)
    return plans, notes
