"""Sound effects for the Photoshop route.

The pipeline never translates SFX: they are dropped at ``ocr_review`` and stay
in the art. The Photoshop route can replace some of them, because Photoshop
can do the two things that needs -- erase lettering drawn over artwork
(Content-Aware Fill) and set display type that matches the original's colour,
outline and angle.

**Which ones is the agent's call, and the policy is narrow.** Short English
SFX a Vietnamese reader understands at a glance (AHEM, HUH, HAHA) stay as they
are; long or hard words (TREMBLE, GLARES) are translated; Chinese and Korean
SFX go to English first, and to Vietnamese only if that English is hard too.
So this is a small gate: ``comic ps sfx`` writes a crop of every dropped
region, the agent writes ``sfx_response.json`` naming only the ones to
replace, and ``comic ps build`` does the rest.

Dropped regions no longer exist in the analysis -- ``apply_ocr_review``
deletes them -- but the detector mask on disk still holds them, and regions
are derived from the mask deterministically. Re-deriving reproduces the
original ids and boxes exactly (verified: 174 of 174 kept regions over three
chapters), so a dropped id from the review means the same lettering here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from typing import Literal

from pydantic import BaseModel, model_validator, Field

from comic.paths import ChapterPaths
from comic.schemas import BBox, region_id
from comic.stages import analysis_for
from comic.state.store import ChapterStore, write_json

#: How far past the glyph mask to erase: SFX carry a thick outline the
#: detector mask does not cover.
SFX_DILATION = 6
#: Below this contrast between the letters and the ring around them, the
#: original has no outline worth reproducing.
OUTLINE_CONTRAST = 80
#: How far past the letters to erase, to take a light outline with them.
SFX_HALO = 6
#: An SFX set at a steeper angle than this is treated as vertical and skipped.
MAX_ANGLE = 60
#: ``letters: whole``: how far past the region a touched blob may reach, and
#: how close to the ink colour (RGB distance) its pixels must be. A blob that
#: reaches further is artwork the letter merely touches -- a black panel.
WHOLE_REACH = 90
WHOLE_TOLERANCE = 70


#: A ``note`` item's look: solid dark letters, a white halo so it reads over
#: art, as a share of the size.
NOTE_INK = (20, 20, 20)
NOTE_HALO = (255, 255, 255)
NOTE_HALO_PX = 2.0


from comic.photoshop.notes import find_spot, placement_masks  # noqa: E402


class SfxItem(BaseModel):
    region_id: str = Field(pattern=r"^p\d{3}_r\d{2}$")
    #: What to set in its place -- Vietnamese, or English for a CJK original.
    #: The replacement. Empty only with ``erase_only``.
    text: str = ""
    #: Erase the lettering and set nothing in its place -- another group's
    #: watermark or credit tag on a story page (the operator: remove it
    #: entirely; replace with "Raichi"/"Rồng nhỏ" only when asked).
    erase_only: bool = False
    #: Already erased by LaMa at cleaning (an SFX boxed with Studio's key L,
    #: 2026-11-14): set the text over the cleaned page, erase nothing more.
    pre_erased: bool = False
    #: Set ``text`` as written instead of in capitals. Vietnamese groups set
    #: loud impacts in caps (RẦM, ĐÙNG) and soft or continuous sounds in
    #: lower or mixed case (Thình thịch, Hộc, bước) -- docs/lettering.md
    #: section 9. Pair it with a ``font`` that has real lowercase.
    keep_case: bool = False
    #: The word's exact extent, when the detector's region is wrong for it:
    #: it may cover only part of the word (ch.60 FLINCH: only the H) or take
    #: in neighbouring marks (ch.46 DRAG: the "???" below it). Inside the box
    #: the letters are found by colour, not from the detector mask. An id
    #: with a box need not exist as a region at all.
    bbox: BBox | None = None
    #: How to find the letters inside ``bbox``: ``colour`` (everything that
    #: is not background -- for a word the mask missed) or ``mask`` (the
    #: detector mask clipped to the box -- for a word the mask merged with
    #: its neighbours, where the box would otherwise catch artwork).
    letters: Literal["colour", "mask", "whole", "ink", "area"] = "colour"
    #: ``area``: everything inside the ``poly``/``bbox`` is erased -- for a
    #: gradient word on a plain background (ch.50 HUG), which no colour rule
    #: separates from the page. Draw the outline tight to the lettering.
    #: ``ink``: only pixels of the ink's own colour inside the box. For plain
    #: text on a flat pale field -- a phone message (ch.25) -- where
    #: ``colour`` takes the field itself for the letters' outline and erases
    #: the whole bubble.
    #: ``whole``: like ``mask``, plus every ink-coloured blob inside the box
    #: (or the parts' boxes), taken whole. The mask often covers only part of a big
    #: letter, and the rest is lost when it runs off the search zone
    #: (ch.5 BURST: the B and the T's stem). Applies with or without a box.
    #: Who erases the original: Photoshop's Content-Aware Fill, or the
    #: project's local LaMa model. Content-Aware Fill samples the whole page
    #: and can copy a nearby line into the hole -- measured on ch.46 DRAG, a
    #: dark rule and a "?" stroke; LaMa only extends what surrounds the hole.
    fill: Literal["content-aware", "lama"] = "content-aware"
    #: Set the replacement at this angle (degrees, counter-clockwise
    #: negative as measured) instead of the lettering's own. Letters laid
    #: out as a staircase -- each upright, the word stepping down (ch.0
    #: "T A P") -- measure as a steep diagonal and come out near-sideways.
    angle: float | None = Field(default=None, ge=-90, le=90)
    #: How far past the detector mask the letters may reach (mask mode). The
    #: mask often covers only part of a stroke -- ch.0 TAP: the T's stem and
    #: the end of its bar -- and the rest is lost beyond the default.
    grow: int = Field(default=SFX_DILATION, ge=0, le=40)
    #: How far past the letters to erase. Motion-blurred lettering (ch.8
    #: SLAMM) fades out ~15px beyond its strokes, which the default leaves
    #: behind as a grey ghost.
    halo: int = Field(default=SFX_HALO, ge=0, le=40)
    #: Other dropped regions that are pieces of this same word -- the
    #: detector often splits a big SFX letter by letter (ch.5 BURST: six
    #: regions). Their masks are erased and measured together; nothing
    #: between them is touched, so a bubble sitting between the pieces
    #: (ch.5 G / RAB around a "HUH?") survives where a box around the
    #: whole word would not.
    parts: list[str] = Field(default_factory=list)
    #: Which colour is the letters, for a word that breaks the series' rule
    #: in ``fonts.yaml`` (ch.9 THWACK: white letters, black outline).
    ink: Literal["auto", "dark", "light"] | None = None
    #: Size relative to the original's footprint. Stacked diacritics stand
    #: taller than the capitals they replace, so words stacked close
    #: together (ch.2 "STOMP / STOMP") overlap at full size.
    scale: float = Field(default=1.0, gt=0.2, le=2.0)
    #: Letter colour (RGB) where one median cannot stand for the original --
    #: a gradient fill (ch.2 "WTH": maroon to red) measures as its darkest end.
    colour: tuple[int, int, int] | None = None
    #: Move the replacement (px, right and down) off something the original
    #: wove its letters around -- ch.5 GRAB runs through a "HUH?" bubble,
    #: and a solid word over the same path covers it.
    offset: tuple[float, float] = (0.0, 0.0)
    #: Font file in ``assets/fonts`` for this item instead of the SFX face --
    #: for a dialogue line lettered straight onto the art like an SFX (ch.17
    #: "TRY THIS ONE TOO.", white with a dark outline), which a bubble fill
    #: would smear over the faces around it.
    font: str | None = None
    #: Force the outline on or off; by default it follows the original.
    #: Typed text (a phone screen) has none, whatever the series' rule.
    outline: bool | None = None
    #: Size the replacement by one letter's height (``letter``, default) or
    #: by the word's footprint (``box``). A gradient fill (ch.50 HUG, dark
    #: red to pink) breaks the letters into ink-coloured fragments, and the
    #: letter measure then set ÔM at a third of the original.
    size_by: Literal["letter", "box"] = "letter"
    #: The word's outline as a polygon, for lettering set on a slant next to
    #: other lettering -- phone messages stacked on a tilted screen (ch.32),
    #: where any upright box takes in part of the next message. Used like
    #: ``bbox`` (which it replaces), with ``letters: colour``.
    poly: list[tuple[int, int]] | None = Field(default=None, min_length=3)
    #: Not every dropped region is an SFX. Review also dropped some real
    #: lettering by mistake -- a bubble ("WHY, YOU...!", ch.4), a caption box,
    #: a line the detector split into single words of which only some were
    #: kept (ch.0: "WHAT IN THE WORLD IS GOING ON?!" read "Cái IN gì-
    #: WORLD..."). Any other kind sets ``text`` the way the pipeline sets that
    #: kind -- same bubble area, cleaning, style and fit as ``render_page`` --
    #: instead of as a sound effect.
    #: ``note``: a "difficult" SFX -- hard to erase, translate or even read
    #: -- is left exactly as drawn, and ``text`` is set as a small
    #: translator's note in empty space near it: a gutter, the page margin,
    #: a blank panel corner, never on the art beside it (``notes.find_spot``
    #: picks the spot unless ``bbox`` names one). Dark letters, thin white
    #: halo, as written. The operator, 2026-10-15.
    kind: Literal["sfx", "note", "dialogue", "narration", "thought", "shout", "caption"] = "sfx"
    #: Kept regions this item supersedes: their pipeline text is not set.
    #: For a line the detector split, give the whole line's ``bbox`` here
    #: and list the fragments that were kept.
    replaces: list[str] = Field(default_factory=list)


    @model_validator(mode="after")
    def _needs_text(self) -> "SfxItem":
        if not self.erase_only and not self.text.strip():
            raise ValueError(f"{self.region_id}: text is required unless erase_only")
        if self.kind == "note" and self.erase_only:
            raise ValueError(f"{self.region_id}: a note sets text; it cannot be erase_only")
        return self

class SfxRequestRegion(BaseModel):
    region_id: str
    page: int
    bbox: BBox


class SfxRequest(BaseModel):
    gate: str = "sfx"
    policy: str
    crops: str
    regions: list[SfxRequestRegion]


class SfxResponse(BaseModel):
    gate: str = "sfx"
    items: list[SfxItem] = Field(default_factory=list)


@dataclass
class SfxCandidate:
    region_id: str
    page: int
    bbox: BBox


@dataclass
class SfxLayer:
    region_id: str
    text: str
    font: str
    colour: tuple[int, int, int]
    #: (0, 0, 0) with stroke_px 0 means the original had no outline.
    stroke_colour: tuple[int, int, int]
    stroke_ratio: float
    cx: float
    cy: float
    width: float
    height: float
    angle: float
    #: ``text`` without its diacritics. The height fit is measured on this:
    #: the original's capitals carry no accents, so fitting the accented
    #: word's full height to them let a short word (ch.18 CHÍP for CHIRP)
    #: grow until its accents filled the slack -- far larger than the
    #: original. Measuring bare letters matches the letters themselves.
    plain: str = ""
    #: Height of one original letter (median over its letters), or 0 when
    #: it could not be measured. The footprint's height is the whole word's,
    #: which for a staggered or bouncing word (ch.18 CHIRP: letters stepping
    #: up and down) is twice a letter's -- and fitting to it made the
    #: replacement twice the size of the original lettering.
    letter_height: float = 0.0
    #: Set one letter per line (a vertical SFX), stepping sideways by
    #: ``slant`` line heights per line -- done with tabs in Photoshop.
    vertical: bool = False
    slant: float = 0.0
    #: The original's other effects (``sfx_style.effects``), in page pixels.
    stroke2_colour: tuple[int, int, int] = (0, 0, 0)
    stroke2_px: int = 0
    glow_colour: tuple[int, int, int] = (0, 0, 0)
    glow_px: int = 0
    shadow_colour: tuple[int, int, int] = (0, 0, 0)
    shadow_dx: int = 0
    shadow_dy: int = 0
    #: A gradient fill, top colour to bottom colour (both set, or neither).
    gradient_top: tuple[int, int, int] | None = None
    gradient_bottom: tuple[int, int, int] | None = None


def _style_fields(image: np.ndarray, m: dict) -> dict:
    """The original's second stroke, glow and drop shadow, as SfxLayer fields."""
    from comic.photoshop.sfx_style import effects

    fx = effects(image, m["glyphs"] > 0, m["ink"], m["edge"], m["outlined"])
    out: dict = {}
    if fx.stroke2_px:
        out.update(stroke2_colour=fx.stroke2_colour, stroke2_px=fx.stroke2_px)
    if fx.glow_px:
        out.update(glow_colour=fx.glow_colour, glow_px=fx.glow_px)
    if fx.shadow_colour is not None:
        out.update(shadow_colour=fx.shadow_colour, shadow_dx=fx.shadow_dx, shadow_dy=fx.shadow_dy)
    if fx.gradient_top is not None:
        out.update(gradient_top=fx.gradient_top, gradient_bottom=fx.gradient_bottom)
    if fx.textured:
        out["_textured"] = True
    return out


