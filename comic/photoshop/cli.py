"""``comic ps ...`` -- the Photoshop route, separate from the pipeline.

It starts where the pipeline's render stage has already been (analysis,
reviewed OCR, translation, render plan, cleaned pages) and produces, per page,
a layered PSD with editable Vietnamese text plus a flattened PNG preview, in
``workspace/<series>/<chapter>/photoshop/``. It never writes to ``library/``
and never changes the pipeline's own artifacts.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from comic.console import console, setup_console
from comic.paths import ChapterPaths, SeriesPaths, project_root

ps_app = typer.Typer(
    name="ps",
    help="Photoshop route: layered PSDs with editable Vietnamese text (separate feature).",
    no_args_is_help=True,
)

CLEAN_MODES = ("pipeline", "hybrid", "content-aware")


def _series_fonts(series: str | None) -> list[Path]:
    """Font files the series' styles use (all of assets/fonts with no series)."""
    fonts_dir = project_root() / "assets" / "fonts"
    if not series:
        return sorted(fonts_dir.glob("*.ttf")) + sorted(fonts_dir.glob("*.otf"))
    from comic.typesetting.render import font_file, load_styles

    styles = load_styles(SeriesPaths(series).fonts_file)
    return sorted({font_file(fonts_dir, s.font) for s in styles.values()})


@ps_app.command("check")
def check() -> None:
    """Is Photoshop reachable, and which series fonts can it see?"""
    setup_console()
    from comic.photoshop.driver import PhotoshopError, connect, registered_executable, run
    from comic.photoshop.fonts import font_info, is_installed

    exe = registered_executable()
    if not exe:
        console.print("[fail]x[/fail] Photoshop is not registered for COM automation")
        raise typer.Exit(1)
    console.print(f"[ok]+[/ok] registered: [path]{escape(exe)}[/path]")
    try:
        app = connect()
        console.print(f"[ok]+[/ok] connected: Photoshop {app.Version}")
        run(app, "1")
    except PhotoshopError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    for path in _series_fonts(None):
        info = font_info(path)
        state = "[ok]installed[/ok]" if is_installed(info) else "[warn]not installed[/warn]"
        console.print(f"  {info.postscript:28} {state}")


@ps_app.command("fonts")
def fonts(
    series: str = typer.Option(None, "--series", help="Only the fonts this series uses."),
) -> None:
    """Install the project's fonts for the current user so Photoshop can use them.

    Per-user, no administrator rights, nothing system-wide. Restart Photoshop
    afterwards: it reads the font list only at startup.
    """
    setup_console()
    from comic.photoshop.fonts import font_info, install_for_user

    added = 0
    for path in _series_fonts(series):
        info = font_info(path)
        if install_for_user(info):
            added += 1
            console.print(f"[ok]+[/ok] installed {escape(info.postscript)}")
        else:
            console.print(f"  already installed: {escape(info.postscript)}")
    if added:
        console.print("\n[warn]![/warn] Restart Photoshop so it picks up the new fonts.")


@ps_app.command("sfx")
def sfx(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
) -> None:
    """Crop every SFX the review dropped, for the agent to decide which to replace."""
    setup_console()
    from comic.photoshop.sfx import open_request, response_path

    paths = ChapterPaths(series, chapter)
    request, count = open_request(paths, list(page or []) or None)
    console.print(f"[gate]GATE: sfx[/gate] -- {count} dropped region(s) to look at")
    console.print(f"  read   [path]{escape(str(request))}[/path]")
    console.print(f"  crops  [path]{escape(str(paths.workspace / 'photoshop' / 'sfx'))}[/path]")
    console.print(f"  write  [path]{escape(str(response_path(paths)))}[/path]")
    console.print('         {"gate": "sfx", "items": [{"region_id": "p007_r02", "text": "GIẬT MÌNH"}]}')
    console.print("  then   [bold]comic ps build[/bold] -- only the items listed are replaced")


