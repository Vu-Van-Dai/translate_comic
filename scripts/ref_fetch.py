"""Collect reference TikTok edits through the operator's Edge (CDP, port 9223).

    python scripts/ref_fetch.py user baoboidz --top 8
    python scripts/ref_fetch.py search "edit truyện bl" --top 10

Reads the item lists the page itself loads (profile grid or search results),
ranks them by play count and saves the most-viewed videos with their caption,
sound and stats into workspace/_references/tiktok/<name>/. Private study
material for `comic video` -- never republished.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "workspace" / "_references" / "tiktok"


def collect(page, url: str, scrolls: int) -> list[dict]:
    items: dict[str, dict] = {}

    def grab(obj):
        if isinstance(obj, dict):
            if "video" in obj and "id" in obj and isinstance(obj.get("video"), dict) and obj["video"].get("playAddr"):
                items[obj["id"]] = obj
                return
            for v in obj.values():
                grab(v)
        elif isinstance(obj, list):
            for v in obj:
                grab(v)

    def on_response(resp):
        if "/api/" in resp.url and ("item" in resp.url or "search" in resp.url):
            try:
                grab(resp.json())
            except Exception:  # noqa: BLE001 - non-JSON responses
                pass

    page.on("response", on_response)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(8)
    try:
        raw = page.evaluate("() => (document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__') || {}).textContent || ''")
    except Exception:  # noqa: BLE001 - the page navigated while loading
        raw = ""
    if raw:
        try:
            grab(json.loads(raw))
        except json.JSONDecodeError:
            pass
    for _ in range(scrolls):
        page.mouse.wheel(0, 4000)
        time.sleep(2.5)
    return list(items.values())


def save(ctx, items: list[dict], out: Path, top: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    items.sort(key=lambda it: -int((it.get("stats") or {}).get("playCount") or 0))
    saved = 0
    for it in items:
        if saved >= top:
            break
        vid = it["id"]
        target = out / f"{vid}.mp4"
        video = it["video"]
        mu = it.get("music") or {}
        meta = {
            "music_id": mu.get("id"),
            "music_play_url": mu.get("playUrl"),
            "music_duration": mu.get("duration"),
            "id": vid,
            "author": (it.get("author") or {}).get("uniqueId"),
            "desc": it.get("desc"),
            "music": (it.get("music") or {}).get("title"),
            "music_author": (it.get("music") or {}).get("authorName"),
            "original_sound": (it.get("music") or {}).get("original"),
            "duration": video.get("duration"),
            "stats": it.get("stats"),
        }
        if not target.exists():
            resp = ctx.request.get(video["playAddr"], headers={"Referer": "https://www.tiktok.com/"}, timeout=120000)
            body = resp.body()
            if resp.status not in (200, 206) or len(body) < 50000:
                print(vid, "skipped", resp.status, len(body))
                continue
            target.write_bytes(body)
            time.sleep(2)
        sounds = ROOT.parent / "sounds"
        if meta["music_play_url"] and meta["music_id"]:
            snd = sounds / f"{meta['music_id']}.mp3"
            if not snd.exists():
                r = ctx.request.get(meta["music_play_url"], headers={"Referer": "https://www.tiktok.com/"}, timeout=120000)
                if r.status in (200, 206) and len(r.body()) > 20000:
                    sounds.mkdir(parents=True, exist_ok=True)
                    snd.write_bytes(r.body())
                    (sounds / f"{meta['music_id']}.json").write_text(json.dumps(
                        {"id": meta["music_id"], "title": meta["music"], "author": meta["music_author"],
                         "duration": meta["music_duration"], "original": meta["original_sound"],
                         "used_by": vid}, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{vid}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        saved += 1
        print(f"{vid} {meta['author']} {meta['duration']}s plays={meta['stats'].get('playCount')} "
              f"music={meta['music']!r} / {meta['music_author']!r}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["user", "search", "tag"])
    ap.add_argument("query")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--scrolls", type=int, default=6)
    args = ap.parse_args()
    if args.kind == "user":
        url, name = f"https://www.tiktok.com/@{args.query}?lang=vi-VN", args.query
    elif args.kind == "tag":
        url, name = f"https://www.tiktok.com/tag/{quote(args.query)}?lang=vi-VN", "tag_" + args.query
    else:
        url = f"https://www.tiktok.com/search/video?q={quote(args.query)}&lang=vi-VN"
        name = "search_" + re.sub(r"\W+", "_", args.query).strip("_")
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            items = collect(page, url, args.scrolls)
            print(f"{len(items)} items, page ended on {page.url}")
            if args.kind == "user":
                items = [it for it in items if (it.get("author") or {}).get("uniqueId") == args.query]
                print(f"{len(items)} by @{args.query}")
            else:
                # Logged out, search and tag pages can fall back to the For You
                # feed: keep only videos whose caption names the topic.
                want = re.compile(r"#?(bl|manhwa|danmei|đammỹ|dammy|yaoi|webtoon|manga|boylove)", re.I)
                items = [it for it in items if want.search(it.get("desc") or "")]
                print(f"{len(items)} on topic")
            save(ctx, items, ROOT / name, args.top)
        finally:
            page.close()


if __name__ == "__main__":
    main()