def _footprint(mask: np.ndarray) -> tuple[float, float, float, float, float]:
    """(cx, cy, long side, short side, angle) of a mask's minimum-area rectangle."""
    import cv2

    ys, xs = np.nonzero(mask)
    (cx, cy), (w, h), angle = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
    if w < h:
        w, h = h, w
        angle -= 90
    while angle > 90:
        angle -= 180
    while angle < -90:
        angle += 180
    return float(cx), float(cy), float(w), float(h), float(angle)


def plain_letters(text: str) -> str:
    """``text`` with Vietnamese diacritics removed (Đ becomes D)."""
    import unicodedata

    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    )
    return stripped.replace("Đ", "D").replace("đ", "d")


def request_path(paths: ChapterPaths) -> Path:
    return paths.workspace / "photoshop" / "sfx_request.json"


def response_path(paths: ChapterPaths) -> Path:
    return paths.workspace / "photoshop" / "sfx_response.json"


def dropped_regions(paths: ChapterPaths, page: int, filename: str) -> list[SfxCandidate]:
    """Regions the detector found on this page that review dropped, plus the
    ones kept but classified ``sfx`` -- the pipeline leaves those as drawn too,
    and listing only drops meant they were never offered and an item naming
    one was refused."""
    from comic.vision.analyze import load_mask
    from comic.vision.detector import load_page
    from comic.vision.regions import extract_regions, order_regions

    analysis = analysis_for(paths, page)
    mask_path = paths.mask_file(page)
    source = paths.source_pages / filename
    if analysis is None or not mask_path.exists() or not source.exists():
        return []
    kept = {r.id for r in analysis.regions if r.erasable}
    right_to_left = analysis.reading_direction.value == "rtl"
    image, mask = load_page(source), load_mask(mask_path)
    if image.shape[:2] != mask.shape[:2]:
        # The page was trimmed after detection; the mask no longer describes it.
        return []
    candidates = order_regions(
        extract_regions(image, mask),
        right_to_left=right_to_left,
    )
    # A kept region answers to its own id and box. Re-deriving ids from the
    # mask reproduced the review's numbering in the old flow, but not after
    # Studio edits or a re-detect: Itsuka ch.5 p.33's re-derived "r12" was
    # another blob, and its HM~ was set 300 px away (2026-11-10).
    own = {r.id: r for r in analysis.regions}
    found = [
        SfxCandidate(region_id(page, i), page, c.bbox)
        for i, c in enumerate(candidates, start=1)
        if region_id(page, i) not in own
    ]
    found += [SfxCandidate(r.id, page, r.bbox) for r in analysis.regions if r.id not in kept]
    return found


