"""``comic video ...`` -- short vertical videos from a chapter's art (add-on)."""

from __future__ import annotations

import typer
from rich.markup import escape

from comic.console import console, setup_console
from comic.paths import ChapterPaths

video_app = typer.Typer(
    name="video",
    help="Short TikTok-style videos from a chapter's text-free art (separate feature).",
    no_args_is_help=True,
)


@video_app.callback()
def _rules() -> None:
    # The video rules moved from CLAUDE.md to docs/video.md; one line on every
    # `comic video` command keeps them from being skipped.
    setup_console()
    console.print("[path]rules for videos: docs/video.md[/path]")


@video_app.command("plan")
def plan_cmd(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    name: str = typer.Option(None, "--name", help="A named video hosted in this chapter (arc recap, showcase); one chapter may host several."),
    source: str = typer.Option(None, "--source", help="original (the source as served) | art (Photoshop text-free) | cleaned | translated (only where video.yaml allows); default: the series'"),
) -> None:
    """Cut the pages into panels and open the video_plan gate."""
    setup_console()
    from comic.video.gate import GATE_EXIT, VideoPaths, open_plan
    from comic.video.source import ArtSource

    paths = ChapterPaths(series, chapter)
    console.rule(f"[stage]{escape(paths.series)} / {escape(paths.chapter)} -- video[/stage]")
    req = open_plan(paths, ArtSource(source) if source else None, name)
    vp = VideoPaths(paths, name)
    console.print(
        f"GATE: video_plan\n  read   [path]{escape(str(req))}[/path]\n"
        f"  sheets [path]{escape(str(vp.handoff / 'video_plan'))}[/path]\n"
        f"  write  [path]{escape(str(vp.response('video_plan')))}[/path]\n"
        f"  then   comic video render --series \"{escape(series)}\" -c {escape(chapter)}"
    )
    raise typer.Exit(GATE_EXIT)


@video_app.command("render")
def render_cmd(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    name: str = typer.Option(None, "--name", help="A named video hosted in this chapter (arc recap, showcase); one chapter may host several."),
) -> None:
    """Validate the plan, render both MP4s, open the video_qa gate."""
    setup_console()
    from comic.video.gate import GATE_EXIT, MAX_ROUNDS, VideoPaths, _state, load_plan, render

    paths = ChapterPaths(series, chapter)
    vp = VideoPaths(paths, name)
    plan, problems = load_plan(paths, name)
    if problems:
        for p in problems:
            console.print(f"  [fail]x[/fail] {escape(p)}")
        raise typer.Exit(1)
    if _state(vp).get("round", 0) >= MAX_ROUNDS:
        console.print(f"[warn]![/warn] {MAX_ROUNDS} rounds already -- escalate to the operator")
        raise typer.Exit(1)
    result = render(paths, plan, log=lambda m: console.print(m), name=name)
    console.print(
        f"[ok]+[/ok] {result['seconds']:.1f}s, {len(result['durations'])} shots -> "
        f"[path]{escape(str(result['out']))}[/path]\n"
        f"GATE: video_qa (round {result['round']})\n"
        f"  read   [path]{escape(str(result['sheet']))}[/path]\n"
        f"  write  [path]{escape(str(vp.response('video_qa')))}[/path]\n"
        f"  then   comic video publish --series \"{escape(series)}\" -c {escape(chapter)}"
    )
    raise typer.Exit(GATE_EXIT)


@video_app.command("publish")
def publish_cmd(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    name: str = typer.Option(None, "--name", help="A named video hosted in this chapter (arc recap, showcase); one chapter may host several."),
) -> None:
    """Copy the approved video to videos/<series>/<chapter or --name>/."""
    setup_console()
    from comic.video.gate import publish

    try:
        final = publish(ChapterPaths(series, chapter), name=name)
    except ValueError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    console.print(f"[ok]+[/ok] -> [path]{escape(str(final))}[/path]")
    for f in sorted(final.iterdir()):
        console.print(f"  {escape(f.name)}  {f.stat().st_size / 1e6:.1f} MB")


@video_app.command("styles")
def styles_cmd() -> None:
    """List the editing styles."""
    setup_console()
    from comic.video.styles import styles

    for name, s in styles().items():
        lo, hi = s.shot_seconds
        console.print(f"[bold]{name}[/bold]  {lo}-{hi}s/shot  {escape(s.about)}")


@video_app.command("music")
def music_cmd() -> None:
    """List the music library with tempo and moods."""
    setup_console()
    from comic.video.music import analyse, library

    for t in library().values():
        if not t.path.exists():
            console.print(f"[warn]![/warn] {t.id}: file missing")
            continue
        a = analyse(t)
        console.print(f"{t.id:32s} {a['duration']:6.1f}s {a['bpm']:6.1f} bpm  {', '.join(t.moods)}  [{t.license}]")
