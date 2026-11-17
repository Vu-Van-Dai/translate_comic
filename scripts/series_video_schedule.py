"""Add our translated-series videos to the channel schedule's open days.

    python scripts/series_video_schedule.py [--dry-run]

The operator (2026-10-27): the days with no channel video
(workspace/_logs/channel_schedule.json; photo posts do not count) are reserved for the videos of the series we
translate, on Facebook, TikTok and YouTube. This appends one entry per
video in ``VIDEOS`` to channel_schedule.json, each on its own open day, and
``scripts/channel_post.py`` then schedules them like any other entry.
Already-planned videos (same file) are skipped, so it is safe to re-run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "workspace" / "_logs" / "channel_schedule.json"
POSTS = ROOT / "posts" / "schedule.json"
VIDEOS_DIR = ROOT / "videos"

# The operator, 2026-10-15: on every channel post besides the series' own
# tags (channel_post.py also refuses a TikTok caption without the first two).
REQUIRED_TAGS = ["#review", "#spoiler", "#mangabl", "#manhuabl", "#manhwabl", "#truyentranhdammy"]
MAX_TAGS = 15   # YouTube drops every hashtag past 15

# (series, video folder, display title); the music version of each is posted.
VIDEOS = [
    ("Mi Imitador", "Intro", "Mi Imitador"),
    ("Kimigazer", "DeThuong_DungGu", "Kimigazer"),
    ("Mi Imitador", "Arc_01-10", "Mi Imitador"),
    ("Super Lovers", "Chapter_002", "Super Lovers"),
    ("Mi Imitador", "Highlight_Ch13_PuppyWalk", "Mi Imitador"),
    ("Shuumatsu, Kimi to", "UBuon_LoiHua", "Shuumatsu, Kimi to"),
    ("Mi Imitador", "Arc_11-20", "Mi Imitador"),
    ("Itsuka, Kazoku ni Naru made", "Template_Today", "Itsuka, Kazoku ni Naru made"),
    ("Mi Imitador", "KanghyunJiun", "Mi Imitador"),
    ("Kimigazer", "Buon_ThuongNho", "Kimigazer"),
    ("Mi Imitador", "Arc_21-30", "Mi Imitador"),
    ("Super Lovers", "Showcase_Haru", "Super Lovers"),
    ("Mi Imitador", "Highlight_Ch29_Snow", "Mi Imitador"),
    ("Mi Imitador", "Arc_31-40", "Mi Imitador"),
    ("Mi Imitador", "HaeyongYoonjae_Wreath", "Mi Imitador"),
    ("Mi Imitador", "Arc_41-50", "Mi Imitador"),
    ("Mi Imitador", "Highlight_Ch45_Confession", "Mi Imitador"),
    ("Mi Imitador", "Split_KanghyunJiun", "Mi Imitador"),
    ("Mi Imitador", "Arc_51-60", "Mi Imitador"),
    ("Mi Imitador", "Split_HaeyongYoonjae", "Mi Imitador"),
    ("Mi Imitador", "Highlight_Ch59_Beach", "Mi Imitador"),
]

# Folders without a caption.txt get theirs here.
CAPTIONS = {
    "Template_Today": "Một ngày bình thường mà ấm áp ghê 🥹\n📖 Itsuka, Kazoku ni Naru made\n"
                      "#bl #manga #dammy #boylove #truyentranh",
}


def music_file(folder: Path) -> Path:
    """The version with music: video_nhac_tam.mp4 / video.mp4 / the only mp4."""
    for name in ("video_nhac_tam.mp4", "video.mp4"):
        if (folder / name).exists():
            return folder / name
    mp4 = [p for p in folder.glob("*.mp4") if "khong_nhac" not in p.name and "silent" not in p.name]
    if len(mp4) != 1:
        raise SystemExit(f"{folder}: cannot tell which mp4 has the music: {mp4}")
    return mp4[0]


def caption_of(folder: Path) -> str:
    """The post text: caption.txt's short version when it has one, plus the required tags."""
    text = CAPTIONS.get(folder.name)
    if text is None:
        raw = (folder / "caption.txt").read_text(encoding="utf-8")
        m = re.search(r"--- Ngắn ---\s*\n(.*?)(?:\n--- |\Z)", raw, re.S)
        text = (m.group(1) if m else raw).strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tags: list[str] = []
    body: list[str] = []
    for ln in lines:
        words = ln.split()
        if words and all(w.startswith("#") for w in words):
            tags += words
        else:
            body.append(ln)
    for t in REQUIRED_TAGS:
        if t.lower() not in (x.lower() for x in tags):
            tags.append(t)
    seen, unique = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    # Keep the required ones when trimming to YouTube's limit.
    keep = [t for t in unique if t.lower() in (r.lower() for r in REQUIRED_TAGS)]
    other = [t for t in unique if t not in keep][: MAX_TAGS - len(keep)]
    return "\n".join(body) + "\n\n" + " ".join(other + keep)


def busy_days(plan: dict) -> set[str]:
    days = set()
    for e in plan["entries"]:
        for p in ("tiktok", "facebook", "youtube"):
            if isinstance(e.get(p), dict) and e[p].get("at"):
                days.add(e[p]["at"][:10])
    # Photo posts (posts/schedule.json) do not make a day busy: the operator
    # (2026-10-28) wants a video on every day, so Nov 13 and 24 -- photo-post
    # days -- were left without one and had to be filled by hand.
    return days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", default=None, help="first day to use (default: tomorrow)")
    args = ap.parse_args()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    planned = {e["video"] for e in plan["entries"]}
    busy = busy_days(plan)
    day = dt.date.fromisoformat(args.start) if args.start else dt.date.today() + dt.timedelta(days=1)
    rng = random.Random(20261027)
    added = []
    for series, name, title in VIDEOS:
        folder = VIDEOS_DIR / series / name
        video = str(music_file(folder))
        if video in planned:
            continue
        while str(day) in busy:
            day += dt.timedelta(days=1)
        hh, mm = rng.choice([19, 20, 21]), rng.choice(range(0, 60, 5))
        at = dt.datetime.combine(day, dt.time(hh, mm))
        fb = at + dt.timedelta(minutes=rng.randint(15, 22))
        caption = caption_of(folder)
        hook = caption.splitlines()[0]
        entry = {
            "video": video, "folder": f"{series} / {name}", "category": "translated",
            "caption": caption, "title": f"{title} | {hook}"[:100],
            "tiktok": {"at": at.isoformat(timespec="minutes"), "status": "pending"},
            "facebook": {"at": fb.isoformat(timespec="minutes"), "status": "pending"},
            "youtube": {"at": at.isoformat(timespec="minutes"), "status": "pending"},
        }
        plan["entries"].append(entry)
        busy.add(str(day))
        added.append(entry)
        print(f"{at:%Y-%m-%d %H:%M}  {series} / {name}")
    if not args.dry_run and added:
        tmp = PLAN.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(PLAN)
    print(f"{len(added)} added{' (dry run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