def open_request(paths: ChapterPaths, pages: list[int] | None) -> tuple[Path, int]:
    """Write a crop of every dropped region, and the request listing them."""
    from comic.vision.analyze import crop_region, write_crop
    from comic.vision.detector import load_page

    state = ChapterStore(paths).load()
    out = paths.workspace / "photoshop" / "sfx"
    out.mkdir(parents=True, exist_ok=True)
    listed = []
    for record in state.pages:
        if record.excluded or (pages and record.page not in pages):
            continue
        found = dropped_regions(paths, record.page, record.filename)
        if not found:
            continue
        image = load_page(paths.source_pages / record.filename)
        for cand in found:
            write_crop(crop_region(image, SimpleNamespace(bbox=cand.bbox), pad=24, scale=2), out / f"{cand.region_id}.png")
            listed.append(SfxRequestRegion(region_id=cand.region_id, page=cand.page,
                                           bbox=cand.bbox))
    request = SfxRequest(
        policy=(
            "Name only the SFX to replace. Leave out short English SFX a Vietnamese "
            "reader understands (AHEM, HUH, HAHA); translate long or hard English "
            "words to Vietnamese; render Chinese/Korean SFX in English first, and "
            "in Vietnamese only if that English is hard. Leave out front matter, "
            "credits, logos and anything that is not an SFX."
        ),
        crops=str(out),
        regions=listed,
    )
    write_json(request_path(paths), request)
    return request_path(paths), len(listed)


