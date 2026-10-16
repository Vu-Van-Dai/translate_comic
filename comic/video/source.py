"""Which image a video may show for a page -- and which it never may.

The operator's rule (2026-10-13, clarified 2026-10-14): a video may show any
page except **our Vietnamese translation** -- the source as served (English
scanlation, raw Japanese, whatever it is) or a text-free version. The
original is preferred since then: text-free art leaves empty white balloons
that read as unfinished on screen. The allow-list:

1. ``photoshop/NNN_art.png`` -- the Photoshop "Cleaned" layer with the text
   groups hidden: dialogue, translated SFX and other groups' watermarks gone.
2. ``cleaned/NNN.*`` -- the pipeline's cleaned page: dialogue erased, SFX kept.
3. the original page -- raw, untranslated.

``translated/``, the Photoshop previews (``NNN.png``) and ``library/`` hold
translated lettering. ``check_allowed`` refuses them, and every image the
renderer opens goes through it.

**Per-series exception** (the operator, 2026-10-14, Mi Imitador): a series
whose ``video.yaml`` sets ``allow_translated: true`` may show its finished
Vietnamese pages from ``library/`` (``ArtSource.TRANSLATED``), and that is
then its default source. The switch is read here, from the path's own
series, so no caller can bypass it.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from comic.paths import ChapterPaths

#: Directory names whose images carry translated lettering.
FORBIDDEN_DIRS = frozenset({"translated", "library"})


class ArtSource(str, Enum):
    #: Our finished Vietnamese page -- only where video.yaml allows it.
    TRANSLATED = "translated"
    ART = "art"
    CLEANED = "cleaned"
    ORIGINAL = "original"


class ForbiddenSource(ValueError):
    """An image with translated dialogue was about to go into a video."""


def _series_of(path: Path) -> str | None:
    """The series a page belongs to: the folder after ``library``/``workspace``."""
    parts = path.parts
    for i, p in enumerate(parts[:-1]):
        if p.lower() in ("library", "workspace"):
            return parts[i + 1]
    return None


def translated_allowed(series: str | None) -> bool:
    from comic.video.styles import series_video

    return bool(series) and series_video(series).allow_translated


def check_allowed(path: Path) -> Path:
    path = Path(path)
    if translated_allowed(_series_of(path)):
        return path
    parts = {p.lower() for p in path.parts}
    if parts & FORBIDDEN_DIRS:
        raise ForbiddenSource(f"{path}: pages under {sorted(parts & FORBIDDEN_DIRS)} carry translated text")
    if path.parent.name.lower() == "photoshop" and not path.stem.endswith("_art"):
        raise ForbiddenSource(f"{path}: a Photoshop preview carries translated text; use NNN_art.png")
    return path


def _one(folder: Path, page: int) -> Path | None:
    for f in sorted(folder.glob(f"{page:03d}.*")):
        if f.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}:
            return f
    return None


def art_file(paths: ChapterPaths, page: int, prefer: ArtSource = ArtSource.ART) -> tuple[Path, ArtSource]:
    """The best allowed image for ``page``, at or below ``prefer``."""
    order = list(ArtSource)[list(ArtSource).index(prefer):]
    for kind in order:
        if kind is ArtSource.TRANSLATED:
            found = _one(paths.library, page) if translated_allowed(paths.series) else None
        elif kind is ArtSource.ART:
            f = paths.workspace / "photoshop" / f"{page:03d}_art.png"
            found = f if f.exists() else None
        elif kind is ArtSource.CLEANED:
            found = _one(paths.cleaned, page)
        else:
            found = _one(paths.source_pages, page) or _one(paths.original, page)
        if found is not None:
            return check_allowed(found), kind
    raise FileNotFoundError(f"page {page}: no art, cleaned or original image")


def default_source(series: str) -> ArtSource:
    """Translated pages where the series allows them, else the original."""
    return ArtSource.TRANSLATED if translated_allowed(series) else ArtSource.ORIGINAL
