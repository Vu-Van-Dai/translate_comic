"""Publishing the Photoshop route's pages into ``library/``.

The library holds finished pages only, so this refuses rather than publish a
chapter where any translated page lacks its Photoshop version -- a chapter
half from one route and half from the other would look finished and not be.

Pages are written as WebP by default. Measured over 24 pages of this series:
quality 95 is **14.6%** of the PNG size at PSNR >= 47.5 dB and SSIM >= 0.995,
and no difference is visible even in fine coloured hatching at 3x zoom.
Lossless WebP only reaches 42%; quality 90 reaches 10% at PSNR >= 44.7 dB.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from comic.paths import ChapterPaths
from comic.poster import write_poster
from comic.schemas import ChapterTranslation, TextKind
from comic.stages import analysis_for
from comic.state.store import ChapterStore, read_json

FORMATS = ("webp", "png")


@dataclass
class PublishReport:
    pages: int = 0
    from_photoshop: int = 0
    originals: int = 0
    held: int = 0
    before: int = 0
    after: int = 0


def save_page(source: Path, target: Path, fmt: str, quality: int) -> None:
    """Write one page in the library format, atomically."""
    from PIL import Image

    image = Image.open(source).convert("RGB")
    temp = target.with_suffix(target.suffix + ".tmp")
    if fmt == "webp":
        image.save(temp, "WEBP", quality=quality, method=6)
    else:
        image.save(temp, "PNG", optimize=True)
    temp.replace(target)


def library_page(paths: ChapterPaths, state, page: int) -> Path | None:
    """The published file for ``page``: the library numbers pages 001, 002...
    in reading order over every page that is not excluded (held ones too)."""
    index = 0
    for record in state.pages:
        if record.excluded:
            continue
        index += 1
        if record.page == page:
            found = sorted(f for f in paths.library.glob(f"{index:03d}.*")
                           if f.suffix.lower() in (".png", ".webp", ".jpg"))
            return found[0] if found else None
    return None


def publish_chapter(paths: ChapterPaths, fmt: str = "webp", quality: int = 95,
                    keep_published: bool = False) -> PublishReport:
    """``keep_published``: a page with no Photoshop build keeps the page already
    in the library (an old-route chapter that got only an SFX pass, the
    operator 2026-11-10) instead of refusing."""
    if fmt not in FORMATS:
        raise ValueError(f"--format must be one of: {', '.join(FORMATS)}")
    state = ChapterStore(paths).load()
    translated: set[str] = set()
    if paths.translation_file.exists():
        chapter = read_json(paths.translation_file, ChapterTranslation)
        translated = {e.region_id for e in chapter.entries if e.target_text.strip()}  # type: ignore[union-attr]
    ps_dir = paths.workspace / "photoshop"

    # Decide every page before touching the library. A held page has no
    # source: it only reserves its number.
    plan: list[tuple[Path | None, bool]] = []
    missing: list[int] = []
    for record in state.pages:
        if record.held:
            plan.append((None, False))
            continue
        if record.excluded:
            continue
        built = ps_dir / f"{record.page:03d}.png"
        if built.exists():
            plan.append((built, True))
            continue
        analysis = analysis_for(paths, record.page)
        # A page carrying another group's mark needs its build too: the
        # original would publish the mark (the operator, 2026-11-12).
        marked = analysis and any(r.erased_whole for r in analysis.regions)
        if analysis and ({r.id for r in analysis.regions} & translated or marked):
            kept = library_page(paths, state, record.page) if keep_published else None
            if kept is None:
                missing.append(record.page)
            else:
                plan.append((kept, False))
            continue
        plan.append((paths.source_pages / record.filename, False))
    if missing:
        raise ValueError(
            "no Photoshop build for translated page(s) "
            + ", ".join(str(p) for p in missing) + " -- run `comic ps build` first"
        )

    report = PublishReport()
    library = paths.library
    library.mkdir(parents=True, exist_ok=True)
    report.before = sum(f.stat().st_size for f in library.iterdir() if f.is_file())
    # Number the published pages 001, 002, ... in reading order: an excluded
    # page (another group's credits page, the operator 2026-10-09) must not
    # leave the chapter starting at 002. A held page keeps its number, and
    # whatever the operator put there stays.
    targets = []
    for index, (source, from_ps) in enumerate(plan, 1):
        if source is None:
            targets.extend(f for f in library.glob(f"{index:03d}.*") if f.is_file())
            report.held += 1
            continue
        target = library / f"{index:03d}.{fmt}"
        save_page(source, target, fmt, quality)
        targets.append(target)
        report.pages += 1
        report.from_photoshop += from_ps
        report.originals += not from_ps
    poster = write_poster(library, fmt, quality)
    if poster:
        targets.append(poster)
    for stale in library.iterdir():
        if stale.is_file() and stale not in targets and stale.suffix != ".tmp":
            stale.unlink()
    report.after = sum(f.stat().st_size for f in library.iterdir() if f.is_file())
    return report