def published_path(paths: ChapterPaths) -> Path:
    """Items for an SFX pass over an already-published page (`--clean published`)."""
    return paths.workspace / "photoshop" / "sfx_published.json"


def load_response(paths: ChapterPaths, published: bool = False) -> dict[str, SfxItem]:
    """SFX to replace: the chapter's translated SFX regions, plus hand-written items.

    ``published``: only the items of ``sfx_published.json``. The published page
    already carries everything ``sfx_response.json`` and the translation set;
    applying those again would erase and re-set them on top of themselves.

    Since the human-in-the-loop workflow (2026-11-08) SFX are ordinary regions
    with a translation, so each translated SFX region becomes an item by itself
    (its own box, letters found by colour, Content-Aware Fill). An SFX left
    untranslated stays as drawn. A hand-written ``sfx_response.json`` item for
    the same region wins, for the tricky cases (``parts``, ``poly``, ``fill:
    lama``, notes ...).
    """
    items = {} if published else translated_sfx(paths)
    # An SFX LaMa already erased at cleaning stays erased whatever replaces its item.
    pre = {rid for rid, item in items.items() if item.pre_erased}
    path = published_path(paths) if published else response_path(paths)
    if path.exists():
        answer = SfxResponse.model_validate_json(path.read_text(encoding="utf-8"))
        items.update({item.region_id: item for item in answer.items
                      if item.text.strip() or item.erase_only})
    for rid in pre & items.keys():
        items[rid] = items[rid].model_copy(update={"pre_erased": True})
    return items


def translated_sfx(paths: ChapterPaths) -> dict[str, SfxItem]:
    """An item per SFX region that has a Vietnamese translation."""
    from comic.schemas import ChapterTranslation, TextKind
    from comic.stages import analysis_for
    from comic.state.store import ChapterStore, StateError, read_json

    if not paths.translation_file.exists():
        return {}
    try:
        state = ChapterStore(paths).load()
    except StateError:
        return {}
    text = {e.region_id: e.target_text for e in
            read_json(paths.translation_file, ChapterTranslation).entries}  # type: ignore[union-attr]
    items: dict[str, SfxItem] = {}
    for record in state.pages:
        if record.excluded:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for r in analysis.regions:
            vi = text.get(r.id, "").strip()
            if r.kind is TextKind.SFX and vi:
                items[r.id] = SfxItem(region_id=r.id, text=vi, bbox=r.bbox,
                                     angle=max(-90.0, min(90.0, r.angle)) if r.angle else None,
                                     pre_erased=r.lama_sfx)
    return items


def _median_colour(image: np.ndarray, where: np.ndarray) -> tuple[int, int, int]:
    pixels = image[where > 0]
    if pixels.size == 0:
        return (0, 0, 0)
    # Pages come from ``load_page`` as RGB.
    r, g, b = np.median(pixels, axis=0)
    return (int(r), int(g), int(b))


