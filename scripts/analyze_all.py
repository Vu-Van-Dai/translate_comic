"""Run `comic analyze` (slice + detect + OCR) over every chapter of a series.

    python scripts/analyze_all.py --series "Dasadanan" [--series ...]

The operator's workflow (2026-10-14): analyse every chapter first, review
and translate chapter by chapter afterwards. Each chapter stops at its own
ocr_review gate (exit 10), which is expected; a chapter already past detect
and OCR is skipped. One log per run in workspace/_logs/.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def done(chapter_dir: Path) -> bool:
    f = chapter_dir / "chapter.json"
    if not f.exists():
        return True                       # nothing to analyse (failed download)
    state = json.loads(f.read_text(encoding="utf-8"))
    pages = [p for p in state.get("pages", []) if not p.get("excluded")]
    if not pages:
        return True
    return all(p.get("stages", {}).get("ocr") in ("done", "review") for p in pages) or bool(
        state.get("pending_gate"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", action="append", required=True)
    args = ap.parse_args()
    log_dir = ROOT / "workspace" / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"analyze_all_{time.strftime('%Y%m%d_%H%M%S')}.log"
    with log.open("w", encoding="utf-8") as out:
        for series in args.series:
            for d in sorted((p for p in (ROOT / "workspace" / series).iterdir() if p.is_dir() and p.name.startswith("Chapter_")), key=lambda p: float(p.name.split("_")[1])):
                if not d.is_dir() or not d.name.startswith("Chapter_"):
                    continue
                n = d.name.removeprefix("Chapter_")
                if done(d):
                    out.write(f"== {series} {d.name}: skipped\n")
                    continue
                t = time.time()
                out.write(f"== {series} {d.name}\n")
                out.flush()
                r = subprocess.run([shutil.which("comic") or "comic", "analyze", "--series", series, "-c", n],
                                   cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                   env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
                out.write(f"== {series} {d.name}: exit {r.returncode} in {time.time() - t:.0f}s\n")
                out.flush()
    print(log)


if __name__ == "__main__":
    main()
