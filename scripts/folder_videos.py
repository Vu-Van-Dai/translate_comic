"""One video per image folder of the operator's channel (2026-10-14).

    python scripts/folder_videos.py "D:/Kênh/Kênh tiktok/bl" [--dry]

Walks <root>/<manga|manhua|manhwa|lụm nhặt>/<series>/, skipping folders that
already hold a video or that contain subfolders, and renders a video into each
one with a recipe and a track spread for variety (comic/video/folder.py).
The plan (folder, recipe, track) is fixed once in workspace/_logs/folder_videos.json
so a re-run resumes with the same choices; a folder that gained a video is
skipped, which also makes it safe to stop and restart.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comic.video import music as music_lib  # noqa: E402
from comic.video.folder import assign, eligible, render_folder  # noqa: E402

TOPS = ["manga", "manhua", "manhwa", "lụm nhặt"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    plan_file = ROOT / "workspace" / "_logs" / "folder_videos.json"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.exists() else {}
    folders = [d for top in TOPS if (root / top).is_dir() for d in sorted((root / top).iterdir()) if eligible(d)]
    new = [d for d in folders if str(d) not in plan]
    if new:
        tracks = [t for t in music_lib.library().values() if t.path.exists() and t.id.startswith("tt_")]
        # Spread over the whole run, continuing after what earlier runs used.
        for d, recipe, track in assign(new, tracks, seed=len(plan) + 7):
            plan[str(d)] = {"recipe": recipe, "track": track}
        plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    todo = [d for d in folders if str(d) in plan]
    print(f"{len(todo)} folders to render", flush=True)
    if args.dry:
        for d in todo:
            print(plan[str(d)]["recipe"], plan[str(d)]["track"], d)
        return
    lib = music_lib.library()
    for i, d in enumerate(todo, 1):
        if not eligible(d):
            continue
        p = plan[str(d)]
        t0 = time.time()
        try:
            out = render_folder(d, p["recipe"], p["track"], seed=i, log=lambda m: None)
            p["video"] = out.name
            print(f"[{i}/{len(todo)}] {p['recipe']:9s} {lib[p['track']].title[:30]:30s} {time.time() - t0:4.0f}s  {d}", flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad folder must not stop the run
            p["error"] = repr(exc)
            print(f"[{i}/{len(todo)}] FAILED {d}: {exc}", flush=True)
            traceback.print_exc()
        plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
