"""Drawing fitted Vietnamese onto a cleaned page.

Styling is per text kind, from the series profile: dialogue is set in a plain
face, narration in a serif, a shout in a heavy condensed face. The renderer
does not decide what a region is -- detection typed it and the agent can
correct that -- it only honours the type.

Text is drawn through PIL rather than OpenCV because OpenCV's Hershey fonts
have no Vietnamese at all, and would silently drop every diacritic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from comic.schemas import RegionOverride, TextKind, TextRegion
from comic.typesetting.coverage import SUBSTITUTES
from comic.typesetting.fit import MAX_FONT_PX, MIN_FONT_PX, NBSP, Layout, fit_text, load_font


@dataclass(frozen=True)
class Style:
    """How one kind of text is set."""

    font: str
    #: Outline drawn behind the glyphs, in pixels at a 20px font, scaled.
    stroke_em: float = 0.0
    colour: tuple[int, int, int] = (20, 20, 20)
    stroke_colour: tuple[int, int, int] = (255, 255, 255)
    uppercase: bool = False
    #: The face for a line set in sentence case when ``font`` draws its
    #: lowercase as capitals (most comic caps faces do). See ``cased_style``.
    sentence_font: str | None = None
    #: Flip to light type on a dark background. Off: a chapter wants one ink
    #: colour throughout, and anything needing a flip is drawn-in art we do
    #: not translate. See ``contrasting``.
    auto_contrast: bool = False
    #: Margin kept clear inside the bubble on every side, as a fraction of
    #: its shorter side. See ``fit._inset``.
    inset: float = 0.0
    #: Multiplies the source-size ceiling (``source_size_cap``): below 1 sets
    #: type smaller than the lettering it replaces.
    size_scale: float = 1.0
    #: Margin kept clear on every side of the block, in ems of the set size.
    #: None keeps the built-in side-only padding. See ``fit._padded``.
    pad_em: float | None = None
    #: Leading from the face's own accent span (``fit.leading_em``) rather
    #: than the fixed 1.22 em. ``leading: auto`` in fonts.yaml.
    auto_leading: bool = False


#: Falls back to `dialogue` for any kind not named here.
STYLES: dict[TextKind, Style] = {
    TextKind.DIALOGUE: Style("BeVietnamPro-Regular.ttf"),
    TextKind.THOUGHT: Style("BeVietnamPro-Italic.ttf"),
    TextKind.NARRATION: Style("Lora-Variable.ttf"),
    TextKind.CAPTION: Style("Nunito-Variable.ttf"),
    TextKind.SHOUT: Style(
        "Anton-Regular.ttf", stroke_em=0.06, uppercase=True
    ),
}


#: Below this background luminance the lettering has to be light.
#:
#: Inverted panels -- white lettering on black -- are ordinary in manga, and
#: the flat fill correctly restores them to black. Drawing the usual dark type
#: onto that is invisible: measured on this chapter, three lines disappeared
#: completely and the QA residue check reported "159% of the source lettering
#: still visible", which was the black fill being counted as ink.
DARK_BACKGROUND = 110



#: Shared lettering presets: ``profiles/_presets/<name>.yaml``.
PRESETS_DIR = "_presets"


def read_profile(path: Path, _seen: tuple[str, ...] = ()) -> dict:
    """``fonts.yaml`` with its ``preset:`` chain merged underneath it.

    A series names a format preset (``preset: manhwa``), which may name the
    shared mood library (``preset: voices``). Later files win key by key;
    ``styles`` and ``voices`` merge per entry, so a series overrides one
    voice without restating the rest. Presets live beside the series
    profiles, in ``profiles/_presets/``.
    """
    import yaml

    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    name = raw.pop("preset", None)
    if not name:
        return raw
    if name in _seen:
        raise ValueError(f"preset loop: {' -> '.join(_seen + (name,))}")
    base_dir = path.parent if path.parent.name == PRESETS_DIR else path.parent.parent / PRESETS_DIR
    base_path = base_dir / f"{name}.yaml"
    if not base_path.exists():
        raise ValueError(f"{path.name}: no preset {name!r} at {base_path}")
    merged = read_profile(base_path, _seen + (name,))
    for key, value in raw.items():
        if key in ("styles", "voices") and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
        else:
            merged[key] = value
    return merged


def load_styles(path: Path) -> dict[TextKind, Style]:
    """Per-kind styling for one series, from ``profiles/<series>/fonts.yaml``.

    Lettering is a per-series decision -- a comedy and a horror title do not
    want the same face -- and the fonts a translator is entitled to use are
    theirs to choose, so this reads whatever they put in ``assets/fonts/``
    rather than shipping a fixed set.

    Anything the file omits keeps the built-in default, so a profile naming
    only ``dialogue`` is valid. Returns the defaults unchanged when there is
    no file.

        ink: [20, 20, 20]         # one colour for the whole chapter
        stroke: [255, 255, 255]
        auto_contrast: false
        styles:
          dialogue:  {font: "MyComic.ttf"}
          narration: {font: "MyComic-Bold.ttf", uppercase: true}
          shout:     {font: "MyComic-Heavy.ttf", stroke_em: 0.06}

    The face still has to pass the Vietnamese coverage gate; a font missing
    stacked diacritics renders tofu silently, so it is rejected at
    registration rather than at the eye.
    """
    styles = dict(STYLES)
    raw = read_profile(path)
    if not raw:
        return styles

    shared: dict[str, object] = {}
    if "ink" in raw:
        shared["colour"] = tuple(raw["ink"])
    if "stroke" in raw:
        shared["stroke_colour"] = tuple(raw["stroke"])
    if "auto_contrast" in raw:
        shared["auto_contrast"] = bool(raw["auto_contrast"])
    for key in ("inset", "size_scale", "pad_em"):
        if key in raw:
            shared[key] = float(raw[key])
    if "sentence_font" in raw:
        shared["sentence_font"] = str(raw["sentence_font"])
    if "leading" in raw:
        if raw["leading"] not in ("auto", "fixed"):
            raise ValueError(f"{path.name}: leading must be 'auto' or 'fixed'")
        shared["auto_leading"] = raw["leading"] == "auto"

    known = set(Style.__dataclass_fields__)
    #: Keys each style or voice set itself; those win over the shared ones.
    explicit: dict[object, set[str]] = {}
    for name, spec in (raw.get("styles") or {}).items():
        try:
            kind = TextKind(name)
        except ValueError as exc:
            valid = ", ".join(k.value for k in TextKind)
            raise ValueError(
                f"{path.name}: {name!r} is not a text kind (expected: {valid})"
            ) from exc
        spec = dict(spec or {})
        unknown = set(spec) - known
        if unknown:
            raise ValueError(
                f"{path.name}: {name} has unknown key(s): {', '.join(sorted(unknown))}"
            )
        for key in ("colour", "stroke_colour"):
            if key in spec:
                spec[key] = tuple(spec[key])
        styles[kind] = replace(styles.get(kind, STYLES[TextKind.DIALOGUE]), **spec)
        explicit[kind] = set(spec)

    # Voices: named faces for one line's delivery (a stammer, a whisper),
    # chosen per region by the agent. Keyed by plain name beside the kinds;
    # each starts from the dialogue style, so it inherits case and spacing.
    for name, spec in (raw.get("voices") or {}).items():
        if name in {k.value for k in TextKind}:
            raise ValueError(f"{path.name}: voice {name!r} shadows a text kind")
        spec = {k: v for k, v in dict(spec or {}).items() if k != "use"}
        unknown = set(spec) - known
        if unknown:
            raise ValueError(
                f"{path.name}: voice {name} has unknown key(s): {', '.join(sorted(unknown))}"
            )
        for key in ("colour", "stroke_colour"):
            if key in spec:
                spec[key] = tuple(spec[key])
        styles[name] = replace(styles[TextKind.DIALOGUE], **spec)  # type: ignore[index]
        explicit[name] = set(spec)

    # Shared settings apply to every kind, including ones the file did not name.
    # A style's own setting wins: a whisper voice keeps its smaller size_scale.
    if shared:
        styles = {
            k: replace(v, **{key: val for key, val in shared.items()
                             if key not in explicit.get(k, set())})
            for k, v in styles.items()
        }
    return styles


def voice_uses(path: Path) -> dict[str, str]:
    """Each voice in ``fonts.yaml`` and its ``use:`` line, for the agent."""
    raw = read_profile(path)
    return {
        name: str((spec or {}).get("use", "")).strip()
        for name, spec in (raw.get("voices") or {}).items()
    }


def style_for(kind: TextKind | str, styles: dict[TextKind, Style] | None = None) -> Style:
    table = styles or STYLES
    return table.get(kind, table.get(TextKind.DIALOGUE, STYLES[TextKind.DIALOGUE]))



def background_luma(page: np.ndarray, area: np.ndarray) -> float:
    """Mean luminance of the space a line will be set into."""
    spot = area > 0
    if not spot.any():
        return 255.0
    grey = page if page.ndim == 2 else page[:, :, :3].mean(axis=2)
    return float(grey[spot].mean())


#: The light alternative, used when it separates further from the background.
LIGHT_INK = (240, 240, 240)
DARK_STROKE = (10, 10, 10)


#: Luma spread above which the space a line is set into is textured.
TEXTURED_SPREAD = 12
#: Halo for type set over texture, in ems.
HALO_EM = 0.2


def haloed(style: Style, page: np.ndarray, area: np.ndarray) -> Style:
    """Give type a halo when it is set over texture.

    Cleaning now erases only the old glyphs, so a dotted or shaded bubble
    keeps its tone -- and dark type laid straight onto dot screentone is hard
    to read (Super Lovers ch.0 p.8). Letterers knock a thin halo of the
    paper colour out around such type; so does this, in the light stroke
    colour, only where the space behind the line is not plain.
    """
    spot = area > 0
    if style.stroke_em >= HALO_EM or not spot.any():
        return style
    grey = page if page.ndim == 2 else page[:, :, :3].mean(axis=2)
    if float(grey[spot].std()) <= TEXTURED_SPREAD:
        return style
    return replace(style, stroke_em=HALO_EM)


def contrasting(style: Style, page: np.ndarray, area: np.ndarray) -> Style:
    """The same style, left alone unless the profile asks for auto-contrast.

    **One ink colour, no shading and no intensity variation**, because that is
    what a letterer does: a chapter whose type changes colour panel by panel
    reads as inconsistent even when each individual panel is legible.

    This used to flip to light type on dark backgrounds. That is off by
    default now (``auto_contrast`` in ``profiles/<series>/fonts.yaml``), and
    the reason it is safe to switch off is that the text we set belongs in
    bubbles and caption boxes, which cleaning fills with their own pale
    background. Type that would have needed flipping was type placed over
    artwork -- SFX and drawn-in labels -- which should not be translated at
    all.

    Keep that in mind before turning it on: if a *bubble* ever comes out dark,
    the fix is the cleaning tier, not the ink.
    """
    style = haloed(style, page, area)
    if not style.auto_contrast:
        return style
    luma = background_luma(page, area)
    dark_gap = abs(luma - sum(style.colour) / 3)
    light_gap = abs(luma - sum(LIGHT_INK) / 3)
    if dark_gap >= light_gap:
        return style
    return replace(style, colour=LIGHT_INK, stroke_colour=DARK_STROKE)


def font_file(fonts_dir: Path, name: str) -> Path:
    path = fonts_dir / name
    if path.exists():
        return path
    fallback = fonts_dir / "BeVietnamPro-Regular.ttf"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"no usable font in {fonts_dir}")


#: Detector-mask line height per pixel of source font size. The mask runs a
#: few pixels fatter than the glyphs it marks: measured on ch.45, 22px capitals
#: gave 27px mask bands, and those capitals were set at about 30px.
SOURCE_BAND_EM = 0.9
#: How far past the source's own type size a translation may grow. A little,
#: because Vietnamese stacks diacritics above its capitals; not more, because
#: a letterer matches the size of what they replace -- a short line in a big
#: bubble does not get shouted just because there is room.
SOURCE_SIZE_HEADROOM = 1.15
#: Box area one character of caps lettering takes, in square ems (advance
#: about 0.62 em times a 1.2 em line).
DENSITY_EM2 = 0.75
#: How far the band measurement may exceed the density estimate before the
#: estimate wins: loose leading and short lines make density run low.
DENSITY_SLACK = 1.3


def source_size_cap(page_mask: np.ndarray | None, region: TextRegion) -> int:
    """The largest size this region's translation should be set at.

    Measured from the source lettering's own line height in the detector mask.
    Without it the fitter's only ceiling is the space available, which was fine
    while short lines were confined to their text box, and became a problem as
    soon as they were given the whole bubble: "I LIKE YOU." came back at about
    twice the source's size.
    """
    if page_mask is None:
        return MAX_FONT_PX
    from comic.ocr.registry import line_bands

    box = region.bbox
    crop = page_mask[box.y1 : box.y2, box.x1 : box.x2]
    bands = line_bands(crop, crop)
    if not bands:
        return MAX_FONT_PX
    heights = sorted(b - a for a, b in bands)
    source_px = heights[len(heights) // 2] / SOURCE_BAND_EM
    # Bands merge when lines are set tight -- one 69px "band" for four 16px
    # lines of a caption (Super Lovers ch.0 p.13), which lifted the cap to
    # the 64px ceiling and set that caption at twice its neighbours. The
    # lettering's density bounds it: n characters fill the box at about
    # DENSITY_EM^2 each.
    chars = len((region.source_text or "").strip())
    if chars >= 4:
        density_px = ((box.x2 - box.x1) * (box.y2 - box.y1) / (chars * DENSITY_EM2)) ** 0.5
        source_px = min(source_px, density_px * DENSITY_SLACK)
    return max(MIN_FONT_PX, min(MAX_FONT_PX, int(round(source_px * SOURCE_SIZE_HEADROOM))))


@lru_cache(maxsize=64)
def _charset(path: str) -> frozenset[int]:
    """What the face can really set: its cmap, less letters drawn wrong."""
    from comic.typesetting.coverage import font_charset, tone_defects

    broken = {ord(c) for c in tone_defects(Path(path))}
    return frozenset(font_charset(Path(path))) - broken


def printable(text: str, path: Path) -> str:
    """``text`` with punctuation the face lacks swapped for what it has.

    The coverage gate admits a face missing only ``coverage.SUBSTITUTES``
    punctuation, so the swap has to happen here or those marks render as tofu.
    """
    chars = _charset(str(path))
    for missing, stand_in in SUBSTITUTES.items():
        if missing in text and ord(missing) not in chars:
            text = text.replace(missing, stand_in)
    return text


def missing_for(text: str, path: Path) -> set[str]:
    """Characters of ``text`` this face cannot set, after substitution."""
    chars = _charset(str(path))
    return {c for c in printable(text, path) if not c.isspace() and ord(c) not in chars}


#: An area no bigger than this multiple of the region's own box is that box:
#: ``fit_areas`` falls back to it when the lettering sits on open art.
OWN_BOX_RATIO = 1.25


def _is_own_box(area: np.ndarray, region: TextRegion) -> bool:
    box = region.bbox
    box_area = max(1, (box.x2 - box.x1) * (box.y2 - box.y1))
    return int(np.count_nonzero(area)) <= OWN_BOX_RATIO * box_area


#: A source line is "written in sentence case" when at least this share of its
#: letters are lowercase. English scanlations letter speech in caps and their
#: asides on the art ("As he said.", "Yeah yeah.") in sentence case.
SOURCE_LOWER_SHARE = 0.3
#: How much larger sentence case may be set than the caps it replaces.
SENTENCE_CASE_BOOST = 1.25


def source_is_sentence_case(source: str) -> bool:
    letters = [c for c in source if c.isalpha()]
    if len(letters) < 2:
        return False
    return sum(c.islower() for c in letters) >= SOURCE_LOWER_SHARE * len(letters)


@lru_cache(maxsize=64)
def caps_only(path: str) -> bool:
    """Whether a face draws its lowercase as capitals (most comic faces)."""
    from PIL import ImageFont

    font = ImageFont.truetype(path, 60)
    same = 0
    for ch in "aegnrt":
        image_l, image_u = (Image.new("L", (120, 140), 0) for _ in range(2))
        ImageDraw.Draw(image_l).text((20, 20), ch, font=font, fill=255)
        ImageDraw.Draw(image_u).text((20, 20), ch.upper(), font=font, fill=255)
        same += np.array_equal(np.asarray(image_l), np.asarray(image_u))
    return same >= 4


def cased_style(
    style: Style, region: TextRegion, area: np.ndarray, voiced: bool, fonts_dir: Path
) -> Style:
    """The style with the case convention Vietnamese groups follow.

    Measured on two established groups (docs/lettering.md, section 9): caps
    for speech and thought bubbles; sentence case for caption boxes, the
    soft cloud, phone text -- and for asides lettered on the art outside
    any bubble. Kinds and voices carry the first rules (``uppercase``).
    The last needs the page: text with no bubble around it whose source was
    itself lettered in sentence case. A shout on the art stays caps, because
    its source was.

    Sentence case needs a face with real lowercase, so a caps-only face
    hands the line to ``sentence_font``.
    """
    if (
        style.uppercase and not voiced
        and region.kind is not TextKind.SHOUT
        and _is_own_box(area, region)
        and source_is_sentence_case(region.source_text or "")
    ):
        style = replace(style, uppercase=False)
    if not style.uppercase and style.sentence_font:
        if caps_only(str(font_file(fonts_dir, style.font))):
            style = replace(style, font=style.sentence_font)
    return style


def layout_region(
    region: TextRegion,
    text: str,
    area: np.ndarray,
    fonts_dir: Path,
    override: RegionOverride | None = None,
    styles: dict[TextKind, Style] | None = None,
    max_size: int = MAX_FONT_PX,
) -> tuple[Layout, Style]:
    override = override or RegionOverride()
    table = styles or STYLES
    if override.voice and override.voice in table:
        style = table[override.voice]  # type: ignore[index]
    else:
        style = style_for(override.style_kind or region.kind, styles)
    style = cased_style(style, region, area, bool(override.voice), fonts_dir)
    path = font_file(fonts_dir, style.font)
    cased = text.upper() if style.uppercase else text
    if missing_for(cased, path):
        # A voice face need not cover every letter (the operator's call): a
        # line that needs one it lacks is set in its kind's face instead, so
        # it never renders tofu or a different word.
        fallback = style_for(override.style_kind or region.kind, styles)
        fallback_path = font_file(fonts_dir, fallback.font)
        fallback_cased = text.upper() if fallback.uppercase else text
        if not missing_for(fallback_cased, fallback_path):
            style, path, cased = fallback, fallback_path, fallback_cased
    shown = printable(cased, path)
    forced = [
        printable((line.upper() if style.uppercase else line).replace(NBSP, " "), path)
        for line in override.forced_lines
        if line.strip()
    ]
    if style.size_scale != 1.0 and max_size < MAX_FONT_PX:
        max_size = max(MIN_FONT_PX, int(round(max_size * style.size_scale)))
    if (not style.uppercase and max_size < MAX_FONT_PX
            and not source_is_sentence_case(region.source_text or "")):
        # The cap measures the source's capitals; sentence case at that size
        # reads a size smaller, its body being lowercase -- captions set this
        # way came out visibly smaller than the boxes they sit in (Super
        # Lovers ch.0 p.13). Match body height instead.
        max_size = min(MAX_FONT_PX, int(round(max_size * SENTENCE_CASE_BOOST)))
    pad_em, inset = style.pad_em, style.inset
    if _is_own_box(area, region):
        # No bubble was found, so the area is the lettering's own box (text on
        # open art). The margin is breathing room inside a *balloon*; taken
        # out of a box this tight it starved a shout set on screentone down to
        # a quarter of its size (Super Lovers ch.1 p.4).
        pad_em, inset = None, 0.0
    def fit(limit: int) -> Layout:
        return fit_text(
            shown, area, path,
            max_size=limit,
            forced_lines=forced or None,
            font_delta=override.font_delta,
            anchor=(region.bbox.x1, region.bbox.y1, region.bbox.x2, region.bbox.y2),
            inset=inset,
            pad_em=pad_em,
            auto_leading=style.auto_leading,
        )

    layout = fit(max_size)
    if (layout.fits and max_size < MAX_FONT_PX and not _is_own_box(area, region)
            and layout.width * layout.height < MIN_FILL * int(np.count_nonzero(area))):
        # Size follows the balloon, not only the source: a line that leaves
        # most of a roomy balloon empty reads as "too small for the speech
        # bubble" (the operator). Let it grow toward a comfortable fill --
        # the fitter still keeps the margin and stops where it no longer fits.
        # But not up to the outline -- type "too big, it touches the frame"
        # was the first complaint (Mi Imitador). Growth stops at MAX_FILL of
        # the balloon; the pad_em margin holds at every size regardless.
        room = int(np.count_nonzero(area))
        limit = min(MAX_FONT_PX, int(round(max_size * FILL_GROWTH)))
        while limit > layout.font_size:
            roomier = fit(limit)
            if roomier.fits and roomier.width * roomier.height <= MAX_FILL * room:
                layout = roomier
                break
            limit -= 1
    if override.dx or override.dy:
        layout = replace(layout, x=layout.x + override.dx, y=layout.y + override.dy)
    return layout, style


def line_tops(layout: Layout, font: ImageFont.FreeTypeFont) -> list[float]:
    """Where each line is drawn (PIL's text origin), on one even baseline grid.

    Each line used to be drawn with its own *ink* top on the grid, so a line
    with stacked accents (VẪN, NHỈ) sat lower than one without (ARATA-KUN):
    uneven gaps, and a block that read off-centre in its balloon (the
    operator, 2026-10-15). Now every line shares the leading, and the whole
    block's ink -- first line's top to last line's bottom -- is centred in
    the fitted box, which is a full ``line_height`` per line and so always
    taller than that ink.
    """
    if not layout.lines:
        return []
    lead = layout.line_height
    first_top = font.getbbox(layout.lines[0])[1]
    last_bottom = font.getbbox(layout.lines[-1])[3]
    ink = (len(layout.lines) - 1) * lead + last_bottom - first_top
    origin = layout.y + (layout.height - ink) / 2 - first_top
    return [origin + i * lead for i in range(len(layout.lines))]


def draw_layout(
    page: Image.Image, layout: Layout, style: Style, fonts_dir: Path
) -> None:
    """Draw one fitted block onto ``page`` in place."""
    if not layout.lines:
        return
    path = str(font_file(fonts_dir, style.font))
    font = load_font(path, layout.font_size)
    draw = ImageDraw.Draw(page)
    stroke = int(round(layout.font_size * style.stroke_em))

    tops = line_tops(layout, font)
    for index, line in enumerate(layout.lines):
        left, _top, right, _bottom = font.getbbox(line)
        width = right - left
        x = layout.x + (layout.width - width) / 2 - left
        y = tops[index]
        draw.text(
            (x, y),
            line,
            font=font,
            fill=style.colour,
            stroke_width=stroke,
            stroke_fill=style.stroke_colour,
        )


#: A text block covering less than this share of its balloon may grow...
MIN_FILL = 0.3
#: ...up to this multiple of the source-size ceiling,
FILL_GROWTH = 1.35
#: but never past covering this share of it.
MAX_FILL = 0.5

#: Kinds set in balloons, which share a size floor on a page.
BUBBLE_KINDS = (TextKind.DIALOGUE, TextKind.THOUGHT, TextKind.SHOUT)
#: No balloon's size ceiling below this share of the page median.
BUBBLE_FLOOR = 0.85

#: Kinds whose boxes read as one series of captions: set at one size a page.
CAPTION_KINDS = (TextKind.CAPTION, TextKind.NARRATION)


def layout_page(
    cleaned: np.ndarray,
    regions: list[TextRegion],
    translations: dict[str, str],
    areas: dict[str, np.ndarray],
    fonts_dir: Path,
    overrides: dict[str, RegionOverride] | None = None,
    styles: dict[TextKind, Style] | None = None,
    page_mask: np.ndarray | None = None,
) -> list[tuple[TextRegion, Layout, Style]]:
    """Every translated region's layout and style -- shared by both renderers.

    Captions and narration on a page are held to one size: each fits its own
    box, so a roomy box set its line at half again the size of the caption
    beside it (Super Lovers ch.0 p.13). A letterer sets a page's captions
    alike; here none may exceed the page's median caption size.
    """
    # A floor for bubbles: a roomy balloon whose source was lettered small
    # (or measured small) came out at half the size of its neighbours -- the
    # operator: "text that appears too small for the speech bubble" (Super
    # Lovers ch.0 p.15). No bubble's ceiling sits below BUBBLE_FLOOR of the
    # page's median bubble ceiling; the fitter still shrinks what cannot fit.
    caps: dict[str, int] = {}
    for region in regions:
        if translations.get(region.id, "").strip() and region.id in areas:
            caps[region.id] = source_size_cap(page_mask, region)
    bubbled = sorted(
        caps[r.id] for r in regions
        if r.id in caps and r.kind in BUBBLE_KINDS and not _is_own_box(areas[r.id], r)
    )
    floor = int(bubbled[len(bubbled) // 2] * BUBBLE_FLOOR) if len(bubbled) >= 3 else 0

    out: list[tuple[TextRegion, Layout, Style]] = []
    for region in regions:
        text = translations.get(region.id, "").strip()
        area = areas.get(region.id)
        if not text or area is None or not area.any():
            continue
        override = (overrides or {}).get(region.id)
        cap = caps[region.id]
        if region.kind in BUBBLE_KINDS and not _is_own_box(area, region):
            cap = max(cap, floor)
        caps[region.id] = cap
        layout, style = layout_region(
            region, text, area, fonts_dir, override, styles, max_size=cap,
        )
        out.append((region, layout, contrasting(style, cleaned, area)))

    def caption(item) -> bool:
        region, _layout, _style = item
        override = (overrides or {}).get(region.id)
        kind = (override.style_kind if override and override.style_kind else region.kind)
        # Only captions in a box of their own: an aside lettered on the art
        # is tagged a caption too, and its small size dragged a whole box's
        # caption down to it (Super Lovers ch.0 p.7).
        return (kind in CAPTION_KINDS and not (override and override.voice)
                and not _is_own_box(areas[region.id], region))

    sizes = sorted(layout.font_size for region, layout, style in out if caption((region, layout, style)))
    if len(sizes) >= 2:
        ceiling = sizes[len(sizes) // 2]
        for index, item in enumerate(out):
            region, layout, style = item
            if caption(item) and layout.font_size > ceiling:
                text = translations[region.id].strip()
                layout, style = layout_region(
                    region, text, areas[region.id], fonts_dir,
                    (overrides or {}).get(region.id), styles,
                    max_size=min(caps[region.id], ceiling),
                )
                out[index] = (region, layout, contrasting(style, cleaned, areas[region.id]))
    return out


def render_page(
    cleaned: np.ndarray,
    regions: list[TextRegion],
    translations: dict[str, str],
    areas: dict[str, np.ndarray],
    fonts_dir: Path,
    overrides: dict[str, RegionOverride] | None = None,
    styles: dict[TextKind, Style] | None = None,
    page_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Layout]]:
    """Set every translated region onto the cleaned page.

    ``page_mask`` is the detector mask, used to size each translation against
    the source lettering it replaces. Without it the only ceiling is the space
    available.

    Returns the rendered page and the layout used for each region, which is
    what the visual_qa pre-checks measure and what its fixes adjust.
    """
    page = Image.fromarray(cleaned[:, :, ::-1].copy())  # BGR -> RGB
    layouts: dict[str, Layout] = {}

    for region, layout, style in layout_page(
        cleaned, regions, translations, areas, fonts_dir, overrides, styles, page_mask
    ):
        layouts[region.id] = layout
        draw_layout(page, layout, style, fonts_dir)

    return np.asarray(page)[:, :, ::-1].copy(), layouts
