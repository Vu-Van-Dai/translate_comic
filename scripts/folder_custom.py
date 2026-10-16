"""Render folder videos from a hand-written plan (captions, image subsets, names).

    python scripts/folder_custom.py workspace/_logs/folder_captions.json KEY [KEY...] --track KEY=TRACK_ID ...

Each entry: folder, recipe, optional images (order), captions {file: [text, pos]},
out_name ("{title}" = the track title). See ``comic/video/folder.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.console import setup_console  # noqa: E402
from comic.cpu import throttle  # noqa: E402


def main() -> None:
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--track", action="append", default=[], help="KEY=TRACK_ID")
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()
    throttle()
    from comic.video import folder as fv
    from comic.video import music as music_lib

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    tracks = dict(t.split("=", 1) for t in args.track)
    lib = music_lib.library()
    for key in args.keys:
        e = plan[key]
        folder = Path(e["folder"])
        only = [folder / n for n in e["images"]] if e.get("images") else None
        caps = {k: tuple(v) for k, v in (e.get("captions") or {}).items()}
        track = lib[tracks[key]]
        out_name = e.get("out_name")
        if out_name:
            out_name = out_name.format(title=fv.safe_name(track.title))
        out = fv.render_folder(folder, e["recipe"], track.id, seed=args.seed, only=only, captions=caps or None,
                               out_name=out_name, log=lambda m: None)
        print(key, "->", out, flush=True)


if __name__ == "__main__":
    main()
