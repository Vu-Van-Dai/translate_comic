"""Schedule the fan-page image posts (posts/schedule.json) on Meta Business Suite.

    python scripts/fb_photo_post.py [--dry-run] [--limit N]

Through the operator's project Edge (``comic chrome --edge --port 9223``,
signed in to Facebook), each pending post is created in the Business Suite
post composer with its images and caption, for the page Rồng Nhỏ AND the
Instagram account linked to it (the operator, 2026-10-26: littledragontranslations;
both are ticked by default, and checked), and scheduled with "Lên lịch" at its planned
time. Each success is written back at once, so the script is safe to stop and
re-run. Business Suite accepts dates ~29 days ahead: re-run every few weeks
until the plan is done. Nothing here starts on its own.

``--dry-run`` fills the first pending post, saves a screenshot, and cancels.

Measured on the live composer (2026-10-26): "Đăng lên" dropdown with one
checkbox row per account; "Thêm ảnh/video" opens a file chooser; caption is
the contenteditable under "Văn bản"; "Lên lịch" card with a toggle "Đặt ngày
và giờ" that reveals the date/time fields; submit button changes from "Đăng".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "posts" / "schedule.json"
WINDOW = dt.timedelta(days=28, hours=20)
MIN_AHEAD = dt.timedelta(minutes=30)
SHOTS = ROOT / "workspace" / "_logs" / "fb_photo_post"


def save(plan: dict) -> None:
    tmp = PLAN.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PLAN)


def both_accounts(page: Page) -> str:
    """The "Đăng lên" picker must read "Rồng Nhỏ và <instagram account>"."""
    label = page.locator("text=/^Rồng Nhỏ và /").first
    if not label.count():
        raise RuntimeError("composer is not posting to both the page and Instagram")
    return label.inner_text().split(" và ", 1)[1].strip()


def compose(page: Page, entry: dict, dry: bool) -> None:
    at = dt.datetime.fromisoformat(entry["at"])
    caption = (ROOT / entry["caption_file"]).read_text(encoding="utf-8").strip()
    page.bring_to_front()
    page.goto("https://business.facebook.com/latest/composer", wait_until="commit", timeout=90000)
    page.get_by_text("Thêm ảnh/video").first.wait_for(timeout=60000)
    time.sleep(3)
    print(f"  posting to Rồng Nhỏ + Instagram {both_accounts(page)}", flush=True)

    with page.expect_file_chooser(timeout=20000) as fc:
        page.get_by_text("Thêm ảnh/video").first.click()
        time.sleep(1)
        upload = page.get_by_text("Tải lên từ máy tính")
        if upload.count():
            upload.first.click()
    fc.value.set_files([str(ROOT / p) for p in entry["images"]])
    time.sleep(6 + 2 * len(entry["images"]))

    box = page.locator("[contenteditable=true]").first
    box.click()
    page.keyboard.insert_text(caption)
    time.sleep(1.5)
    if caption.splitlines()[0][:20] not in box.inner_text():
        raise RuntimeError("caption did not take")

    toggle = page.get_by_text("Đặt ngày và giờ", exact=True)
    toggle.scroll_into_view_if_needed()
    toggle.locator("xpath=following::input[1]").click()
    time.sleep(2)
    # one date/time row per platform (Facebook, then Instagram): every row
    # must get the planned time, or Instagram posts at its default -- now
    dates = page.locator("input[placeholder='dd/mm/yyyy']")
    rows = dates.count()
    if rows < 2:
        raise RuntimeError(f"expected Facebook + Instagram schedule rows, found {rows}")
    for i in range(rows):
        d = dates.nth(i)
        d.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(at.strftime("%d/%m/%Y"))
        page.keyboard.press("Enter")
        time.sleep(1)
        for label, val in (("giờ", f"{at.hour:02d}"), ("phút", f"{at.minute:02d}")):
            f = page.locator(f"input[aria-label='{label}']").nth(i)
            f.click()
            page.keyboard.press("Control+A")
            page.keyboard.type(val)
            time.sleep(0.4)
        page.keyboard.press("Tab")
        time.sleep(1.5)
    for i in range(rows):
        shown = dates.nth(i).input_value()
        hh_el = page.locator("input[aria-label='giờ']").nth(i)
        mm_el = page.locator("input[aria-label='phút']").nth(i)
        hh = hh_el.input_value() or hh_el.get_attribute("aria-valuenow") or ""
        mm = mm_el.input_value() or mm_el.get_attribute("aria-valuenow") or ""
        if not (hh and mm):
            print("  time field html:", hh_el.evaluate("e => e.outerHTML.slice(0, 300)"), flush=True)
        if f"{at.day} " not in shown + " " and at.strftime("%d/%m/%Y") not in shown or str(at.year) not in shown:
            raise RuntimeError(f"row {i} date shows {shown!r}")
        if (int(hh), int(mm)) != (at.hour, at.minute):
            raise RuntimeError(f"row {i} time shows {hh}:{mm}")

    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{entry['series']}_{entry['name']}.png"))
    if dry:
        page.get_by_role("button", name="Hủy", exact=True).last.click()
        return
    page.get_by_role("button", name="Lên lịch", exact=True).last.click()
    end = time.time() + 120
    while time.time() < end:
        body = page.locator("body").inner_text()
        if "Đã lên lịch" in body or "đã được lên lịch" in body:
            return
        time.sleep(2)
    raise RuntimeError("no confirmation after scheduling")


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    now = dt.datetime.now()
    done = 0
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1600, "height": 1000})
        try:
            for entry in plan["posts"]:
                if entry.get("status") == "scheduled":
                    continue
                at = dt.datetime.fromisoformat(entry["at"])
                if at < now + MIN_AHEAD or at > now + WINDOW:
                    continue
                try:
                    compose(page, entry, args.dry_run)
                except Exception as e:  # noqa: BLE001 - report and move on
                    print(f"FAILED {entry['series']} {entry['name']}: {e}", flush=True)
                    if args.dry_run:
                        raise
                    continue
                if args.dry_run:
                    print(f"dry run ok: {entry['series']} {entry['name']} (screenshot in {SHOTS})")
                    return
                entry["status"] = "scheduled"
                entry["scheduled_on"] = dt.datetime.now().isoformat(timespec="seconds")
                save(plan)
                done += 1
                print(f"scheduled {entry['at'][:16]} {entry['series']} {entry['name']}", flush=True)
                if args.limit and done >= args.limit:
                    break
        finally:
            page.close()  # borrowed browser: close our tab only
    left = sum(1 for e in plan["posts"] if e.get("status") != "scheduled")
    print(f"{done} scheduled this run, {left} still to schedule")


if __name__ == "__main__":
    main()