def _letter_colours(
    image: np.ndarray, own: np.ndarray, grow: int = SFX_DILATION, ink_rule: str = "auto"
) -> tuple[np.ndarray, tuple[int, int, int], tuple[int, int, int], bool]:
    """Find an SFX's ink and outline by *structure*, not by colour statistics.

    Every colour-statistics rule tried here broke on real pages (ch.46/54/60):
    a median over the fat mask turned black letters grey; "the cluster away
    from the ring" lost white letters when a light hand crossed the ring
    (SQUEEZE); black letters on a black panel merged into the background
    (THROB); a mid-grey anti-alias cluster passed for the outline (CLICK) or
    hid which colour was inside (LEAN).

    What holds for all of them: lettering is the **darkest and lightest**
    colours present, one inside the strokes and one around them.

    1. Cluster the colours near the mask; take the darkest and the lightest.
    2. Of each, discard what connects to the edge of the search zone -- that
       is background, whatever its colour (the black panel behind THROB).
    3. What is left of the two is the lettering. The ink is the one lying
       deeper inside it (distance transform); the other is the outline.

    An outline is always reported when the two differ: a white outline on a
    white page draws nothing, and the series letters every SFX outlined.

    Returns (letter pixels, ink, outline, whether there is an outline).
    """
    import cv2
    from scipy.ndimage import label

    from comic.image_processing.mask import dilate

    near = dilate(own, grow) > 0 if grow else own > 0
    zone = dilate(own, grow + 8) > 0
    pixels = image[zone].astype(np.float32)
    if len(pixels) < 40:
        return own.copy(), _median_colour(image, own), (255, 255, 255), False
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _c, labels, centres = cv2.kmeans(pixels, 4, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    label_map = np.full(own.shape, -1, dtype=np.int32)
    label_map[zone] = labels.ravel()
    luma = centres @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    dark, light = int(np.argmin(luma)), int(np.argmax(luma))
    border = zone & ~(cv2.erode(zone.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)

    def inner(cluster: int) -> np.ndarray:
        """This colour's pixels near the mask, minus any touching the zone edge."""
        mask = label_map == cluster
        comps, _n = label(mask)
        outer = np.unique(comps[border & mask])
        return mask & near & ~np.isin(comps, outer[outer > 0])

    def colour_of(where: np.ndarray) -> tuple[int, int, int]:
        return _median_colour(image, where)

    in_dark, in_light = inner(dark), inner(light)
    shape = (in_dark | in_light).astype(np.uint8)
    if not shape.any() or luma[light] - luma[dark] < OUTLINE_CONTRAST / 3:
        # One colour, no contrast to read roles from.
        return own.copy(), colour_of(own), (255, 255, 255), False

    # A series that letters every SFX the same way says so in its profile
    # (``fonts.yaml: sfx: {ink: dark}``) -- measured on "Mi Imitador": 89 of
    # 89 SFX are black with a white outline, and no per-word rule got all of
    # them, because on a dark panel the white outline is drawn *thicker*
    # than the black strokes (THROB, SCRATCH, CLASP).
    #
    # Otherwise the ink is the *thicker* of the two inside the detector
    # mask; an outline is usually a thin band. Measured on 15 SFX over ch.46/54/60: right on
    # all 15, where depth-in-the-lettering got 3 wrong (a white outline that
    # joins a white page is not "inside" anything) and a vote over the mask
    # core got 2 wrong. Ties go to the mean, then to dark.
    # In box mode ``own`` is the whole box, background and all; measure
    # inside the lettering found above instead.
    within = own > 0 if grow else shape > 0

    def thickness(cluster: int) -> tuple[float, float]:
        mask = ((label_map == cluster) & within).astype(np.uint8)
        if not mask.any():
            return (0.0, 0.0)
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)[mask > 0]
        return (float(np.percentile(dist, 90)), float(dist.mean()))

    if ink_rule == "dark":
        ink, edge = dark, light
    elif ink_rule == "light":
        ink, edge = light, dark
    else:
        ink, edge = (dark, light) if thickness(dark) >= thickness(light) else (light, dark)
    in_ink = in_dark if ink == dark else in_light
    edge_px = (label_map == edge) & near
    ink_rgb = colour_of(in_ink) if in_ink.any() else tuple(int(v) for v in centres[ink])
    edge_rgb = colour_of(edge_px) if edge_px.any() else tuple(int(v) for v in centres[edge])
    return shape, ink_rgb, edge_rgb, True


def _bbox_of(page_mask: np.ndarray, own: np.ndarray) -> BBox | None:
    ys, xs = np.nonzero(own)
    if not len(xs):
        return None
    return BBox(x1=int(xs.min()), y1=int(ys.min()), x2=int(xs.max()) + 1, y2=int(ys.max()) + 1)


def _touched_blobs(image: np.ndarray, own: np.ndarray, ink: tuple[int, int, int]) -> np.ndarray:
    """Ink-coloured blobs the detector mask touches, whole, if they stay near it."""
    import cv2

    out = np.zeros(own.shape[:2], dtype=np.uint8)
    found = _bbox_of(own, own)
    if found is None:
        return out
    h, w = own.shape[:2]
    # A big word's letters reach further past the fragments the mask found.
    reach = max(WHOLE_REACH, found.width // 2, found.height // 2)
    x1, y1 = max(0, found.x1 - reach), max(0, found.y1 - reach)
    x2, y2 = min(w, found.x2 + reach), min(h, found.y2 + reach)
    window = image[y1:y2, x1:x2].astype(np.int32)
    near_ink = (np.abs(window - np.array(ink, dtype=np.int32)).sum(axis=-1) < WHOLE_TOLERANCE).astype(np.uint8)
    count, labels, stats, _c = cv2.connectedComponentsWithStats(near_ink, connectivity=8)
    touched = np.unique(labels[(own[y1:y2, x1:x2] > 0) & (near_ink > 0)])
    for index in touched:
        if index == 0:
            continue
        bx, by, bw, bh, _a = stats[index]
        # Running into the window's edge means it is not a letter.
        clipped = (bx == 0 and x1 > 0) or (by == 0 and y1 > 0)             or (bx + bw == x2 - x1 and x2 < w) or (by + bh == y2 - y1 and y2 < h)
        if not clipped:
            out[y1:y2, x1:x2] |= (labels == index).astype(np.uint8)
    return out


#: Pixels this close (summed RGB distance) to the ink colour are ink.
INK_TOLERANCE = 90
#: A piece found by colour stays lettering when the detector marked this share
#: of it (and at least 20 px). Measured on 10 SFX pages of NH ch.8: 0.25 lost
#: whole outlined kana (ch.8 p21 コ, く); 0.08 kept every letter and still
#: dropped the hair, hand and shelf dots a box-wide erase took.
STROKE_SHARE = 0.08
#: A straight horizontal/vertical run this long (px) is a line, not a stroke.
LINE_RUN = 70
#: ... and at least this share of the lettering's own width/height.
LINE_SHARE = 0.8


def keep_strokes(letters: np.ndarray, detected: np.ndarray) -> np.ndarray:
    """The pieces of ``letters`` the detector also marked, each kept whole.

    Colour finds everything in a box that is not background -- lettering and
    any artwork drawn inside the rectangle alike. The detector marks only the
    lettering, but as a rough blob. Together: a connected piece (a stroke with
    its outline) is lettering when the blob covers enough of it, and then all
    of it is erased, outline included; a piece it never touched is art and
    stays. Without a usable detector mark the colour result stands.
    """
    import cv2

    from comic.image_processing.mask import dilate

    if detected.sum() < 30:
        return letters
    # Panel borders and speed lines are long straight runs no letter has; cut
    # them out first, or a letter touching one joins it into a single piece
    # (ch.8 p31: the top kana of きゃっ fused with the panel border).
    # "Long" is relative to the lettering: a big kana's own stroke can run
    # 100 px (ch.8 p21 コ); a border runs right across the box.
    solid = (letters > 0).astype(np.uint8)
    ys, xs = np.nonzero(solid)
    if not len(xs):
        return letters
    across = max(LINE_RUN, int(LINE_SHARE * (xs.max() - xs.min() + 1)))
    down = max(LINE_RUN, int(LINE_SHARE * (ys.max() - ys.min() + 1)))
    runs = np.zeros_like(solid)
    for kernel in (np.ones((1, across), np.uint8), np.ones((down, 1), np.uint8)):
        runs |= cv2.morphologyEx(solid, cv2.MORPH_OPEN, kernel)
    letters = solid & (1 - runs)
    count, labels, stats, _c = cv2.connectedComponentsWithStats(letters, connectivity=8)
    if count <= 1:
        return letters
    near = dilate(detected, 3) > 0
    hits = np.bincount(labels[near].ravel(), minlength=count)
    keep = np.zeros(count, dtype=bool)
    for index in range(1, count):
        keep[index] = hits[index] >= max(20, STROKE_SHARE * stats[index][4])
    # A letter drawn touching art (ch.8 p31: a kana across the panel border)
    # joins it into one huge piece the blob barely covers: keep the part of
    # it the detector marked rather than losing the letter.
    partial = (letters > 0) & ~keep[labels] & (dilate(detected, 2) > 0)
    # A letter's fill in a third colour (ch.8 p6 THUD: grey inside a black
    # outline) is neither the darkest nor the lightest colour; it is what the
    # outline encloses.
    from scipy.ndimage import binary_fill_holes

    found = (keep[labels] | partial).astype(np.uint8)
    # Close the small gaps of a rough brush outline before filling it.
    closed = cv2.morphologyEx(found, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return (binary_fill_holes(closed) | found).astype(np.uint8)


def letter_height(
    image: np.ndarray, where: np.ndarray, ink: tuple[int, int, int], angle: float
) -> float:
    """Median height of the ink blobs in ``where``, measured across ``angle``.

    Each blob is usually one letter; the median ignores the odd merged pair,
    a stray mark or a broken stroke.
    """
    import cv2

    near = (np.abs(image.astype(np.int32) - np.array(ink, dtype=np.int32)).sum(axis=-1)
            < INK_TOLERANCE) & (where > 0)
    count, labels, stats, _c = cv2.connectedComponentsWithStats(near.astype(np.uint8), connectivity=8)
    t = np.deg2rad(angle)
    across = np.array([-np.sin(t), np.cos(t)], dtype=np.float32)
    heights = []
    for index in range(1, count):
        if stats[index][4] < 15:
            continue
        ys, xs = np.nonzero(labels == index)
        v = np.column_stack([xs, ys]).astype(np.float32) @ across
        heights.append(float(v.max() - v.min()))
    if not heights:
        return 0.0
    tall = [h for h in heights if h >= 0.4 * max(heights)]
    return float(np.median(tall))


def measure(
    image: np.ndarray, page_mask: np.ndarray, bbox: BBox, by_colour: bool = False,
    ink_rule: str = "auto", angle: float | None = None, grow: int = SFX_DILATION,
    parts: list[BBox] | None = None, whole: bool = False, halo: int = SFX_HALO,
    poly: list[tuple[int, int]] | None = None, ink_only: bool = False, area: bool = False,
) -> dict:
    """The original SFX's ink, outline, footprint and angle.

    By default the lettering is the detector mask inside ``bbox``. With
    ``by_colour`` the box itself is trusted instead: everything in it that is
    not the surrounding background is lettering, and nothing outside it is
    touched -- for words the detector split or merged with their neighbours.

    The footprint is the lettering's minimum-area rectangle, which is what
    gives the angle -- SFX are drawn slanted far more often than level.
    """
    import cv2

    from comic.image_processing.mask import dilate

    box = np.zeros(page_mask.shape[:2], dtype=np.uint8)
    if poly:
        cv2.fillPoly(box, [np.array(poly, dtype=np.int32)], 1)
    else:
        for b in parts or [bbox]:
            box[b.y1:b.y2, b.x1:b.x2] = 1
    own = np.zeros(page_mask.shape[:2], dtype=np.uint8)
    if by_colour:
        own[:] = box
        letters, ink, edge, outlined = _letter_colours(image, own, grow=0, ink_rule=ink_rule)
        if area:
            letters = box.copy()
        elif ink_only:
            letters = ((np.abs(image.astype(np.int32) - np.array(ink, dtype=np.int32)).sum(axis=-1)
                        < INK_TOLERANCE) & (box > 0)).astype(np.uint8)
        else:
            # Follow the strokes, not the box (the operator, 2026-11-10): art
            # inside the rectangle -- hair, a hand, shelf dots -- is not lettering.
            letters = keep_strokes(letters, ((page_mask > 0) & (box > 0)).astype(np.uint8))
        footprint, reach = letters, dilate(box, 2)
    else:
        own[box > 0] = page_mask[box > 0] > 0
        letters, ink, edge, outlined = _letter_colours(image, own, grow=grow, ink_rule=ink_rule)
        footprint, reach = own, dilate(own, max(3 * SFX_DILATION, grow + halo))
        # A box given with mask mode exists to keep neighbouring marks out.
        if parts or bbox != _bbox_of(page_mask, own):
            reach = reach & dilate(box, 2 + max(0, halo - SFX_HALO))
        # The detector marked these strokes as lettering; erase them even
        # where the colour rule lost them -- a letter touching dark artwork
        # joins it and reads as background (ch.0 TAP: the T stayed).
        letters = letters | own
        if whole:
            # Seed from the box, not just the mask: a letter the detector
            # missed entirely (ch.6 SLAM: the S) touches no mask at all.
            blobs = _touched_blobs(image, box, ink)
            letters = letters | blobs
            footprint = footprint | blobs
            reach = reach | dilate(blobs, halo + 2)

    if not footprint.any():
        # No stroke found (Xiao Gou ch.9 p.24: a thin red 咻): measuring nothing
        # gave a 0x0 footprint, Photoshop scaled the word to 0 px and the
        # build hung. The box the SFX was given is its footprint then.
        footprint = (box > 0).astype(np.uint8)
    ys, xs = np.nonzero(footprint)
    points = np.column_stack([xs, ys]).astype(np.float32)
    if angle is None:
        (cx, cy), (w, h), angle = cv2.minAreaRect(points)
        if w < h:
            w, h = h, w
            angle -= 90
        while angle > 90:
            angle -= 180
        while angle < -90:
            angle += 180
    else:
        # The footprint's extent along the given direction and across it.
        t = np.deg2rad(angle)
        u = points @ np.array([np.cos(t), np.sin(t)], dtype=np.float32)
        v = points @ np.array([-np.sin(t), np.cos(t)], dtype=np.float32)
        w, h = float(u.max() - u.min()), float(v.max() - v.min())
        mu, mv = (u.max() + u.min()) / 2, (v.max() + v.min()) / 2
        cx, cy = mu * np.cos(t) - mv * np.sin(t), mu * np.sin(t) + mv * np.cos(t)
    return {
        "ink": ink, "edge": edge, "outlined": outlined,
        "cx": float(cx), "cy": float(cy), "width": float(w), "height": float(h),
        "angle": float(angle),
        # Erase the letters *and* their outline, which reaches past the
        # detector mask -- but only near it, never into the rest of the panel.
        # The letters plus their halo: an outline as light as the background
        # clusters with it, and leaving it in the hole makes every fill --
        # Content-Aware, LaMa or Telea -- smear it into a pale blob (ch.46).
        "glyphs": dilate(letters, halo) & reach,
        "letter_height": letter_height(image, dilate(letters, halo) & reach, ink, float(angle)),
    }


def sfx_layers(
    image: np.ndarray,
    page_mask: np.ndarray,
    page: int,
    candidates: list[SfxCandidate],
    answers: dict[str, SfxItem],
    font: str,
    ink_rule: str = "auto",
    fonts: dict[str, str] | None = None,
    note_font: str | None = None,
    note_font_file: str | None = None,
    occupied: np.ndarray | None = None,
) -> tuple[list[SfxLayer], np.ndarray, np.ndarray, list[str]]:
    """Layers to set, the masks to erase (Content-Aware, LaMa), and skip notes.

    ``fonts`` maps a font file named by an item to its PostScript name.
    """
    fonts = fonts or {}
    erase = np.zeros(page_mask.shape[:2], dtype=np.uint8)
    erase_local = np.zeros(page_mask.shape[:2], dtype=np.uint8)
    layers: list[SfxLayer] = []
    notes: list[str] = []
    boxes = {c.region_id: c.bbox for c in candidates}
    prefix = f"p{page:03d}_"
    busy: tuple[np.ndarray, np.ndarray] | None = None
    for rid, item in sorted(answers.items()):
        if rid.startswith(prefix) and item.kind == "note":
            text, angle, b = item.text.strip(), item.angle or 0.0, item.bbox
            if b is None:
                # No place given: the nearest empty spot -- gutter, margin,
                # blank corner -- never on the art beside the SFX.
                near = boxes.get(rid)
                if near is None or note_font_file is None:
                    notes.append(f"{rid}: a note without a bbox needs its SFX's dropped region")
                    continue
                if busy is None:
                    busy = placement_masks(image, occupied)
                spot = find_spot(busy, ((near.x1 + near.x2) / 2, (near.y1 + near.y2) / 2),
                                 text, note_font_file, image.shape[1])
                if spot is None:
                    notes.append(f"{rid}: no empty spot for the note")
                    continue
                for taken in busy:
                    taken[spot.y1:spot.y2, spot.x1:spot.x2] = 1
                text, angle = chr(10).join(spot.lines), spot.angle
                b = BBox(x1=spot.x1, y1=spot.y1, x2=spot.x2, y2=spot.y2)
            turned = abs(angle) > 45
            width = float(b.y2 - b.y1 if turned else b.x2 - b.x1) * item.scale
            height = float(b.x2 - b.x1 if turned else b.y2 - b.y1) * item.scale
            layers.append(SfxLayer(
                region_id=rid, text=text, font=note_font or font,
                plain=plain_letters(text), letter_height=0.0,
                colour=NOTE_INK, stroke_colour=NOTE_HALO,
                stroke_ratio=NOTE_HALO_PX / max(height, 1.0),
                cx=(b.x1 + b.x2) / 2 + item.offset[0], cy=(b.y1 + b.y2) / 2 + item.offset[1],
                width=width, height=height, angle=angle,
            ))
            continue
        if not rid.startswith(prefix) or item.kind != "sfx":
            continue
        box = item.bbox or boxes.get(rid)
        if item.poly:
            xs, ys = [x for x, _y in item.poly], [y for _x, y in item.poly]
            box = BBox(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys))
        if box is None:
            notes.append(f"{rid}: no such dropped region -- give it a bbox")
            continue
        missing = [p for p in item.parts if p not in boxes]
        if missing:
            notes.append(f"{rid}: no such dropped region(s) {', '.join(missing)}")
            continue
        pieces = [box] + [boxes[p] for p in item.parts] if item.parts else None
        by_colour = (item.bbox is not None or item.poly is not None)             and item.letters in ("colour", "ink", "area") and not pieces
        m = measure(image, page_mask, box, by_colour=by_colour, ink_rule=item.ink or ink_rule,
                    angle=item.angle, grow=item.grow, parts=pieces,
                    whole=item.letters == "whole", halo=item.halo, poly=item.poly,
                    ink_only=item.letters == "ink", area=item.letters == "area")
        if item.outline is not None:
            m["outlined"] = item.outline
        shown = item.text.strip() if item.keep_case else item.text.strip().upper()
        # A vertical SFX is set one letter per line now (the operator,
        # 2026-11-09), no longer erased with LaMa and left to set by hand.
        vertical = item.angle is None and abs(m["angle"]) > MAX_ANGLE
        if item.pre_erased:
            pass  # LaMa took it at cleaning; the base layer is already clean there
        elif item.fill == "lama":
            erase_local |= m["glyphs"]
        else:
            erase |= m["glyphs"]
        if item.erase_only:
            continue
        # The group's SFX standard (the operator, 2026-11-09): reproduce the
        # original's look -- strokes, glow, shadow, orientation -- at 70-80%.
        style = _style_fields(image, m)
        if style.pop("_textured", False):
            notes.append(f"{rid}: textured/patterned original -- '{shown}' set plain, finish its look by hand")
        common = dict(font=fonts.get(item.font, font), colour=item.colour or m["ink"],
                      stroke_colour=m["edge"] if m["outlined"] else (0, 0, 0),
                      stroke_ratio=0.09 if m["outlined"] else 0.0, **style)
        if vertical:
            # One letter per line; tabs give the column its lean (in Photoshop).
            from comic.photoshop.sfx_style import slant_of, vertical_text

            text = vertical_text(shown)
            layers.append(SfxLayer(
                region_id=rid, text=text, plain=plain_letters(text), letter_height=0.0,
                cx=m["cx"] + item.offset[0], cy=m["cy"] + item.offset[1],
                # the footprint's long side is the column's height
                width=m["height"] * item.scale, height=m["width"] * item.scale,
                angle=0.0, vertical=True, slant=slant_of(m["angle"]), **common,
            ))
            continue
        split = None
        if not item.parts and not item.poly and tuple(item.offset) == (0.0, 0.0) and len(shown) > 1:
            from comic.photoshop.sfx_style import pieces, split_text

            gap = max(4, int(0.25 * (m["letter_height"] or m["height"] / 2)))
            found = pieces(m["glyphs"] > 0, gap)
            if found:
                # In reading order along the word's own direction.
                t = np.deg2rad(m["angle"])
                axis = np.array([np.cos(t), np.sin(t)])
                found.sort(key=lambda q: float(np.array(_footprint(q)[:2]) @ axis))
                texts = split_text(shown, [_footprint(q)[2] for q in found])
                if texts:
                    split = list(zip(found, texts))
        if split:
            # One text layer per piece, as the original is drawn ("QU" + "ẪY").
            for k, (piece, part) in enumerate(split, 1):
                cx, cy, w, h, ang = _footprint(piece)
                layers.append(SfxLayer(
                    region_id=f"{rid}.{k}", text=part, plain=plain_letters(part), letter_height=0.0,
                    cx=cx, cy=cy, width=w * item.scale, height=h * item.scale, angle=ang, **common,
                ))
            continue
        layers.append(SfxLayer(
            region_id=rid, text=shown, plain=plain_letters(shown),
            letter_height=m["letter_height"] * item.scale if item.size_by == "letter" else 0.0,
            cx=m["cx"] + item.offset[0], cy=m["cy"] + item.offset[1],
            width=m["width"] * item.scale, height=m["height"] * item.scale,
            angle=m["angle"], **common,
        ))
    return layers, erase, erase_local, notes


def series_ink_rule(fonts_file: Path) -> str:
    """The series' SFX convention from ``fonts.yaml`` (``sfx: {ink: ...}``)."""
    import yaml

    if not fonts_file.exists():
        return "auto"
    raw = yaml.safe_load(fonts_file.read_text(encoding="utf-8")) or {}
    rule = str(((raw.get("sfx") or {}).get("ink")) or "auto")
    if rule not in ("auto", "dark", "light"):
        raise ValueError(f"{fonts_file.name}: sfx.ink must be auto, dark or light")
    return rule


def to_script(layers: list[SfxLayer]) -> list[dict]:
    return [asdict(layer) for layer in layers]
