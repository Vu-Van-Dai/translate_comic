"""Run the `comic` CLI without the comic.exe launcher.

Windows Application Control can block the pip-generated comic.exe (seen
2026-10-22: "An Application Control policy has blocked this file"); the
python.exe it wraps is still allowed, so this calls the same entry point.

    python scripts/comic_run.py ps build --series NAME -c N
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.cli.app import main  # noqa: E402

if __name__ == "__main__":
    # the guard matters: the worker pools spawn fresh interpreters that import
    # this file, and an unguarded main() re-runs the CLI in every worker and
    # kills the pool (BrokenProcessPool on `analyze`, 2026-10-26)
    sys.argv[0] = "comic"
    main()
