"""`comic colab ...`: the Colab GPU worker, reached through Google Drive."""

from __future__ import annotations

import typer
from rich.markup import escape

from comic.console import console, setup_console

colab_app = typer.Typer(help="Colab GPU worker over Google Drive (OCR + LaMa).",
                        no_args_is_help=True)


@colab_app.command()
def push() -> None:
    """Send the current code to Drive for the notebook (models are fetched by Colab)."""
    setup_console()
    from comic.colab.jobs import ColabError, push as do_push

    try:
        out = do_push()
    except ColabError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    console.print(f"[ok]+[/ok] pushed: {out}")
    console.print("  Colab: re-run the notebook from cell 1 to pick up code changes.")


@colab_app.command()
def status() -> None:
    """Is the notebook's worker cell running?"""
    setup_console()
    from comic.colab.jobs import ColabError, drive_root, worker_status

    try:
        root = drive_root()
        st = worker_status()
    except ColabError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    console.print(f"[path]{escape(str(root))}[/path]")
    if not st:
        console.print("[warn]![/warn] no worker has ever reported -- open colab/comic_gpu.ipynb, Run all")
        raise typer.Exit(2)
    tag = "[ok]alive[/ok]" if st["alive"] else "[warn]stale[/warn]"
    console.print(f"{tag} gpu={escape(str(st.get('gpu')))} last beat {st['age_s']}s ago, "
                  f"busy={escape(str(st.get('busy')))}")
    pending = [d.name for d in (root / "jobs").iterdir() if d.is_dir() and not (d / "done.json").exists()]
    console.print(f"jobs waiting: {len(pending)}")
    if not st["alive"]:
        raise typer.Exit(2)


@colab_app.command()
def analyze(
    series: str = typer.Option(..., "--series", help="Series name."),
    chapter: str = typer.Option(..., "--chapter", "-c", help="Chapter number."),
    page: list[int] = typer.Option(None, "--page", "-p", help="Limit to these pages."),
    force: bool = typer.Option(False, "--force", help="Redo work already done."),
    language: str = typer.Option(None, "--language", help="Override the source language."),
) -> None:
    """Slice + detect + OCR on the Colab T4; results land in the local workspace.

    Next step: `comic studio --series NAME -c N` to review the regions.
    """
    setup_console()
    from comic.colab.jobs import ColabError
    from comic.colab.tasks import analyze as remote

    try:
        summary = remote(series, chapter, list(page) if page else None, force, language,
                         progress=lambda m: console.print(f"  {escape(m)}"))
    except ColabError as exc:
        console.print(f"[fail]x[/fail] {escape(str(exc))}")
        raise typer.Exit(1)
    for note in summary.get("notes", []):
        console.print(f"  [warn]![/warn] {escape(note)}")
    console.print(f"[ok]+[/ok] {summary.get('pages')} page(s), {summary.get('regions')} region(s), "
                  f"{summary.get('suspicious')} low-confidence, {summary.get('seconds')}s on Colab")
    console.print(f"  next: comic studio --series \"{escape(series)}\" -c {escape(chapter)}")


@colab_app.command("stop-drive")
def stop_drive() -> None:
    """Close Google Drive for desktop (it does not start at login)."""
    from comic.colab.jobs import stop_drive as do_stop

    do_stop()
