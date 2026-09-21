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
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from comic.schemas import RegionOverride, TextKind, TextRegion
from comic.typesetting.fit import Layout, fit_text, load_font


@dataclass(frozen=True)
class Style:
    """How one kind of text is set."""

    font: str
    #: Outline drawn behind the glyphs, in pixels at a 20px font, scaled.
    stroke_em: float = 0.0
    colour: tuple[int, int, int] = (20, 20, 20)
    stroke_colour: tuple[int, int, int] = (255, 255, 255)
    uppercase: bool = False


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


def style_for(kind: TextKind) -> Style:
    return STYLES.get(kind, STYLES[TextKind.DIALOGUE])


def background_luma(page: np.ndarray, area: np.ndarray) -> float:
    """Mean luminance of the space a line will be set into."""
    spot = area > 0
    if not spot.any():
        return 255.0
    grey = page if page.ndim == 2 else page[:, :, :3].mean(axis=2)
    return float(grey[spot].mean())


def contrasting(style: Style, page: np.ndarray, area: np.ndarray) -> Style:
    """The same style, recoloured so it is readable on this background."""
    if background_luma(page, area) >= DARK_BACKGROUND:
        return style
    return replace(style, colour=(240, 240, 240), stroke_colour=(10, 10, 10))


def font_file(fonts_dir: Path, name: str) -> Path:
    path = fonts_dir / name
    if path.exists():
        return path
    fallback = fonts_dir / "BeVietnamPro-Regular.ttf"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"no usable font in {fonts_dir}")


def layout_region(
    region: TextRegion,
    text: str,
    area: np.ndarray,
    fonts_dir: Path,
    override: RegionOverride | None = None,
) -> tuple[Layout, Style]:
    override = override or RegionOverride()
    kind = override.style_kind or region.kind
    style = style_for(kind)
    path = font_file(fonts_dir, style.font)
    shown = text.upper() if style.uppercase else text
    forced = [
        line.upper() if style.uppercase else line
        for line in override.forced_lines
        if line.strip()
    ]
    layout = fit_text(
        shown, area, path,
        forced_lines=forced or None,
        font_delta=override.font_delta,
    )
    if override.dx or override.dy:
        layout = replace(layout, x=layout.x + override.dx, y=layout.y + override.dy)
    return layout, style


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

    for index, line in enumerate(layout.lines):
        left, top, right, _bottom = font.getbbox(line)
        width = right - left
        x = layout.x + (layout.width - width) / 2 - left
        y = layout.y + index * layout.line_height - top
        draw.text(
            (x, y),
            line,
            font=font,
            fill=style.colour,
            stroke_width=stroke,
            stroke_fill=style.stroke_colour,
        )


def render_page(
    cleaned: np.ndarray,
    regions: list[TextRegion],
    translations: dict[str, str],
    areas: dict[str, np.ndarray],
    fonts_dir: Path,
    overrides: dict[str, RegionOverride] | None = None,
) -> tuple[np.ndarray, dict[str, Layout]]:
    """Set every translated region onto the cleaned page.

    Returns the rendered page and the layout used for each region, which is
    what the visual_qa pre-checks measure and what its fixes adjust.
    """
    page = Image.fromarray(cleaned[:, :, ::-1].copy())  # BGR -> RGB
    layouts: dict[str, Layout] = {}

    for region in regions:
        text = translations.get(region.id, "").strip()
        if not text:
            continue
        area = areas.get(region.id)
        if area is None or not area.any():
            continue
        layout, style = layout_region(
            region, text, area, fonts_dir,
            (overrides or {}).get(region.id),
        )
        style = contrasting(style, cleaned, area)
        layouts[region.id] = layout
        draw_layout(page, layout, style, fonts_dir)

    return np.asarray(page)[:, :, ::-1].copy(), layouts
