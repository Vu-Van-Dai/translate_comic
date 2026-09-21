"""Screen-capture acquisition.

For readers that do not expose pages as fetchable images -- canvas renderers,
WebGL, anything painted rather than served -- pages are captured the way a
person would: photograph what the browser has already drawn on screen.

This deliberately does **not** read pixels out of a canvas via ``toDataURL`` and
does not touch any decoding the page performs. It screenshots rendered elements,
at screen fidelity, in a browser the operator is already viewing. Where a site
protects its content, that protection is left intact; see CLAUDE.md.

Each visual element becomes one page, so no stitching or seam-guessing is needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from comic.browser.session import BrowserSession, looks_like_challenge
from comic.paths import ChapterPaths, page_filename
from comic.schemas import PageRecord

log = logging.getLogger(__name__)

#: Elements smaller than this in either axis are chrome, not comic panels.
MIN_PANEL_WIDTH = 400
MIN_PANEL_HEIGHT = 200

#: Selectors tried in order when the caller does not name one.
AUTO_SELECTORS = ("canvas", "img")

#: A painted panel has many distinct colours. An unpainted canvas placeholder
#: has one or two. Measured on a real virtualised reader: blank captures came
#: back at exactly 2 colours and ~6 KB, artwork at 3000-8000 colours.
MIN_DISTINCT_COLOURS = 24


class CaptureError(RuntimeError):
    """Raised when nothing capturable could be found."""


@dataclass
class CaptureReport:
    pages: list[PageRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    selector: str = ""
    title: str = ""
    #: Rasterisation multiple actually achieved (1.0 if the override failed).
    scale: float = 1.0
    #: Elements that never painted before the timeout.
    blank: int = 0

    @property
    def count(self) -> int:
        return len(self.pages)


#: Measure candidates without screenshotting, so a bad selector fails fast.
_MEASURE_JS = """
(selector) => {
  const out = [];
  document.querySelectorAll(selector).forEach((el, i) => {
    const r = el.getBoundingClientRect();
    out.push({
      index: i,
      w: Math.round(r.width),
      h: Math.round(r.height),
      y: Math.round(r.top + window.scrollY),
    });
  });
  return out;
}
"""


def choose_selector(page, explicit: str | None = None) -> tuple[str, list[dict]]:
    """Pick the selector whose elements look most like comic panels."""
    if explicit:
        found = page.evaluate(_MEASURE_JS, explicit)
        usable = [e for e in found
                  if e["w"] >= MIN_PANEL_WIDTH and e["h"] >= MIN_PANEL_HEIGHT]
        if not usable:
            raise CaptureError(
                f"selector {explicit!r} matched {len(found)} element(s), none of "
                f"panel size (>= {MIN_PANEL_WIDTH}x{MIN_PANEL_HEIGHT})"
            )
        return explicit, usable

    best: tuple[str, list[dict]] | None = None
    for selector in AUTO_SELECTORS:
        found = page.evaluate(_MEASURE_JS, selector)
        usable = [e for e in found
                  if e["w"] >= MIN_PANEL_WIDTH and e["h"] >= MIN_PANEL_HEIGHT]
        if usable and (best is None or len(usable) > len(best[1])):
            best = (selector, usable)
    if best is None:
        raise CaptureError(
            "found nothing of panel size to capture. Pass --selector with the "
            "element that holds the artwork."
        )
    return best


def set_scale(page, scale: float) -> bool:
    """Raise the capture resolution above CSS pixel size.

    A reader lays pages out at whatever width its layout dictates -- often well
    below the artwork's native resolution. Screenshotting at 1x therefore
    throws away detail that OCR needs. Overriding the device scale factor makes
    the browser rasterise at a multiple of that, which is the difference between
    legible and unreadable small lettering.
    """
    if scale <= 1.0:
        return False
    try:
        cdp = page.context.new_cdp_session(page)
        metrics = page.evaluate(
            "()=>({w: window.innerWidth, h: window.innerHeight})"
        )
        cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": metrics["w"],
                "height": metrics["h"],
                "deviceScaleFactor": scale,
                "mobile": False,
            },
        )
        return True
    except Exception as exc:
        log.warning("could not raise capture scale: %s", exc)
        return False


def _looks_painted(data: bytes) -> bool:
    """Whether a screenshot actually contains artwork.

    Long readers virtualise: with dozens of canvases spread over ~100k pixels of
    scroll, only those near the viewport are painted. Screenshotting the rest
    yields a uniform placeholder that no later stage can distinguish from a
    genuinely blank page, so the emptiness is caught here, at capture time.
    """
    import io

    import numpy as np
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            arr = np.asarray(im.convert("RGB"))
    except Exception:
        return False
    sample = arr[::4, ::4].reshape(-1, 3)
    return len(np.unique(sample, axis=0)) >= MIN_DISTINCT_COLOURS


def _screenshot_when_painted(page, handle, settle_ms: int, attempts: int) -> bytes | None:
    """Screenshot an element once it has actually rendered."""
    handle.scroll_into_view_if_needed(timeout=10_000)
    data = None
    for attempt in range(attempts):
        page.wait_for_timeout(settle_ms * (1 + attempt))
        data = handle.screenshot(type="png")
        if _looks_painted(data):
            return data
        # Nudge the viewport: some readers only paint on a scroll event.
        page.evaluate("()=>window.scrollBy(0, 1)")
        page.evaluate("()=>window.scrollBy(0, -1)")
    return None


#: Drive a paged reader by its own page slider.
#:
#: Measured on bilibili: the toolbar arrow is CHAPTER navigation and walks out
#: of the chapter entirely, while the slider addresses a view directly and
#: cannot overshoot. Assigning `.value` is swallowed by the page's framework,
#: so the native setter has to be invoked before the input event is believed.
_SLIDER_JS = """
(target) => {
  const input = [...document.querySelectorAll('input[type=range]')]
    .find((i) => Number(i.max || 0) >= 2);
  if (!input) return null;
  const at = Number(input.value || 0), max = Number(input.max || 0);
  if (target === null) return { at, max };
  if (target > max) return { at, max, moved: false };
  const proto = Object.getPrototypeOf(input);
  const d = Object.getOwnPropertyDescriptor(proto, 'value')
         || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  if (d && d.set) d.set.call(input, String(target)); else input.value = String(target);
  for (const t of ['input', 'change']) {
    input.dispatchEvent(new Event(t, { bubbles: true }));
  }
  return { at: target, max, moved: true };
}
"""


def slider_state(page) -> dict | None:
    """Current view and total, when the reader exposes a page slider."""
    return page.evaluate(_SLIDER_JS, None)


def goto_view(page, target: int) -> bool:
    result = page.evaluate(_SLIDER_JS, target)
    return bool(result and result.get("moved"))


def capture_paged(
    url: str,
    paths: ChapterPaths,
    session: BrowserSession,
    selector: str | None = None,
    settle_ms: int = 900,
    start_at: int = 1,
) -> CaptureReport:
    """Walk a paged reader view by view, photographing each one.

    Screenshots are taken of the panel element itself rather than of the
    viewport, which keeps the reader's own furniture out of the picture without
    having to hide anything.
    """
    page = session.context.new_page()
    report = CaptureReport()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(settle_ms)

        for _ in range(15):
            if not looks_like_challenge(page.title() or "", page.content()):
                break
            page.wait_for_timeout(2_000)
        report.title = page.title() or ""

        # Poll rather than guess a fixed wait: the reader takes several seconds
        # to build its controls, and a short wait reads "no slider" on a reader
        # that has one.
        state = None
        for _ in range(20):
            state = slider_state(page)
            if state and int(state.get("max", 0)) >= 2:
                break
            page.wait_for_timeout(1_000)
        if not state or int(state.get("max", 0)) < 2:
            raise CaptureError(
                "this reader has no page slider; use the long-strip path instead"
            )
        total = int(state["max"])
        paths.original.mkdir(parents=True, exist_ok=True)

        number = start_at - 1
        seen: set[str] = set()

        for view in range(1, total + 1):
            if view > 1 and not goto_view(page, view):
                report.skipped.append(f"view {view}: slider would not move")
                break
            page.wait_for_timeout(settle_ms)

            chosen, elements = choose_selector(page, selector)
            report.selector = chosen
            handles = page.query_selector_all(chosen)
            elements.sort(key=lambda e: e["y"])

            for entry in elements:
                handle = handles[entry["index"]]
                try:
                    handle.scroll_into_view_if_needed(timeout=8_000)
                    page.wait_for_timeout(200)
                    data = handle.screenshot(type="png")
                except Exception as exc:
                    report.skipped.append(f"view {view}: {exc}")
                    continue

                import hashlib

                digest = hashlib.sha256(data).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)

                number += 1
                filename = page_filename(number, ".png")
                (paths.original / filename).write_bytes(data)
                report.pages.append(
                    PageRecord(
                        page=number, filename=filename,
                        width=entry["w"], height=entry["h"],
                        source_url=f"{url}#view{view}",
                    )
                )

        if not report.pages:
            raise CaptureError("no views could be captured")
        return report
    finally:
        page.close()


def capture_chapter(
    url: str,
    paths: ChapterPaths,
    session: BrowserSession,
    selector: str | None = None,
    settle_ms: int = 600,
    challenge_waits: int = 15,
    scale: float = 2.0,
    paint_attempts: int = 5,
) -> CaptureReport:
    """Open ``url`` and save one image per rendered panel."""
    page = session.context.new_page()
    report = CaptureReport()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(settle_ms)

        # The operator may need to clear an interstitial in their own window.
        for _ in range(challenge_waits):
            if not looks_like_challenge(page.title() or "", page.content()):
                break
            page.wait_for_timeout(2_000)

        report.title = page.title() or ""

        # Raise rasterisation resolution before anything is drawn or measured.
        report.scale = scale if set_scale(page, scale) else 1.0

        # A full pass primes lazy rendering before anything is measured.
        page.evaluate("()=>window.scrollTo(0, 0)")
        height = page.evaluate("()=>document.body.scrollHeight")
        step = page.evaluate("()=>window.innerHeight")
        position = 0
        while position < height:
            page.evaluate("(y)=>window.scrollTo(0, y)", position)
            page.wait_for_timeout(settle_ms)
            position += step
            height = page.evaluate("()=>document.body.scrollHeight")
        page.evaluate("()=>window.scrollTo(0, 0)")
        page.wait_for_timeout(settle_ms)

        chosen, elements = choose_selector(page, selector)
        report.selector = chosen
        elements.sort(key=lambda e: e["y"])

        paths.original.mkdir(parents=True, exist_ok=True)
        handles = page.query_selector_all(chosen)

        number = 0
        for entry in elements:
            handle = handles[entry["index"]]
            try:
                data = _screenshot_when_painted(
                    page, handle, settle_ms=settle_ms, attempts=paint_attempts
                )
            except Exception as exc:
                report.skipped.append(f"element {entry['index']}: {exc}")
                continue
            if data is None:
                report.skipped.append(
                    f"element {entry['index']}: never painted (still blank)"
                )
                report.blank += 1
                continue

            number += 1
            filename = page_filename(number, ".png")
            (paths.original / filename).write_bytes(data)
            report.pages.append(
                PageRecord(
                    page=number,
                    filename=filename,
                    width=entry["w"],
                    height=entry["h"],
                    source_url=f"{url}#{chosen}[{entry['index']}]",
                )
            )

        if not report.pages:
            raise CaptureError("every candidate element failed to capture")
        return report
    finally:
        page.close()
