"""``comic`` command line entry point."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from comic import __version__
from comic.console import console, setup_console
from comic.doctor import Level, run_checks
from comic.downloader.adapters import adapter_for, describe_adapters, load_adapters
from comic.paths import ChapterPaths, SeriesPaths, browser_profile_dir, chapter_sort_key
from comic.schemas import STAGE_ORDER, ChapterStatus, Gate, Stage, StageStatus
from comic.state.store import ChapterStore, StateError, next_stage

app = typer.Typer(
    name="comic",
    help="Agent-driven comic acquisition, translation and localization.",
    no_args_is_help=True,
    add_completion=False,
)

from comic.photoshop.cli import ps_app  # noqa: E402 - separate Photoshop route

app.add_typer(ps_app, name="ps")

from comic.video.cli import video_app  # noqa: E402 - separate video add-on

app.add_typer(video_app, name="video")

_STYLE = {Level.OK: "ok", Level.WARN: "warn", Level.FAIL: "fail"}
_GLYPH = {Level.OK: "+", Level.WARN: "!", Level.FAIL: "x"}

_STAGE_GLYPH = {
    StageStatus.DONE: ("ok", "+"),
    StageStatus.RUNNING: ("gate", ">"),
    StageStatus.FAILED: ("fail", "x"),
    StageStatus.SKIPPED: ("path", "-"),
    StageStatus.PENDING: ("path", "."),
}

#: Exit code the gate protocol uses to mean "stopped, waiting for the agent".
GATE_EXIT_CODE = 10


@app.command()
def doctor(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show passing checks as well as problems."
    ),
) -> None:
    """Probe the environment and report what is missing."""
    setup_console()
    report = run_checks()

    shown = report.checks if verbose else [
        c for c in report.checks if c.level is not Level.OK
    ]

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("", width=1)
    table.add_column("check", style="bold")
    table.add_column("detail", overflow="fold")

    for check in shown:
        table.add_row(
            f"[{_STYLE[check.level]}]{_GLYPH[check.level]}[/{_STYLE[check.level]}]",
            escape(check.name),
            escape(check.detail),
        )
    if shown:
        console.print(table)
    console.print()

    fixes: list[str] = []
    for check in report.checks:
        if check.level is not Level.OK and check.fix and check.fix not in fixes:
            fixes.append(check.fix)
    if fixes:
        console.print("[bold]To fix:[/bold]")
        for fix in fixes:
            console.print(f"    {escape(fix)}")
        console.print()

    n_ok = sum(1 for c in report.checks if c.level is Level.OK)
    summary = (
        f"{n_ok} ok, {len(report.warnings)} warning(s), {len(report.failures)} blocking"
    )
    if report.ready:
        console.print(f"[ok]Ready.[/ok] {summary}")
    else:
        console.print(f"[fail]Not ready.[/fail] {summary}")
        raise typer.Exit(1)


@app.command()
def sites() -> None:
    """List the site adapters currently installed."""
    setup_console()
    rows = describe_adapters()
    if not rows:
        console.print("[warn]no adapters found[/warn]")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("host", style="bold")
    table.add_column("behaviour")
    for host, summary in rows:
        table.add_row(escape(host), escape(summary))
    console.print(table)
    console.print(
        f"\n[path]{len(rows)} adapter(s). Unknown hosts fall back to generic mode.[/path]"
    )


@app.command()
def login(
    site: str = typer.Option(..., "--site", "-s", help="Host to sign in to, e.g. manga.bilibili.com"),
) -> None:
    """Open a visible browser so you can sign in once.

    The session is saved to the persistent profile, so later runs are headless
    and already authenticated. Nothing is read from your everyday browser.
    """
    setup_console()
    from comic.browser.session import BrowserError, BrowserSession

    matched = next((a for a in load_adapters() if a.match == site), None)
    url = (matched.login_url if matched and matched.login_url else f"https://{site}")

    console.print(f"[gate]Opening[/gate] {escape(url)}")
    console.print(
        "Sign in in the window that appears, then [bold]close the window[/bold] "
        "to save the session."
    )
    console.print(f"[path]profile: {browser_profile_dir()}[/path]\n")

    try:
        with BrowserSession(headless=False) as session:
            session.login(url)
    except BrowserError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    console.print("[ok]+[/ok] Session saved. Future runs will reuse it.")


@app.command()
def chrome(
    port: int = typer.Option(9222, "--port", help="Remote debugging port."),
    edge: bool = typer.Option(False, "--edge", help="Use Microsoft Edge (own profile) instead of Chrome."),
) -> None:
    """Start your own Chrome (or Edge) with remote debugging, for challenge-gated sites.

    Some hosts serve an anti-bot interstitial to any browser that looks
    automated. This tool does not try to defeat that check. Instead, sign in and
    browse normally in the window this opens -- once the site has cleared it,
    'comic download --chrome' can read pages through that same browser. Edge
    speaks the same debugging protocol; give it its own --port if Chrome is
    already running, and pass that port to 'comic download'.

    The profile is dedicated (Chrome refuses remote debugging on the default
    one), so sign in there once; it persists.
    """
    setup_console()
    from comic.browser import chrome as ch

    name = "Edge" if edge else "Chrome"
    existing = ch.debug_target(port)
    if existing:
        console.print(f"[ok]+[/ok] Already running: {escape(existing.get('Browser', name))}")
        console.print(f"[path]  {ch.cdp_url(port)}[/path]")
        return

    binary = ch.find_edge() if edge else ch.find_chrome()
    if binary is None:
        console.print(f"[fail]x[/fail] No {name} binary found.")
        console.print(f"    Start it yourself with:\n    {escape(ch.manual_command(port, edge=edge))}")
        raise typer.Exit(1)

    console.print(f"[gate]Launching[/gate] {escape(str(binary))}")
    ch.launch(port, edge=edge)

    import time
    for _ in range(20):
        time.sleep(0.5)
        if ch.debug_target(port):
            break
    target = ch.debug_target(port)
    if not target:
        console.print(f"[fail]x[/fail] {name} did not expose a debugging endpoint.")
        console.print(f"    Try manually:\n    {escape(ch.manual_command(port, edge=edge))}")
        raise typer.Exit(1)

    profile = ch.edge_profile_dir() if edge else ch.chrome_profile_dir()
    console.print(f"[ok]+[/ok] {escape(target.get('Browser', name))} on {ch.cdp_url(port)}")
    console.print(f"[path]  profile: {profile}[/path]\n")
    console.print("Now, in that window: visit the site, sign in, and clear any")
    console.print("'checking your browser' prompt. Then run your download with")
    console.print(f"[bold]--chrome --port {port}[/bold]. Leave the window open while it runs.")


@app.command()
def download(
    url: str = typer.Argument(..., help="Chapter URL."),
    series: str = typer.Option(..., "--series", help="Series name (the persistent workspace)."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="Override the detected chapter number."),
    headful: bool = typer.Option(False, "--headful", help="Show the browser window."),
    use_chrome: bool = typer.Option(
        False, "--chrome",
        help="Read through your own running Chrome (start it with 'comic chrome').",
    ),
    port: int = typer.Option(9222, "--port", help="Chrome remote debugging port."),
) -> None:
    """Acquire one chapter's pages into the workspace."""
    setup_console()
    from comic.browser.session import BotChallengeError, BrowserError
    from comic.downloader.importer import (
        ChapterLockedError,
        ImportError_,
        download_chapter,
        verify_pages,
    )

    adapter = adapter_for(url)
    detected = chapter or adapter.chapter_number(url)
    if not detected:
        console.print(
            f"[fail]x[/fail] Could not work out the chapter number from that URL "
            f"(site: {escape(adapter.name)})."
        )
        console.print("    Pass it explicitly, e.g. [bold]--chapter 46[/bold]")
        raise typer.Exit(1)

    paths = ChapterPaths(series, detected)
    paths.ensure()
    store = ChapterStore(paths)
    state = store.load_or_create(
        source_url=url,
        source_site=adapter.match,
        source_language=adapter.source_language,
        page_kind=adapter.page_kind,
        reading_direction=adapter.reading_direction,
    )

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    console.print(f"[path]{escape(adapter.name)} | {adapter.page_kind.value} | "
                  f"{adapter.reading_direction.value} | {adapter.source_language}[/path]")
    if adapter.requires_login:
        console.print(f"[warn]![/warn] {escape(adapter.match)} needs a signed-in session "
                      f"(comic login --site {escape(adapter.match)})")

    record = state.stage(Stage.DOWNLOAD)
    record.status = StageStatus.RUNNING
    state.status = ChapterStatus.RUNNING
    store.save(state)

    cdp = None
    if use_chrome:
        from comic.browser import chrome as ch

        if not ch.debug_target(port):
            console.print(
                f"\n[fail]x[/fail] No Chrome listening on port {port}. "
                f"Run [bold]comic chrome[/bold] first."
            )
            raise typer.Exit(1)
        cdp = ch.cdp_url(port)
        console.print(f"[path]reading through your Chrome at {cdp}[/path]")

    try:
        report = download_chapter(
            url, paths, adapter=adapter, headless=not headful, cdp_url=cdp
        )
    except ChapterLockedError as exc:
        record.status = StageStatus.FAILED
        record.error = str(exc)
        state.status = ChapterStatus.FAILED
        store.save(state)
        console.print(f"\n[fail]x Locked.[/fail] {escape(str(exc))}")
        raise typer.Exit(2)
    except BotChallengeError as exc:
        record.status = StageStatus.FAILED
        record.error = str(exc)
        state.status = ChapterStatus.FAILED
        store.save(state)
        console.print(f"\n[fail]x Blocked.[/fail] {escape(str(exc))}")
        console.print(
            "\n  This host serves an anti-bot interstitial to automated browsers.\n"
            "  Defeating that check is circumventing an access control, so this\n"
            "  tool does not attempt it. The supported route is to drive a browser\n"
            "  the site has already cleared:\n\n"
            "      [bold]comic download <url> --series <name> --chrome[/bold]\n\n"
            "  See 'comic download --help' for how to start Chrome for that."
        )
        raise typer.Exit(3)
    except (ImportError_, BrowserError) as exc:
        record.status = StageStatus.FAILED
        record.error = str(exc)
        state.status = ChapterStatus.FAILED
        store.save(state)
        console.print(f"\n[fail]x Import failed.[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    problems = verify_pages(paths, report.pages)
    for warning in report.warnings:
        console.print(f"[warn]![/warn] {escape(warning)}")
    for skipped in report.skipped:
        console.print(f"[path]  skipped {escape(skipped)}[/path]")
    for problem in problems:
        console.print(f"[fail]x[/fail] {escape(problem)}")

    state.pages = report.pages
    state.page_count = len(report.pages)
    for page in state.pages:
        page.stages[Stage.DOWNLOAD] = StageStatus.DONE
    record.status = StageStatus.DONE if not problems else StageStatus.FAILED
    record.summary = f"{len(report.pages)} pages"
    state.warnings.extend(report.warnings)
    state.status = ChapterStatus.PARTIAL
    store.save(state)

    console.print(
        f"\n[ok]+[/ok] {len(report.pages)} pages -> [path]{escape(str(paths.original))}[/path]"
    )
    if report.selection and report.selection.rejected:
        summary = ", ".join(f"{k}: {v}" for k, v in report.selection.reasons().items())
        console.print(f"[path]  rejected {len(report.selection.rejected)} image(s) -- {escape(summary)}[/path]")

    # An unknown host got the generic defaults, two of which are usually wrong
    # for a new source. Leave a starter file saying what the images actually
    # look like, so the operator has something to correct rather than a silent
    # misconfiguration to discover at the translate gate.
    from comic.downloader.adapters import suggest_adapter

    written = suggest_adapter(url, [paths.original / p.filename for p in report.pages])
    if written:
        console.print(
            f"\n[warn]![/warn] No adapter for this host; wrote a starter one:\n"
            f"  [path]{escape(str(written))}[/path]\n"
            f"  Check [bold]source_language[/bold] and [bold]reading_direction[/bold] "
            f"before running [bold]comic analyze[/bold]."
        )


@app.command()
def capture(
    url: str = typer.Argument(..., help="Chapter URL."),
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="Chapter number."),
    selector: str = typer.Option(
        None, "--selector", help="CSS selector for the panels. Auto-detected if omitted."
    ),
    use_chrome: bool = typer.Option(
        True, "--chrome/--own-browser",
        help="Capture through your running Chrome (default) or a fresh browser.",
    ),
    port: int = typer.Option(9222, "--port", help="Chrome remote debugging port."),
    start_at: int = typer.Option(
        1, "--start-at", help="First page number to write, to resume a run."
    ),
    scale: float = typer.Option(
        2.0, "--scale",
        help="Rasterise at this multiple of CSS size. Readers lay out below the "
             "artwork's native resolution; 1x loses detail OCR needs.",
    ),
) -> None:
    """Acquire pages by screenshotting rendered panels.

    For readers that paint pages rather than serving them as images. This
    photographs what the browser has already drawn; it does not read pixels out
    of a canvas or touch any decoding the page performs.

    One rendered element becomes one page, so nothing has to be stitched.
    """
    setup_console()
    from comic.browser.capture import (
        CaptureError,
        capture_chapter,
        capture_paged,
    )
    from comic.browser.session import BrowserError, BrowserSession

    adapter = adapter_for(url)
    detected = chapter or adapter.chapter_number(url)
    if not detected:
        console.print("[fail]x[/fail] Could not detect the chapter number; pass --chapter.")
        raise typer.Exit(1)

    cdp = None
    if use_chrome:
        from comic.browser import chrome as ch

        if not ch.debug_target(port):
            console.print(f"[fail]x[/fail] No Chrome on port {port}. "
                          f"Run [bold]comic chrome[/bold] first.")
            raise typer.Exit(1)
        cdp = ch.cdp_url(port)

    paths = ChapterPaths(series, detected)
    paths.ensure()
    store = ChapterStore(paths)
    state = store.load_or_create(
        source_url=url, source_site=adapter.match,
        source_language=adapter.source_language,
        page_kind=adapter.page_kind, reading_direction=adapter.reading_direction,
    )

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    console.print("[path]screen capture -- one rendered element per page[/path]")
    if cdp:
        console.print(f"[path]through your Chrome at {cdp}[/path]")
    console.print("[warn]![/warn] Leave the window alone while this runs.\n")

    try:
        with BrowserSession(headless=False, cdp_url=cdp) as session:
            # A paged reader is walked by its own slider; a long strip is
            # scrolled. Try the paged path and fall back, rather than opening a
            # second page just to decide.
            try:
                report = capture_paged(url, paths, session, selector=selector,
                                       start_at=start_at)
            except CaptureError as exc:
                if "no page slider" not in str(exc):
                    raise
                console.print("[path]no slider; treating as a long strip[/path]")
                report = capture_chapter(url, paths, session,
                                         selector=selector, scale=scale)
    except (CaptureError, BrowserError) as exc:
        state.stage(Stage.DOWNLOAD).status = StageStatus.FAILED
        state.stage(Stage.DOWNLOAD).error = str(exc)
        state.status = ChapterStatus.FAILED
        store.save(state)
        console.print(f"\n[fail]x Capture failed.[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    state.pages = report.pages
    state.page_count = len(report.pages)
    for page in state.pages:
        page.stages[Stage.DOWNLOAD] = StageStatus.DONE
    record = state.stage(Stage.DOWNLOAD)
    record.status = StageStatus.DONE
    record.summary = f"{len(report.pages)} pages (screen capture)"
    state.status = ChapterStatus.PARTIAL
    store.save(state)

    for skip in report.skipped:
        console.print(f"[path]  skipped {escape(skip)}[/path]")
    console.print(f"\n[ok]+[/ok] {report.count} pages via [bold]{escape(report.selector)}[/bold]"
                  f" -> [path]{escape(str(paths.original))}[/path]")
    if report.title:
        console.print(f"[path]  page title: {escape(report.title[:70])}[/path]")


@app.command("import-raw")
def import_raw(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    source: str = typer.Option(
        None, "--from", help="Folder of saved images. Default: raw/<series>/<chapter>/"
    ),
    order_by: str = typer.Option(
        "name", "--order-by", help="How to order the files: name | mtime | ctime."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    language: str = typer.Option(
        None, "--language", help="Source language: ja | zh | ko | en."
    ),
    kind: str = typer.Option(
        None, "--kind", help="Page shape: paged | longstrip (webtoon)."
    ),
    direction: str = typer.Option(
        None, "--direction", help="Reading order: rtl | ltr | ttb."
    ),
    normalize: bool = typer.Option(
        False, "--normalize/--no-normalize",
        help="Restore full contrast on pages captured through a dimmed browser. "
             "Off by default: the workspace copy is meant to be verbatim.",
    ),
) -> None:
    """Ingest manually-saved pages from a folder.

    The escape hatch for hosts that cannot be read automatically: save the pages
    yourself, drop them in the folder, and the rest of the pipeline is identical.

    Ordering is shown for confirmation first, because manually-saved filenames
    are unreliable and a scrambled chapter is expensive to notice later.
    """
    setup_console()
    from comic.downloader.raw_ingest import (
        OrderBy,
        RawIngestError,
        extension_dir,
        find_source,
        ingest,
        peak_luminance,
        raw_dir,
        scan,
    )

    try:
        ordering = OrderBy(order_by)
    except ValueError:
        console.print(f"[fail]x[/fail] --order-by must be one of: "
                      f"{', '.join(o.value for o in OrderBy)}")
        raise typer.Exit(1)

    paths = ChapterPaths(series, chapter)
    if source:
        folder = Path(source).resolve()
    else:
        folder = (find_source(series, paths.chapter, given=chapter)
                  or raw_dir(series, paths.chapter))

    try:
        plan = scan(folder, ordering)
    except RawIngestError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        if not folder.exists():
            console.print(f"\n  Create it and drop your saved pages there:\n"
                          f"  [bold]{escape(str(folder))}[/bold]")
        raise typer.Exit(1)

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    console.print(f"[path]from {escape(str(folder))} -- ordered by {ordering.value}[/path]\n")

    table = Table(box=None, pad_edge=False)
    table.add_column("becomes", style="bold")
    table.add_column("source file")
    table.add_column("size", justify="right")
    mapping = plan.mapping()
    preview = mapping if len(mapping) <= 24 else mapping[:12] + mapping[-12:]
    for index, (src, dest) in enumerate(preview):
        if len(mapping) > 24 and index == 12:
            table.add_row("...", f"({len(mapping) - 24} more)", "")
        candidate = next(c for c in plan.candidates if c.path == src)
        table.add_row(dest, escape(src.name), f"{candidate.width}x{candidate.height}")
    console.print(table)

    for path, why in plan.rejected:
        console.print(f"[path]  skipped {escape(path.name)} -- {escape(why)}[/path]")

    console.print(f"\n[bold]{plan.count}[/bold] page(s) will be imported.")
    if not yes:
        console.print(
            "[warn]![/warn] Check the order above. If it is wrong, re-run with "
            "[bold]--order-by mtime[/bold] or rename the files."
        )
        if not typer.confirm("Import with this order?", default=True):
            console.print("[path]nothing written[/path]")
            raise typer.Exit(0)

    paths.ensure()
    store = ChapterStore(paths)
    state = store.load_or_create(source_url=folder.as_uri(), source_site="raw")

    # A manual import has no adapter to inherit from, so the defaults are
    # Japanese, paged and right-to-left. That is wrong for a Korean webtoon in
    # all three respects, and nothing downstream can infer it from the files:
    # OCR would be handed the wrong engine and the strip would never be re-cut.
    from comic.schemas import PageKind, ReadingDirection

    for value, field, enum in (
        (kind, "page_kind", PageKind),
        (direction, "reading_direction", ReadingDirection),
    ):
        if not value:
            continue
        try:
            setattr(state, field, enum(value.lower()))
        except ValueError:
            console.print(f"[fail]x[/fail] --{field.split('_')[0]} must be one of: "
                          f"{', '.join(e.value for e in enum)}")
            raise typer.Exit(1)
    if language:
        state.source_language = language.lower()
    if kind and state.page_kind is PageKind.LONGSTRIP and not direction:
        # A strip is read downwards; nothing else makes sense.
        state.reading_direction = ReadingDirection.TTB

    records = ingest(plan, paths, normalize=normalize)

    state.pages = records
    state.page_count = len(records)
    for page in state.pages:
        page.stages[Stage.DOWNLOAD] = StageStatus.DONE
    record = state.stage(Stage.DOWNLOAD)
    record.status = StageStatus.DONE
    record.summary = f"{len(records)} pages (manual import)"
    state.status = ChapterStatus.PARTIAL
    store.save(state)

    console.print(f"\n[ok]+[/ok] {len(records)} pages -> "
                  f"[path]{escape(str(paths.original))}[/path]")
    if len(records) < plan.count:
        console.print(f"[path]  {plan.count - len(records)} duplicate(s) collapsed[/path]")


@app.command()
def slice(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    force: bool = typer.Option(
        False, "--force", help="Re-cut even if the source is already page-shaped."
    ),
) -> None:
    """Re-cut a long strip into pages, and report where the seams landed.

    ``comic analyze`` does this itself. Running it alone is for looking first:
    detection costs minutes, and a chapter whose seams were all forced is worth
    knowing about before paying that.
    """
    setup_console()
    from comic.stages import run_slice

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    report = run_slice(paths, state, force=force)
    if report is None:
        console.print(
            f"[path]nothing to cut -- page_kind is "
            f"{state.page_kind.value} and the joins are already page ends[/path]"
        )
        return

    store.save(state)
    for note in report.notes:
        console.print(f"[warn]![/warn] {escape(note)}")
    console.print(
        f"[ok]+[/ok] {report.total_height}px strip -> {report.page_count} page(s)"
    )
    if report.gutters:
        shades = ", ".join(f"{g:.0f}" for g in report.gutters)
        console.print(f"[path]  gutter colour(s) measured at {shades}[/path]")
    for index, (span, seam) in enumerate(zip(report.spans, report.seams), start=1):
        where = f"{seam.band}px flat band" if seam.safe else "[warn]FORCED[/warn]"
        console.print(
            f"  {index:03d}.png  {span[1] - span[0]:>5}px   seam at "
            f"y={seam.y:<6} {where}"
        )
    console.print(f"  {report.page_count:03d}.png  "
                  f"{report.spans[-1][1] - report.spans[-1][0]:>5}px   (end)")
    console.print(f"[path]  {escape(str(paths.pages))}[/path]")


@app.command()
def exclude(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(..., "--page", "-p", help="Page number(s) to drop."),
    restore: bool = typer.Option(
        False, "--restore", help="Put the page back into the chapter."
    ),
) -> None:
    """Drop a page that is not part of the comic.

    For an uploader's insert -- a scanlation group's credits card, a "read it
    on <host>" notice, an ad. Those are not the work, and publishing one puts
    somebody else's banner in the library as though it were a page of story.

    This is *not* the same as a page with no lettering. A cover or a wordless
    splash is the comic, and it is published untranslated so the page numbers
    keep matching the source; answering "" for its regions at `ocr_review` is
    how you handle that one.

    The page stays in `original/` either way -- that remains the verbatim
    archive of what the source served. This only decides what reaches
    `library/`, and it also keeps the page out of the gates, the watermark
    check and the declared-language check.
    """
    setup_console()

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    wanted = set(page)
    known = {record.page for record in state.pages}
    missing = sorted(wanted - known)
    if missing:
        console.print(
            f"[fail]x[/fail] no such page(s): {', '.join(str(n) for n in missing)}"
        )
        console.print(f"    this chapter has pages 1-{max(known) if known else 0}")
        raise typer.Exit(1)

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    changed = 0
    for record in state.pages:
        if record.page not in wanted:
            continue
        if record.excluded == (not restore):
            console.print(
                f"[path]  page {record.page} ({escape(record.filename)}) "
                f"already {'excluded' if record.excluded else 'included'}[/path]"
            )
            continue
        record.excluded = not restore
        changed += 1
        verb = "restored" if restore else "excluded"
        console.print(f"[ok]+[/ok] page {record.page} ({escape(record.filename)}) {verb}")

    if changed:
        store.save(state)
    live = sum(1 for record in state.pages if not record.excluded)
    console.print(
        f"[path]  {live} of {len(state.pages)} page(s) are part of the comic[/path]"
    )
    if not restore and changed:
        console.print(
            "[path]  still in original/; it will not be published or sent to a gate[/path]"
        )


@app.command()
def analyze(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
    force: bool = typer.Option(False, "--force", help="Redo work already done."),
    language: str = typer.Option(None, "--language", help="Override the source language."),
) -> None:
    """Detect text and read it, stopping at the review gate if anything looks wrong."""
    setup_console()
    from comic.ocr.registry import OcrError
    from comic.stages import (
        open_ocr_review,
        run_detect,
        run_ocr,
        run_slice,
        write_page_overlays,
    )

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    if language:
        state.source_language = language
    pages = list(page) if page else None

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    console.print(f"[path]{state.page_count} pages | {state.source_language} -> "
                  f"{state.target_language} | {state.reading_direction.value}[/path]\n")

    # A webtoon arrives as tall slices whose boundaries fall wherever the
    # uploader's export step landed, so pages have to be re-cut before anything
    # is detected on them. A paged source returns immediately.
    #
    # ``--force`` is deliberately *not* passed on. Here it means "redo the
    # detection and OCR", and re-cutting is a different, destructive thing: it
    # renumbers every page and resets every per-page stage. So `analyze -p 12
    # --force`, meaning "redo page 12", would otherwise discard the other 54.
    # Re-cutting has its own opt-in: `comic slice --force`.
    report = run_slice(paths, state)
    if report is not None and report.spans:
        if pages:
            console.print("  [warn]![/warn] --page ignored: the strip was re-cut, "
                          "so the old page numbers name files that no longer exist")
        pages = None
        store.save(state)
        console.print(
            f"[stage]slice[/stage] {report.total_height}px strip -> "
            f"{report.page_count} page(s), {len(report.seams) - report.forced}"
            f"/{len(report.seams)} seam(s) on a flat band"
        )
        for note in report.notes:
            console.print(f"  [warn]![/warn] {escape(note)}")
        console.print()

    console.print("[stage]detect[/stage]")
    detected = run_detect(
        paths, state, only=pages, force=force,
        progress=lambda pg, n: console.print(f"  page {pg:03d}: {n} regions"),
    )
    store.save(state)
    for note in detected.notes:
        console.print(f"  [warn]![/warn] {escape(note)}")

    console.print(f"\n[stage]ocr[/stage] ({state.source_language})")
    try:
        read, suspicious = run_ocr(
            paths, state, only=pages, force=force,
            progress=lambda pg, n, bad: console.print(
                f"  page {pg:03d}: {n} regions, {bad} need checking"),
        )
    except OcrError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    store.save(state)
    for note in read.notes:
        console.print(f"  [warn]![/warn] {escape(note)}")

    done_pages = [p.page for p in state.pages
                  if p.status_of(Stage.DETECT) is StageStatus.DONE]
    overlays = write_page_overlays(paths, pages or done_pages)

    console.print(
        f"\n[ok]+[/ok] {detected.pages_done} page(s) detected, "
        f"{read.regions} region(s) read"
    )
    if overlays:
        console.print(f"[path]  overlays: {escape(str(overlays[0].parent))}[/path]")

    if not suspicious:
        console.print("[ok]+[/ok] every reading looks sound; no review needed.")
        return

    request = open_ocr_review(paths, state, suspicious)
    from comic.state.store import open_gate
    open_gate(state, Stage.OCR, Gate.OCR_REVIEW)
    store.save(state)

    console.print(
        f"\n[gate]GATE: ocr_review[/gate] -- {len(suspicious)} reading(s) need a human eye."
    )
    console.print(f"  read   [path]{escape(str(request))}[/path]")
    console.print(f"  crops  [path]{escape(str(paths.gate_dir('ocr_review')))}[/path]")
    console.print(f"  write  [path]{escape(str(paths.gate_response('ocr_review')))}[/path]")
    console.print(f"  then   [bold]comic resume --series \"{escape(series)}\" "
                  f"--chapter {escape(str(chapter))}[/bold]")
    raise typer.Exit(GATE_EXIT_CODE)


@app.command()
def review(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
    region: list[str] = typer.Option(
        None, "--region", "-r", help="Specific region ids, e.g. p010_r12."
    ),
    all_readings: bool = typer.Option(
        False, "--all", help="Include readings already checked by an agent."
    ),
) -> None:
    """Reopen the OCR review gate for readings the heuristics did not flag.

    Confidence and mask coverage catch the readings that are obviously wrong.
    They cannot catch a reading that is complete, confident and still scrambled,
    which is what fragmented low-resolution text produces. This is the way to
    look anyway, without re-running OCR and discarding corrections.
    """
    setup_console()
    from comic.stages import analysis_for, open_ocr_review

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    wanted = set(region or [])
    pages = set(page or [])
    suspicious = []
    for record in state.pages:
        if pages and record.page not in pages:
            continue
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        for text_region in analysis.regions:
            if not text_region.translatable:
                continue
            if wanted:
                if text_region.id in wanted:
                    suspicious.append((analysis, text_region))
                continue
            # Regions the engine read as nothing are included on purpose:
            # they are where text hides. One of them held a whole line of
            # dialogue that was only caught by eye on the rendered page.
            if text_region.ocr_reviewed and not all_readings:
                continue
            suspicious.append((analysis, text_region))

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    missing = wanted - {r.id for _a, r in suspicious}
    for region_id in sorted(missing):
        console.print(f"  [warn]![/warn] no such region: {escape(region_id)}")
    if not suspicious:
        console.print("[warn]![/warn] nothing to review.")
        raise typer.Exit(1)

    request = open_ocr_review(paths, state, suspicious)
    from comic.state.store import open_gate

    open_gate(state, Stage.OCR, Gate.OCR_REVIEW)
    store.save(state)

    console.print(
        f"[gate]GATE: ocr_review[/gate] -- {len(suspicious)} reading(s) to check."
    )
    console.print(f"  read   [path]{escape(str(request))}[/path]")
    console.print(f"  crops  [path]{escape(str(paths.gate_dir('ocr_review')))}[/path]")
    console.print(f"  write  [path]{escape(str(paths.gate_response('ocr_review')))}[/path]")
    console.print(f"  then   [bold]comic resume --series \"{escape(series)}\" "
                  f"--chapter {escape(str(chapter))}[/bold]")
    raise typer.Exit(GATE_EXIT_CODE)


def _open_render_gate(paths, store, state, outcome, plan, series, chapter, error=None,
                      unchanged=None):
    """Write the visual_qa gate and print what the agent must read.

    ``unchanged``: pages left out of this round because no fix touched them
    (see ``open_visual_qa``).
    """
    from comic.qa.visual import open_visual_qa, save_plan
    from comic.state.store import open_gate

    # Pages this render produced no longer wait for one.
    plan.rerender_pages = [p for p in plan.rerender_pages if p not in outcome.pages]
    save_plan(paths, plan)
    request = open_visual_qa(paths, state, outcome, plan, previous_error=error,
                             unchanged=unchanged)
    open_gate(state, Stage.QA, Gate.VISUAL_QA)
    store.save(state)

    errors = [c for c in outcome.checks if c.severity == "error"]
    warnings = [c for c in outcome.checks if c.severity == "warning"]
    off_centre = sum(1 for c in outcome.checks if c.code == "off_centre")
    spills = sum(1 for c in outcome.checks if c.code == "spill")
    console.print(
        f"\n[gate]GATE: visual_qa[/gate] -- round {plan.round}, "
        f"{len(outcome.pages)} page(s) rendered, "
        f"{len(errors)} error(s), {len(warnings)} warning(s) "
        f"({off_centre} off-centre, {spills} spill)."
    )
    shown = set(outcome.pages)
    skipped = sorted(set(unchanged or []) - shown)
    if skipped:
        console.print(
            f"  [path]{len(skipped)} page(s) unchanged since they were approved, "
            "not shown again[/path]"
        )
    for check in errors[:8]:
        console.print(f"  [fail]x[/fail] {escape(check.region_id)}: {escape(check.detail)}")
    console.print(f"  read   [path]{escape(str(request))}[/path]")
    console.print(f"  images [path]{escape(str(paths.gate_dir('visual_qa')))}[/path]")
    console.print(f"  write  [path]{escape(str(paths.gate_response('visual_qa')))}[/path]")
    console.print(f"  then   [bold]comic resume --series \"{escape(series)}\" "
                  f"--chapter {escape(str(chapter))}[/bold]")
    raise typer.Exit(GATE_EXIT_CODE)


@app.command()
def render(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
    workers: int = typer.Option(None, "--workers", help="Parallel page jobs (default: cores-1)."),
    all_pages: bool = typer.Option(
        False, "--all",
        help="Render every page, even when a QA round left only some waiting for a re-render.",
    ),
) -> None:
    """Clean the source lettering, set the Vietnamese, and open visual QA."""
    setup_console()
    from comic.qa.visual import load_plan, render_chapter

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    plan = load_plan(paths, state)

    # A QA round that sent lines back to the translate gate left its other
    # fixes waiting too. Render just those pages: every other page was approved
    # and is pixel-identical to a fresh render, because each render starts
    # from the original plus the stored plan.
    only = list(page or []) or None
    unchanged = None
    if only is None and not all_pages and plan.rerender_pages:
        only = sorted(plan.rerender_pages)
        unchanged = sorted(
            r.page for r in state.pages
            if not r.excluded and r.page not in only
            and (paths.translated / r.filename).exists()
        )
        console.print(
            f"  [path]rendering only the {len(only)} page(s) the last QA round "
            f"changed: {', '.join(str(p) for p in only)} (--all renders every page)[/path]"
        )

    console.print("[stage]clean + typeset[/stage]")
    outcome = render_chapter(
        paths, state, plan, only=only, workers=workers,
        model=paths.root / "models" / "inpainting_lama.onnx",
        progress=lambda pg, n: console.print(f"  page {pg:03d}: {n} line(s) set"),
    )
    for note in outcome.notes:
        console.print(f"  [warn]![/warn] {escape(note)}")

    if not outcome.pages:
        console.print("[fail]x[/fail] Nothing rendered. Is the chapter translated?")
        raise typer.Exit(1)

    console.print(
        f"\n[ok]+[/ok] {len(outcome.pages)} page(s): "
        f"{outcome.flat} flat fill, {outcome.inpainted} inpainted"
    )
    if outcome.artwork_intact:
        console.print("[ok]+[/ok] artwork outside the cleaning masks is unchanged")
    else:
        console.print("[fail]x[/fail] artwork changed outside the masks -- not publishable")

    state.stage(Stage.CLEAN).status = StageStatus.DONE
    state.stage(Stage.TYPESET).status = StageStatus.DONE
    store.save(state)
    _open_render_gate(paths, store, state, outcome, plan, series, chapter,
                      unchanged=unchanged)


def _resume_visual_qa(paths, store, state, series, chapter) -> None:
    """Apply visual_qa fixes, re-render, and either publish or ask again."""
    from pydantic import ValidationError

    from comic.handoff.schemas import FixAction, VisualQaResponse
    from comic.qa.visual import (
        MAX_QA_ROUNDS,
        apply_fixes,
        load_plan,
        pages_of,
        publish,
        render_chapter,
        save_plan,
        touched_pages,
    )
    from comic.state.store import close_gate

    path = paths.gate_response(Gate.VISUAL_QA.value)
    if not path.exists():
        console.print(f"[fail]x[/fail] no response at {escape(str(path))}")
        raise typer.Exit(1)
    try:
        answer = VisualQaResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        state.gate_error = str(exc)
        store.save(state)
        console.print(
            "[fail]x[/fail] response does not match the schema:\n"
            f"{escape(str(exc))}"
        )
        console.print("\n  Fix the response and run resume again.")
        raise typer.Exit(1)

    # A restyle to a voice is checked against this series' fonts.yaml here,
    # since the schema cannot see the profile; a typo would fall back silently.
    from comic.handoff.schemas import FixAction
    from comic.paths import SeriesPaths
    from comic.schemas import TextKind
    from comic.typesetting.render import voice_uses

    voices = voice_uses(SeriesPaths(state.series).fonts_file)
    kinds = {k.value for k in TextKind}
    bad = [
        f"{f.region_id}: unknown voice {f.value!r}"
        for f in answer.fixes
        if f.action is FixAction.RESTYLE and f.value not in kinds and f.value not in voices
    ]
    if bad:
        state.gate_error = "; ".join(bad) + f" (voices: {', '.join(sorted(voices)) or 'none'})"
        store.save(state)
        console.print(f"[fail]x[/fail] {escape(state.gate_error)}")
        raise typer.Exit(1)

    plan = load_plan(paths, state)

    if answer.blocked:
        console.print(f"[warn]![/warn] blocked: {escape(', '.join(answer.blocked))}")
    if answer.note:
        console.print(f"  [path]{escape(answer.note)}[/path]")

    if answer.passed:
        close_gate(state)
        state.stage(Stage.QA).status = StageStatus.DONE
        count, problems = publish(paths, state)
        for problem in problems:
            console.print(f"  [warn]![/warn] {escape(problem)}")
        if count:
            state.stage(Stage.PUBLISH).status = StageStatus.DONE
            state.status = (
                ChapterStatus.PARTIAL if problems else ChapterStatus.COMPLETED
            )
        store.save(state)
        console.print(f"[ok]+[/ok] {count} page(s) published.")
        console.print(f"  [path]{escape(str(paths.library))}[/path]")
        return

    applied, retranslate = apply_fixes(plan, answer.fixes)
    # The pages this answer changes. Only these are re-rendered and shown
    # again; every other page was approved and no fix touched it.
    touched = touched_pages(paths, state, answer.fixes)
    if retranslate:
        # The re-render waits for the shorter lines; remember every page this
        # round changed so the next `comic render` does just those.
        plan.rerender_pages = sorted(set(plan.rerender_pages) | touched)
    save_plan(paths, plan)
    console.print(f"[ok]+[/ok] {applied} fix(es) applied (round {plan.round}).")

    if retranslate:
        # Shortening a line is a translation decision, so it goes back to the
        # translate gate rather than being solved with a smaller font.
        from comic.state.store import open_gate
        from comic.translation.gate import open_translate_gate

        pages = sorted(pages_of(paths, state, retranslate))
        limits = {
            rid: plan.for_region(rid).max_chars
            for rid in retranslate
            if plan.for_region(rid).max_chars
        }
        request, count = open_translate_gate(
            paths, state, only=pages or None, limits=limits or None
        )
        open_gate(state, Stage.TRANSLATE, Gate.TRANSLATE)
        store.save(state)
        console.print(
            f"\n[gate]GATE: translate[/gate] -- {len(retranslate)} line(s) must be "
            f"shorter: {escape(', '.join(retranslate[:6]))}"
        )
        for rid in retranslate:
            limit = plan.for_region(rid).max_chars
            console.print(f"  {escape(rid)}: at most {limit} characters")
        console.print(f"  read   [path]{escape(str(request))}[/path]")
        console.print(f"  write  [path]{escape(str(paths.gate_response('translate')))}[/path]")
        if plan.rerender_pages:
            console.print(
                f"  then   [bold]comic resume[/bold], then [bold]comic render[/bold] "
                f"(it renders only the {len(plan.rerender_pages)} page(s) this round changed)"
            )
        raise typer.Exit(GATE_EXIT_CODE)

    if plan.round > MAX_QA_ROUNDS:
        close_gate(state)
        store.save(state)
        console.print(
            f"[warn]![/warn] {MAX_QA_ROUNDS} rounds of fixes did not settle this "
            "chapter. Stopping rather than looping; the rendered pages are in "
            f"{escape(str(paths.translated))}."
        )
        raise typer.Exit(1)

    # Re-render only the pages a fix touched. When none did (an answer of
    # accepts, or only blocked pages), fall back to the whole chapter so the
    # gate is never empty.
    # Pages still waiting from an earlier round are rendered too.
    only = sorted(touched | set(plan.rerender_pages)) or None
    unchanged = None
    console.print("\n[stage]re-render[/stage]")
    if only:
        unchanged = sorted(
            r.page for r in state.pages
            if not r.excluded and r.page not in only
            and (paths.translated / r.filename).exists()
        )
        console.print(
            f"  [path]only the {len(only)} page(s) with a fix: "
            f"{', '.join(str(p) for p in only)}[/path]"
        )
    outcome = render_chapter(
        paths, state, plan, only=only,
        model=paths.root / "models" / "inpainting_lama.onnx",
        progress=lambda pg, n: console.print(f"  page {pg:03d}: {n} line(s) set"),
    )
    for note in outcome.notes:
        console.print(f"  [warn]![/warn] {escape(note)}")
    _open_render_gate(paths, store, state, outcome, plan, series, chapter,
                      unchanged=unchanged)


@app.command()
def translate(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
) -> None:
    """Open the translate gate for a chapter that has been read."""
    setup_console()
    from comic.memory.profile import SeriesMemory
    from comic.state.store import open_gate
    from comic.translation.gate import open_translate_gate

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    # A series is a long-lived workspace; create its memory on first use.
    SeriesMemory(series).ensure_initialised()

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    request, count = open_translate_gate(paths, state, only=list(page or []) or None)
    if count == 0:
        console.print("[warn]![/warn] Nothing to translate. Run `comic analyze` first.")
        raise typer.Exit(1)

    open_gate(state, Stage.TRANSLATE, Gate.TRANSLATE)
    store.save(state)

    console.print(f"[gate]GATE: translate[/gate] -- {count} line(s) to translate.")
    console.print(f"  context  [path]{escape(str(paths.workspace / 'translate_context.md'))}[/path]")
    console.print(f"  read     [path]{escape(str(request))}[/path]")
    console.print(f"  images   [path]{escape(str(paths.gate_dir('translate')))}[/path]")
    console.print(f"  write    [path]{escape(str(paths.gate_response('translate')))}[/path]")
    console.print(f"  then     [bold]comic resume --series \"{escape(series)}\" "
                  f"--chapter {escape(str(chapter))}[/bold]")
    raise typer.Exit(GATE_EXIT_CODE)


@app.command()
def resume(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
) -> None:
    """Apply a gate response and carry on."""
    setup_console()
    from comic.stages import apply_ocr_review
    from comic.state.store import close_gate

    paths = ChapterPaths(series, chapter)
    store = ChapterStore(paths)
    try:
        state = store.load()
    except StateError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)

    if state.pending_gate is None:
        console.print("[warn]![/warn] This chapter is not waiting at a gate.")
        raise typer.Exit(0)

    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)}[/stage]")
    console.print(f"[gate]gate: {state.pending_gate.value}[/gate]")

    if state.pending_gate is Gate.TRANSLATE:
        from comic.translation.gate import apply_translation, missing_translations

        stored, learned, problems = apply_translation(paths, state)
        if problems:
            # Nothing is written on a problem: a half-applied translation hides
            # its own gaps.
            state.gate_error = "; ".join(problems)
            store.save(state)
            for problem in problems:
                console.print(f"[fail]x[/fail] {escape(problem)}")
            console.print("\n  Fix the response and run resume again.")
            raise typer.Exit(1)

        close_gate(state)
        state.stage(Stage.TRANSLATE).status = StageStatus.DONE
        store.save(state)
        console.print(f"[ok]+[/ok] {stored} line(s) translated.")
        for item in learned:
            console.print(f"    learned: {escape(item)}")
        pending = missing_translations(paths, state)
        if pending:
            console.print(
                f"[warn]![/warn] {len(pending)} region(s) still untranslated "
                f"(e.g. {', '.join(pending[:3])})"
            )
        console.print("  Next: [bold]comic render --series "
                      f"\"{escape(series)}\" --chapter {escape(str(chapter))}[/bold]")
        from comic.qa.visual import load_plan

        waiting = load_plan(paths, state).rerender_pages
        if waiting:
            console.print(
                f"  [path]it renders only the {len(waiting)} page(s) the last QA "
                f"round changed ({', '.join(str(p) for p in waiting)}); "
                "--all renders every page[/path]"
            )
        return

    if state.pending_gate is Gate.VISUAL_QA:
        _resume_visual_qa(paths, store, state, series, chapter)
        return

    if state.pending_gate is not Gate.OCR_REVIEW:
        console.print(f"[warn]![/warn] {state.pending_gate.value} is not implemented yet.")
        raise typer.Exit(1)

    corrected, dropped, problems = apply_ocr_review(paths, state)
    if problems:
        # An invalid answer reopens the gate rather than corrupting state.
        state.gate_error = "; ".join(problems)
        store.save(state)
        for problem in problems:
            console.print(f"[fail]x[/fail] {escape(problem)}")
        console.print("\n  Fix the response and run resume again.")
        raise typer.Exit(1)

    close_gate(state)
    store.save(state)
    console.print(f"[ok]+[/ok] {corrected} reading(s) corrected, {dropped} region(s) dropped.")
    console.print(f"  Next: [bold]comic translate --series \"{escape(series)}\" "
                  f"--chapter {escape(str(chapter))}[/bold]")


