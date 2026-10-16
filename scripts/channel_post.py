"""Schedule the planned channel videos on TikTok Studio and Meta Business Suite.

    python scripts/channel_post.py [--platform tiktok|facebook|both] [--limit N]

Reads workspace/_logs/channel_schedule.json (made by channel_schedule.py) and,
through the operator's project Edge (``comic chrome --edge --port 9223``,
signed in to TikTok and to Facebook), schedules every pending post the
platform's own scheduler accepts -- TikTok takes up to 30 days ahead (measured 2026-10-15), Meta
Business Suite ~29 -- using the platforms' "Schedule" option, exactly as the
operator would by hand. Each success is written back to the plan at once, so
the script is safe to stop and re-run; run it again every week or so until
the plan is exhausted. Nothing here starts on its own.

Measured on the live pages (2026-10-15):
- TikTok Studio upload: one ``input[type=file]``; description is the first
  contenteditable; "Lên lịch" radio; time/date are pickers (5-minute steps,
  ``span.tiktok-timepicker-left/right``; ``.calendar-wrapper`` days); the
  submit button is "Lên lịch".
- Business Suite reels composer (page Rồng Nhỏ): "Thêm video" opens a file
  chooser; description contenteditable; "Tiếp" twice; "Lên lịch" tab; date
  ``input[placeholder=dd/mm/yyyy]``, ``input[aria-label=giờ|phút]``
  (Asia/Jakarta = UTC+7); submit "Lên lịch"; confirm "Xong".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "workspace" / "_logs" / "channel_schedule.json"
WINDOW = {"tiktok": dt.timedelta(days=29, hours=20), "facebook": dt.timedelta(days=28, hours=20),
          "youtube": dt.timedelta(days=360)}
MIN_AHEAD = dt.timedelta(minutes=30)
MONTHS_VI = {m: i for i, m in enumerate(["Một", "Hai", "Ba", "Tư", "Năm", "Sáu", "Bảy", "Tám", "Chín", "Mười",
                                          "Mười Một", "Mười Hai"], 1)}


def save(plan: dict) -> None:
    tmp = PLAN.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PLAN)


def wait_text(page: Page, text: str, timeout: float = 180) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if text in page.locator("body").inner_text():
            return
        time.sleep(2)
    raise TimeoutError(f"'{text}' never appeared")


# ----------------------------------------------------------------- TikTok
def tiktok(page: Page, entry: dict) -> None:
    at = dt.datetime.fromisoformat(entry["tiktok"]["at"])
    page.goto("https://www.tiktok.com/tiktokstudio/upload?from=webapp", wait_until="domcontentloaded", timeout=90000)
    page.locator("input[type=file]").first.wait_for(state="attached", timeout=60000)
    page.locator("input[type=file]").first.set_input_files(entry["video"])
    wait_text(page, "Đã tải lên", 300)
    time.sleep(2)
    for dismiss in ("Đã hiểu", "Hủy bỏ thông báo"):
        try:
            page.get_by_text(dismiss, exact=True).click(timeout=1500)
        except Exception:  # noqa: BLE001 - only present sometimes
            pass
    ed = page.locator(".public-DraftEditor-content, [contenteditable=true]").first
    got = ""
    for _ in range(4):                     # focus sometimes misses the editor
        ed.scroll_into_view_if_needed()
        ed.click()
        ed.focus()
        time.sleep(0.5)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(entry["caption"])
        time.sleep(1.5)
        page.keyboard.press("Escape")
        got = ed.inner_text().strip()
        if "#spoiler" in got:
            break
        time.sleep(2)
    if "#review" not in got or "#spoiler" not in got:
        raise RuntimeError(f"caption did not take: {got[:80]!r}")
    page.get_by_text("Lên lịch", exact=True).first.click()
    time.sleep(1.5)
    inputs = page.locator("input.TUXTextInputCore-input")
    # date: open the calendar, step months forward, click the day
    inputs.nth(1).click()
    time.sleep(1)
    cal = page.locator(".calendar-wrapper")
    for _ in range(3):
        head = cal.inner_text().split("\n")[0]
        month = MONTHS_VI.get(head.split("/")[0].replace("Tháng", "").strip().replace("\xa0", " ").strip())
        if month == at.month:
            break
        cal.locator("[class*=arrow]").last.click()
        time.sleep(0.8)
    # The grid also shows the neighbouring months' days, greyed: early days of
    # the next month at the end, late days of the previous one at the start.
    days = cal.get_by_text(str(at.day), exact=True)
    (days.first if at.day < 15 else days.last).click()
    time.sleep(1)
    inputs.nth(0).click()
    time.sleep(1)
    h = page.locator("span.tiktok-timepicker-left", has_text=f"{at.hour:02d}").first
    h.scroll_into_view_if_needed()
    h.click()
    time.sleep(0.4)
    m = page.locator("span.tiktok-timepicker-right", has_text=f"{at.minute:02d}").first
    m.scroll_into_view_if_needed()
    m.click()
    time.sleep(0.4)
    page.mouse.click(900, 470)
    time.sleep(1)
    want = (at.strftime("%H:%M"), at.strftime("%Y-%m-%d"))
    got = (inputs.nth(0).input_value(), inputs.nth(1).input_value())
    if got != want:
        raise RuntimeError(f"tiktok picker shows {got}, wanted {want}")
    wait_text(page, "Không phát hiện vấn đề nào", 120)   # music copyright check done
    page.get_by_role("button", name="Lên lịch", exact=True).last.click()
    end = time.time() + 120
    while time.time() < end and "tiktokstudio/upload" in page.url and "Bài đăng" not in page.locator("body").inner_text():
        time.sleep(2)
    if "Bài đăng" not in page.locator("body").inner_text() and "content" not in page.url:
        raise RuntimeError("tiktok: no confirmation after submit")


# --------------------------------------------------------------- Facebook
def facebook(page: Page, entry: dict) -> None:
    at = dt.datetime.fromisoformat(entry["facebook"]["at"])
    page.goto("https://business.facebook.com/latest/reels_composer", wait_until="domcontentloaded", timeout=90000)
    page.get_by_text("Thêm video", exact=True).wait_for(timeout=60000)
    with page.expect_file_chooser(timeout=20000) as fc:
        page.get_by_text("Thêm video", exact=True).click()
    fc.value.set_files(entry["video"])
    page.get_by_text("Mô tả", exact=False).first.wait_for(timeout=120000)
    time.sleep(8)
    box = page.locator("[contenteditable=true]").first
    box.click()
    page.keyboard.insert_text(entry["caption"])
    time.sleep(1.5)
    page.keyboard.press("Escape")
    if "#spoiler" not in box.inner_text():
        raise RuntimeError("facebook caption did not take")
    for _ in range(2):
        page.get_by_role("button", name="Tiếp", exact=True).last.click()
        time.sleep(4)
    page.get_by_text("Lên lịch", exact=True).first.click()
    time.sleep(2)
    date = page.locator("input[placeholder='dd/mm/yyyy']").first
    date.click()
    page.keyboard.press("Control+A")
    page.keyboard.type(at.strftime("%d/%m/%Y"))
    page.keyboard.press("Enter")
    time.sleep(1)
    for label, val in (("giờ", f"{at.hour:02d}"), ("phút", f"{at.minute:02d}")):
        f = page.locator(f"input[aria-label='{label}']").first
        f.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(val)
        time.sleep(0.4)
    page.keyboard.press("Tab")
    time.sleep(1)
    shown = date.input_value()
    if f"{at.day} " not in shown + " " or str(at.year) not in shown:
        raise RuntimeError(f"facebook date shows {shown!r}")
    page.get_by_role("button", name="Lên lịch", exact=True).last.click()
    page.get_by_text("Đã lên lịch đăng thước phim").wait_for(timeout=180000)
    page.get_by_role("button", name="Xong").click()
    time.sleep(2)


# ---------------------------------------------------------------- YouTube
YT_STUDIO = "https://studio.youtube.com/channel/UC8doy0uehsgE57i1IE527Hw/videos/upload?d=ud"


def youtube(page: Page, entry: dict) -> None:
    """Studio upload dialog (measured 2026-10-15, channel Rồng Nhỏ): title and
    description are the two ``#textbox``es, "not made for kids" radio, three
    "Tiếp", then the visibility page's "Lên lịch" section: a date input inside
    ``ytcp-date-picker`` ("21 thg 10, 2026"), ``#time-of-day-container input``
    ("12:15"); timezone defaults to local GMT+7. ``#done-button`` -> "Đã lên
    lịch cho video". The dialog opens on *Public*: never press done before the
    schedule is set."""
    at = dt.datetime.fromisoformat(entry["youtube"]["at"])
    page.goto(YT_STUDIO, wait_until="domcontentloaded", timeout=90000)
    page.locator("input[type=file]").first.wait_for(state="attached", timeout=60000)
    page.locator("input[type=file]").first.set_input_files(entry["video"])
    page.locator("#textbox").nth(1).wait_for(timeout=120000)
    time.sleep(4)
    # The channel's daily upload limit (hit 2026-10-15 after ~110 uploads in a day):
    # the dialog greys out with "Đã đạt giới hạn tải video lên hằng ngày".
    if re.search(r"giới hạn tải|daily upload limit", page.locator("body").inner_text(), re.I):
        raise RuntimeError("youtube daily upload limit")
    title = entry.get("title") or entry["caption"].splitlines()[0]
    tb = page.locator("#textbox")
    tb.nth(0).click(); page.keyboard.press("Control+A"); page.keyboard.insert_text(title[:100])
    tb.nth(1).click(); page.keyboard.press("Control+A"); page.keyboard.insert_text(entry["caption"])
    time.sleep(1)
    if "#spoiler" not in tb.nth(1).inner_text():
        raise RuntimeError("youtube description did not take")
    page.locator("tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]").click()
    for _ in range(3):
        page.locator("#next-button").click(); time.sleep(2.5)
    page.get_by_text("Lên lịch", exact=True).first.click(); time.sleep(1.5)
    want = f"{at.day} thg {at.month}, {at.year}"
    page.locator("#datepicker-trigger").click(); time.sleep(1.2)
    ins = page.locator("ytcp-date-picker input")
    box = [ins.nth(i) for i in range(ins.count()) if ins.nth(i).is_visible() and "thg" in (ins.nth(i).input_value() or "")]
    box[0].fill(want); box[0].press("Enter"); time.sleep(1.2)
    t = page.locator("#time-of-day-container input")
    t.click(); page.keyboard.press("Control+A"); page.keyboard.type(at.strftime("%H:%M")); page.keyboard.press("Enter")
    time.sleep(1)
    shown = (page.locator("#datepicker-trigger").inner_text().strip(), t.input_value())
    if shown != (want, at.strftime("%H:%M")):
        raise RuntimeError(f"youtube picker shows {shown}, wanted {(want, at.strftime('%H:%M'))}")
    def press_done() -> None:
        try:
            page.locator("#done-button").click(timeout=8000)
        except Exception:  # noqa: BLE001 - disabled while a dialog is up; the loop retries
            pass

    def confirmed() -> bool:
        # Two confirmations (2026-10-15): "Đã lên lịch cho video", or -- while SD
        # processing still runs -- "Xử lý video ... sẽ chuyển sang chế độ công khai vào".
        for text in ("Đã lên lịch cho video", "Xử lý video"):
            loc = page.get_by_text(text, exact=True)
            if loc.count() and loc.first.is_visible():
                return True
        return False

    press_done()
    # While YouTube's copyright check still runs, "Chúng tôi vẫn đang kiểm tra nội
    # dung của bạn" pops up with "Đã hiểu" and nothing is scheduled until done is
    # pressed again (two uploads were left as drafts this way).
    for _ in range(90):
        time.sleep(2)
        if confirmed():
            break
        ok = page.get_by_role("button", name="Đã hiểu")
        if ok.count() and ok.first.is_visible():
            ok.first.click(); time.sleep(1.5)
            press_done()
    else:
        raise TimeoutError("youtube: no schedule confirmation -- check Studio drafts before retrying")
    link = page.locator("a[href*='youtube.com/shorts'], a[href*='youtu.be']").first
    try:
        entry["youtube"]["url"] = link.get_attribute("href", timeout=3000)
    except Exception:  # noqa: BLE001 - the link is a nicety
        pass
    page.get_by_role("button", name="Đóng").last.click()
    time.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "facebook", "youtube", "both", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="stop after N posts (0 = all in window)")
    args = ap.parse_args()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    platforms = {"both": ["tiktok", "facebook"], "all": ["tiktok", "facebook", "youtube"]}.get(
        args.platform, [args.platform])
    now = dt.datetime.now()
    done = 0
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        # Upload tabs left by an interrupted run raise "leave this page?" on
        # navigation, which crashed the driver once; close them first.
        for stale in ctx.pages:
            if "tiktokstudio" in stale.url or "reels_composer" in stale.url or "videos/upload" in stale.url:
                stale.close(run_before_unload=False)
        page = ctx.new_page()

        def on_dialog(d):
            try:
                d.accept()
            except Exception:  # noqa: BLE001 - already handled elsewhere
                pass

        page.on("dialog", on_dialog)
        page.set_viewport_size({"width": 1400, "height": 900})
        try:
            for plat in platforms:
                for e in plan["entries"]:
                    slot = e.get(plat)
                    if slot is None:          # older entries were planned before YouTube joined
                        continue
                    at = dt.datetime.fromisoformat(slot["at"])
                    if slot["status"] == "scheduled" or at - now > WINDOW[plat]:
                        continue
                    if at - now < MIN_AHEAD:
                        slot["status"] = "missed"
                        save(plan)
                        print(f"{plat}: {slot['at']} already passed -- missed", flush=True)
                        continue
                    try:
                        {"tiktok": tiktok, "facebook": facebook, "youtube": youtube}[plat](page, e)
                        slot["status"] = "scheduled"
                        slot["done"] = dt.datetime.now().isoformat(timespec="seconds")
                        slot.pop("error", None)
                        print(f"{plat}: {slot['at']} {e['folder']}", flush=True)
                        done += 1
                    except Exception as exc:  # noqa: BLE001 - record and carry on
                        slot["error"] = repr(exc)[:300]
                        print(f"{plat}: FAILED {slot['at']} {e['folder']}: {exc}", flush=True)
                        page.screenshot(path=str(PLAN.with_name(f"channel_fail_{plat}.png")))
                        body = page.locator("body").inner_text()
                        if plat == "youtube" and re.search(r"giới hạn tải|upload limit|daily upload", body, re.I):
                            print("youtube: daily upload limit reached -- stop; re-run tomorrow", flush=True)
                            save(plan)
                            break
                    save(plan)
                    if args.limit and done >= args.limit:
                        return
                    time.sleep(5)
        finally:
            page.close()
    left = {p: sum(1 for e in plan["entries"] if p in e and e[p]["status"] != "scheduled")
            for p in ("tiktok", "facebook", "youtube")}
    print(f"{done} scheduled this run; still to schedule: {left}")


if __name__ == "__main__":
    main()
