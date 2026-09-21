"""One-shot environment setup.

Creates the directory layout, installs optional dependency groups, downloads the
Chromium build Playwright drives, fetches fonts and models, then runs the doctor
so the last thing printed is an honest verdict on whether the machine is ready.

Every step is skippable and re-runnable; nothing here is destructive.

Usage:
    python scripts/bootstrap.py [--skip-deps] [--skip-models] [--skip-fonts]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comic.console import console, setup_console  # noqa: E402

#: Directories that must exist before any stage runs.
LAYOUT = [
    "library", "workspace", "profiles", "models",
    "assets/fonts", "tests/fixtures", "raw",
]

DEPENDENCY_GROUPS = ["browser", "vision", "ocr", "dev"]


def run(cmd: list[str], label: str) -> bool:
    console.rule(f"[stage]{label}[/stage]")
    console.print(f"[path]$ {' '.join(cmd)}[/path]")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        console.print(f"[fail]x[/fail] {label} failed (exit {result.returncode})")
        return False
    console.print(f"[ok]+[/ok] {label}")
    return True


def make_layout() -> None:
    console.rule("[stage]directory layout[/stage]")
    for rel in LAYOUT:
        path = ROOT / rel
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").touch(exist_ok=True)
    console.print(f"[ok]+[/ok] {len(LAYOUT)} directories ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--skip-fonts", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    args = parser.parse_args()

    setup_console()
    failures: list[str] = []
    make_layout()

    if not args.skip_deps:
        extras = ",".join(DEPENDENCY_GROUPS)
        if not run([sys.executable, "-m", "pip", "install", "-e", f".[{extras}]"],
                   "python dependencies"):
            failures.append("dependencies")

    if not args.skip_browser:
        if not run([sys.executable, "-m", "playwright", "install", "chromium"],
                   "chromium for playwright"):
            failures.append("browser")

    if not args.skip_fonts:
        if not run([sys.executable, "scripts/fetch_fonts.py"], "fonts"):
            failures.append("fonts")

    if not args.skip_models:
        if not run([sys.executable, "scripts/fetch_models.py"], "models"):
            failures.append("models")

    console.rule("[stage]verification[/stage]")
    doctor = subprocess.run([sys.executable, "-m", "comic.cli.app", "doctor"], cwd=ROOT)

    console.print()
    if failures:
        console.print(f"[warn]![/warn] setup steps with problems: {', '.join(failures)}")
    if doctor.returncode == 0:
        console.print("[ok]Environment ready.[/ok] Next: comic run --series NAME --url URL")
        return 0
    console.print("[warn]![/warn] See the doctor output above for what is still missing.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