@app.command()
def status(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="One chapter; omit for all."),
) -> None:
    """Show per-stage progress."""
    setup_console()
    sp = SeriesPaths(series)
    labels = [chapter] if chapter else sp.chapters()
    if not labels:
        console.print(f"[warn]no chapters found for {escape(sp.series)}[/warn]")
        console.print(f"[path]looked in {escape(str(sp.workspace_dir))}[/path]")
        raise typer.Exit(1)

    table = Table(box=None, pad_edge=False)
    table.add_column("chapter", style="bold")
    table.add_column("pages", justify="right")
    for stage in STAGE_ORDER:
        table.add_column(stage.value[:4], justify="center")
    table.add_column("status")

    for label in sorted(labels, key=chapter_sort_key):
        try:
            state = ChapterStore(ChapterPaths(series, label)).load()
        except StateError:
            continue
        cells = []
        for stage in STAGE_ORDER:
            style, glyph = _STAGE_GLYPH[state.stage(stage).status]
            cells.append(f"[{style}]{glyph}[/{style}]")

        if state.status is ChapterStatus.AWAITING_AGENT:
            note = f"[gate]awaiting agent: {state.pending_gate.value}[/gate]"
        elif state.status is ChapterStatus.COMPLETED:
            note = "[ok]completed[/ok]"
        elif state.status is ChapterStatus.FAILED:
            note = "[fail]failed[/fail]"
        else:
            upcoming = next_stage(state)
            note = f"next: {upcoming.value}" if upcoming else state.status.value
        table.add_row(label, str(state.page_count), *cells, note)

    console.print(table)
    console.print(f"\n[path]{'  '.join(s.value for s in STAGE_ORDER)}[/path]")


@app.callback(invoke_without_command=True)
def main_callback(
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        console.print(f"comic-translator {__version__}")
        raise typer.Exit()


def main() -> None:
    from comic.cpu import throttle

    throttle()
    setup_console()
    app()


if __name__ == "__main__":
    main()
