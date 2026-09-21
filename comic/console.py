"""Terminal output.

The Windows console defaults to a legacy codepage, so printing Vietnamese raises
``UnicodeEncodeError`` mid-run. Every entry point calls :func:`setup_console`
before writing anything, since the pipeline routinely echoes translated lines.
"""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "ok": "bold green",
        "warn": "bold yellow",
        "fail": "bold red",
        "gate": "bold cyan",
        "path": "dim",
        "stage": "bold magenta",
    }
)

console = Console(theme=THEME, highlight=False, soft_wrap=False)
err_console = Console(theme=THEME, stderr=True, highlight=False)


def setup_console() -> None:
    """Force UTF-8 on stdout/stderr so Vietnamese text cannot crash a run."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Redirected to a pipe that refuses reconfiguration; rich still
            # degrades gracefully, so this is not worth failing the run over.
            pass


def info(message: str, **kw: Any) -> None:
    console.print(message, **kw)


def warn(message: str) -> None:
    console.print(f"[warn]![/warn] {message}")


def error(message: str) -> None:
    err_console.print(f"[fail]x[/fail] {message}")


def success(message: str) -> None:
    console.print(f"[ok]+[/ok] {message}")


def rule(title: str) -> None:
    console.rule(f"[stage]{title}[/stage]")
