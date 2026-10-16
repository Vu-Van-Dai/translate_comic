"""Posting plan for the channel videos (D:/Kênh/Kênh tiktok/bl) on TikTok and the Facebook page.

    python scripts/channel_schedule.py "D:/Kênh/Kênh tiktok/bl" --start 2026-10-16 [--dry]

The operator's rules (2026-10-15):
- only the videos we made there (``edit - <song> - <artist>.mp4``), each once;
- random order and random times, until the batch is used up;
- 1-2 days a week left free of automatic posts (the operator posts by hand);
- every caption carries #review and #spoiler, on both platforms.

One post per posting day, at a random time in the evening-weighted window;
the Facebook post goes out within an hour of the TikTok one. The plan is
written once to workspace/_logs/channel_schedule.json and never reshuffled:
``scripts/channel_post.py`` marks each entry as it is scheduled on a platform.
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

CATEGORY_TAG = {"manga": "#manga", "manhua": "#manhua", "manhwa": "#manhwa", "lụm nhặt": "#truyentranh"}
BASE_TAGS = ["#review", "#spoiler", "#bl", "#truyenbl", "#boylove",
             # the operator, 2026-10-15: on every post, TikTok and Facebook alike
             "#mangabl", "#manhuabl", "#manhwabl", "#truyentranhdammy"]
EXTRA_TAGS = ["#truyentranhdammy", "#metruyentranh", "#dammy", "#xuhuong", "#viral", "#fyp", "#blrecommendations", "#reviewtruyen"]

HOOKS = [
    "Bộ này dễ thương xỉu luôn 🥹 Review nhanh cho mọi người nè",
    "Đọc tới đây là tim mình không chịu nổi nữa rồi 😭",
    "Couple này ngọt quá trời ơi, ai đọc chưa? 🍯",
    "Spoiler nhẹ thôi, phần sau còn cuốn hơn nữa 👀",
    "Lỡ đọc 1 chap là không dừng lại được luôn 😳",
    "Visual đỉnh, cốt truyện cũng đỉnh, đọc ngay đi mọi người 🔥",
    "Ai cần một bộ BL đọc giải trí thì đây nè 💕",
    "Cặp đôi này mà không về chung một nhà là mình buồn lắm đó 😤",
    "Nhìn ánh mắt này là biết có chuyện rồi 🫣",
    "Review siêu ngắn: ngọt, hài, hơi ngược một xíu 🥲",
    "Đố mọi người đoán được kết của bộ này 🤭",
    "Bộ này đang hot lắm nè, ai đọc rồi điểm danh 👇",
]
UNTITLED_HOOKS = [
    "Bộ này tên gì đố mọi người biết đó 👀 Biết thì comment nha",
    "Lụm được bộ này hay quá, ai biết tên comment giúp mình nha 👇",
    "Mấy khung hình này đẹp quá trời, ai đọc bộ này chưa? 🥹",
]


def title_of(folder: Path) -> str | None:
    name = re.sub(r"^\d+-", "", folder.name).strip()
    if folder.parent.name == "lụm nhặt":
        named = [f.stem for f in folder.iterdir() if f.suffix.lower() in (".txt", ".docx")
                 and not f.stem.lower().startswith("new microsoft")]
        return named[0] if named else None
    if name.lower() in ("no name", "no name(1)", "meme", "new folder", "tuyển tập"):
        return None
    return name.title() if name == name.lower() else name


def caption(folder: Path, rng: random.Random) -> str:
    own = [f.stem for f in folder.glob("*.txt")]           # the operator's own caption line
    title = title_of(folder)
    hook = own[0] if own and folder.parent.name != "lụm nhặt" else rng.choice(HOOKS if title else UNTITLED_HOOKS)
    tags = BASE_TAGS + [CATEGORY_TAG.get(folder.parent.name, "#truyentranh")] + rng.sample(EXTRA_TAGS, 3)
    head = f"{hook}\n📖 {title}" if title else hook
    return f"{head}\n\n{' '.join(tags)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--start", default=None, help="first posting day, YYYY-MM-DD (default tomorrow)")
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if PLAN.exists() and not args.dry:
        raise SystemExit(f"{PLAN} exists -- the plan is fixed once; delete it to rebuild")
    rng = random.Random(args.seed)
    videos = sorted(Path(args.root).rglob("edit - *.mp4"))
    rng.shuffle(videos)
    day = dt.date.fromisoformat(args.start) if args.start else dt.date.today() + dt.timedelta(days=1)
    entries, free = [], set()
    week = None
    i = 0
    while i < len(videos):
        monday = day - dt.timedelta(days=day.weekday())
        if monday != week:
            week = monday
            n_free = rng.choice([1, 2])
            free = {monday + dt.timedelta(days=d) for d in rng.sample(range(7), n_free)}
        if day not in free:
            v = videos[i]
            # Evening-weighted: most posts 18:00-22:30, some at lunch.
            if rng.random() < 0.75:
                minute = rng.randint(18 * 60, 22 * 60 + 30)
            else:
                minute = rng.randint(11 * 60 + 30, 13 * 60 + 30)
            tt = dt.datetime.combine(day, dt.time(minute // 60, minute % 60))
            fb = tt + dt.timedelta(minutes=rng.randint(5, 60))
            entries.append({
                "video": str(v), "folder": v.parent.name, "category": v.parent.parent.name,
                "caption": caption(v.parent, rng),
                "tiktok": {"at": tt.isoformat(timespec="minutes"), "status": "pending"},
                "facebook": {"at": fb.isoformat(timespec="minutes"), "status": "pending"},
            })
            i += 1
        day += dt.timedelta(days=1)
    plan = {"created": dt.datetime.now().isoformat(timespec="seconds"), "timezone": "local (Asia/Ho_Chi_Minh)",
            "entries": entries}
    if args.dry:
        for e in entries[:8]:
            print(e["tiktok"]["at"], e["category"], e["folder"], "|", e["caption"].replace("\n", " / ")[:150])
    else:
        PLAN.parent.mkdir(parents=True, exist_ok=True)
        PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(entries)} posts, {entries[0]['tiktok']['at']} -> {entries[-1]['tiktok']['at']}")


if __name__ == "__main__":
    main()
