"""Collect named TikTok sounds (songs, not "original sound") into the music library.

    python scripts/sound_fetch.py taylorswift:pop,us kpop:kpop vpop:vpop,vietnamese --per 6

Each argument is ``tag:mood,mood`` (or ``q=search words:mood,mood``). Through the operator's Edge (CDP, port
9223) the tag page's item list is read; every post whose sound is a named
track (TikTok's ``original`` flag false, and not titled "original sound" /
"âm thanh gốc" / "nhạc nền") gives that sound, the clip TikTok serves for it.
Up to ``--per`` new sounds per tag are saved as
``assets/music/trending/tt_<slug>_<id4>.mp3`` and appended to
``assets/music/library.yaml`` with the tag's moods. Already-registered
sounds (by TikTok music id) are skipped, so a re-run only adds.

The operator's call (2026-10-14): videos are not monetized and songs may be
used; vary them widely -- Taylor Swift, V-pop, K-pop, J-pop and more.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import yaml
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ref_fetch import collect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "assets" / "music" / "library.yaml"
OUT = ROOT / "assets" / "music" / "trending"
GENERIC = re.compile(r"original sound|âm thanh gốc|nhạc nền|son original|sonido original|オリジナル楽曲|原声|오리지널", re.I)


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:40] or "track"


def known_ids() -> set[str]:
    data = yaml.safe_load(LIB.read_text(encoding="utf-8")) or {}
    ids = set()
    for t in data.get("tracks", []):
        m = re.search(r"/music/[^/]*?-?(\d{8,})", t.get("source") or "")
        if m:
            ids.add(m.group(1))
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+", help="tag:mood,mood")
    ap.add_argument("--per", type=int, default=6)
    ap.add_argument("--scrolls", type=int, default=5)
    ap.add_argument("--originals", type=int, default=0,
                    help="also take 'original sound' re-uploads from posts with at least this many plays "
                         "(Chinese songs on TikTok are mostly re-uploads, 2026-10-15)")
    args = ap.parse_args()
    seen = known_ids()
    added = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            for spec in args.tags:
                tag, _, moods = spec.partition(":")
                moods = [m for m in moods.split(",") if m] or ["trending"]
                # "q=words" searches (needs the operator's TikTok login) instead of a tag page:
                # tag pages surface mostly "original sound" posts for Chinese songs.
                url = (f"https://www.tiktok.com/search/video?q={quote(tag[2:])}" if tag.startswith("q=")
                       else f"https://www.tiktok.com/tag/{quote(tag)}?lang=vi-VN")
                items = collect(page, url, args.scrolls)
                items.sort(key=lambda it: -int((it.get("stats") or {}).get("playCount") or 0))
                n = 0
                for it in items:
                    mu = it.get("music") or {}
                    mid, title, url = str(mu.get("id") or ""), mu.get("title") or "", mu.get("playUrl")
                    if n >= args.per or not mid or not url or mid in seen:
                        continue
                    plays = int((it.get("stats") or {}).get("playCount") or 0)
                    if mu.get("original") or GENERIC.search(title):
                        if not (args.originals and plays >= args.originals):
                            continue
                        if GENERIC.search(title):    # "âm thanh gốc - <account>": name it after the search
                            title = f"{(tag[2:] if tag.startswith('q=') else tag).title()} ({mu.get('authorName') or 'TikTok'})"
                    r = ctx.request.get(url, headers={"Referer": "https://www.tiktok.com/"}, timeout=120000)
                    body = r.body()
                    if r.status not in (200, 206) or len(body) < 100000:
                        continue
                    tid = f"tt_{slug(title)}_{mid[-4:]}"
                    OUT.mkdir(parents=True, exist_ok=True)
                    (OUT / f"{tid}.mp3").write_bytes(body)
                    seen.add(mid)
                    n += 1
                    added.append({
                        "id": tid, "file": f"trending/{tid}.mp3", "title": title,
                        "artist": mu.get("authorName") or "", "source": f"https://www.tiktok.com/music/x-{mid}",
                        "license": "TikTok sound (operator's choice, non-monetized)", "moods": moods,
                    })
                    print(f"{tag}: {tid}  {title!r} / {mu.get('authorName')!r}", flush=True)
                    time.sleep(1.5)
                print(f"{tag}: {n} new of {len(items)} posts", flush=True)
        finally:
            page.close()
    if added:
        text = LIB.read_text(encoding="utf-8").rstrip("\n") + "\n"
        for a in added:
            text += "  - " + yaml.safe_dump(a, allow_unicode=True, sort_keys=False, width=200).replace("\n", "\n    ").rstrip() + "\n"
        LIB.write_text(text, encoding="utf-8")
    print(f"{len(added)} sounds added")


if __name__ == "__main__":
    main()