@ps_app.command("publish")
def publish(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    fmt: str = typer.Option("webp", "--format", help="webp (default) or png."),
    quality: int = typer.Option(95, "--quality", help="WebP quality; 95 is visually lossless."),
) -> None:
    """Replace a chapter's library pages with the Photoshop versions.

    Every page of the chapter is written, so the library never mixes routes:
    the Photoshop PNG where one exists, otherwise the original (a page with
    nothing translated). The previous file for each page is removed, so a
    page never exists in two formats.
    """
    setup_console()
    from comic.photoshop.publish import publish_chapter

    paths = ChapterPaths(series, chapter)
    try:
        report = publish_chapter(paths, fmt, quality)
    except ValueError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    console.print(
        f"[ok]+[/ok] {report.pages} page(s) -> [path]{escape(str(paths.library))}[/path]\n"
        f"  {report.from_photoshop} from Photoshop, {report.originals} original(s); "
        f"{report.before / 1e6:.1f} MB -> {report.after / 1e6:.1f} MB"
    )


@ps_app.command("build")
def build(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
    clean: str = typer.Option(
        "pipeline", "--clean",
        help="pipeline: the pipeline's cleaned page; hybrid: plus Photoshop "
             "Content-Aware Fill over artwork; content-aware: Content-Aware Fill "
             "for every region, from the original.",
    ),
) -> None:
    """Build layered PSDs with editable Vietnamese text for a rendered chapter."""
    setup_console()
    from comic.photoshop.driver import PhotoshopError, build_page, connect, missing_fonts
    from comic.photoshop.plan import plan_chapter

    if clean not in CLEAN_MODES:
        console.print(f"[fail]x[/fail] --clean must be one of: {', '.join(CLEAN_MODES)}")
        raise typer.Exit(1)

    paths = ChapterPaths(series, chapter)
    fonts_dir = project_root() / "assets" / "fonts"
    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)} -- Photoshop[/stage]")
    plans, notes = plan_chapter(paths, list(page or []) or None, fonts_dir, clean)
    for note in notes:
        console.print(f"  [warn]![/warn] {escape(note)}")
    if not plans:
        console.print("[fail]x[/fail] nothing to build")
        raise typer.Exit(1)

    try:
        app = connect()
        wanted = {layer.font for p in plans for layer in p.layers}
        wanted |= {s["font"] for p in plans for s in p.sfx}
        missing = missing_fonts(app, wanted)
        if missing:
            console.print(
                "[fail]x[/fail] Photoshop cannot see: " + escape(", ".join(missing))
                + "\n  run [bold]comic ps fonts[/bold], then restart Photoshop"
            )
            raise typer.Exit(1)
        for p in plans:
            seconds = build_page(app, p)
            extra = f", {len(p.sfx)} SFX" if p.sfx else ""
            console.print(f"  page {p.page:03d}: {len(p.layers)} text layer(s){extra}  {seconds:.1f}s")
    except PhotoshopError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    console.print(f"\n[ok]+[/ok] {len(plans)} page(s) -> [path]{escape(str(paths.workspace / 'photoshop'))}[/path]")


@ps_app.command("art")
def art(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
) -> None:
    """Export the text-free art (NNN_art.png) from already-built PSDs.

    `comic ps build` saves it with every page now; this covers chapters built
    before that. The art is what `comic video` uses -- never the previews.
    """
    setup_console()
    from comic.photoshop.driver import PhotoshopError, connect, export_art

    paths = ChapterPaths(series, chapter)
    out_dir = paths.workspace / "photoshop"
    psds = sorted(out_dir.glob("[0-9][0-9][0-9].psd"))
    if page:
        psds = [p for p in psds if int(p.stem) in set(page)]
    if not psds:
        console.print("[fail]x[/fail] no PSDs -- run [bold]comic ps build[/bold] first")
        raise typer.Exit(1)
    try:
        app = connect()
        for psd in psds:
            export_art(app, psd)
    except PhotoshopError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    console.print(f"[ok]+[/ok] {len(psds)} page(s) of art -> [path]{escape(str(out_dir))}[/path]")
