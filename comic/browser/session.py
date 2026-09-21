"""Browser session used for every acquisition.

Plain HTTP does not work: two of the four pilot sites return 403 to a bare fetch.
So pages are opened in a real Chromium, and page images are taken from the bytes
the browser *already received* rather than re-requested afterwards. That single
choice is what makes lazy loading, scroll-triggered loading and expiring CDN
tokens all work without per-site special cases.

The profile is persistent, so a site login survives between runs (``comic login``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Iterator

from comic.paths import browser_profile_dir

if TYPE_CHECKING:  # pragma: no cover - import cost only paid at type-check time
    from playwright.sync_api import Page, Response

log = logging.getLogger(__name__)

#: Content types we treat as candidate comic pages.
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/avif", "image/jpg")

#: Anything smaller than this is a tracking pixel, icon or spacer.
MIN_IMAGE_BYTES = 8_192

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserError(RuntimeError):
    """Raised when the browser cannot be started or a page cannot be opened."""


class BotChallengeError(BrowserError):
    """The host served an anti-bot interstitial instead of the page.

    Deliberately a distinct, terminal outcome. Defeating a bot challenge means
    circumventing an access control, which this project does not do; the
    supported route is to drive a browser the site has already cleared.
    """


#: Markers of an anti-bot interstitial. Matched against the page title and a
#: small slice of the body, not used to defeat anything -- only to explain.
_CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verifying you are human",
    "enable javascript and cookies to continue",
)


def looks_like_challenge(title: str, html: str) -> bool:
    haystack = f"{title}\n{html[:4000]}".lower()
    return any(marker in haystack for marker in _CHALLENGE_MARKERS)


@dataclass
class CapturedImage:
    """One image body observed on the wire."""

    url: str
    body: bytes
    content_type: str

    @property
    def size(self) -> int:
        return len(self.body)


@dataclass
class DomImage:
    """An ``<img>`` as laid out in the document."""

    url: str
    natural_width: int
    natural_height: int
    #: Absolute document coordinates, not viewport coordinates.
    x: float
    y: float
    width: float
    height: float

    @property
    def natural_area(self) -> int:
        return self.natural_width * self.natural_height


@dataclass
class PageHarvest:
    """Everything one chapter page yielded."""

    url: str
    title: str
    dom_images: list[DomImage] = field(default_factory=list)
    captured: dict[str, CapturedImage] = field(default_factory=dict)
    html: str = ""

    def body_for(self, url: str) -> bytes | None:
        hit = self.captured.get(url)
        return hit.body if hit else None


#: Collect every image element, with layout in absolute document space so the
#: ordering pass can sort by true reading position rather than DOM order.
_HARVEST_JS = """
() => {
  const out = [];
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc || img.src;
    if (!src || src.startsWith('data:')) continue;
    const r = img.getBoundingClientRect();
    out.push({
      url: src,
      natural_width: img.naturalWidth || 0,
      natural_height: img.naturalHeight || 0,
      x: r.left + window.scrollX,
      y: r.top + window.scrollY,
      width: r.width,
      height: r.height,
    });
  }
  return out;
}
"""

#: Find whatever element actually scrolls. Readers often scroll an inner div,
#: not the window -- measured on bilibili, whose document scrollHeight is 0
#: while 76 panels live inside a container. Scrolling the window there loads
#: nothing at all, silently.
_FIND_SCROLLER_JS = """
() => {
  const doc = document.scrollingElement || document.documentElement;
  if (doc && doc.scrollHeight > window.innerHeight + 100) return 'window';
  let best = null, bestArea = 0;
  for (const el of document.querySelectorAll('div,main,section,article')) {
    if (el.scrollHeight <= el.clientHeight + 100) continue;
    if (el.clientHeight < 200) continue;
    const style = getComputedStyle(el);
    if (!/auto|scroll|overlay/.test(style.overflowY)) continue;
    const area = el.clientWidth * el.clientHeight;
    if (area > bestArea) { bestArea = area; best = el; }
  }
  if (!best) return 'window';
  best.setAttribute('data-comic-scroller', '1');
  return 'element';
}
"""

#: Scroll whichever element was identified, and report progress.
_SCROLL_STEP_JS = """
(mode) => {
  const el = mode === 'element'
    ? document.querySelector('[data-comic-scroller]')
    : (document.scrollingElement || document.documentElement);
  if (!el) return {y: 0, h: 0, vh: window.innerHeight};
  const vh = mode === 'element' ? el.clientHeight : window.innerHeight;
  el.scrollTop = el.scrollTop + vh * 0.85;
  return {y: el.scrollTop, h: el.scrollHeight, vh};
}
"""

#: Nudge lazy-loaders that key off attributes rather than viewport intersection.
_UNLAZY_JS = """
() => {
  for (const img of document.querySelectorAll('img')) {
    for (const attr of ['data-src', 'data-original', 'data-lazy-src', 'data-url']) {
      const v = img.getAttribute(attr);
      if (v && !img.src.includes(v)) { img.src = v; }
    }
    img.loading = 'eager';
  }
}
"""


class BrowserSession:
    """Context manager wrapping a persistent Chromium context."""

    def __init__(
        self,
        headless: bool = True,
        profile_dir: Path | None = None,
        timeout_ms: int = 45_000,
        viewport: tuple[int, int] = (1440, 1000),
        cdp_url: str | None = None,
    ) -> None:
        self.headless = headless
        self.profile_dir = profile_dir or browser_profile_dir()
        self.timeout_ms = timeout_ms
        self.viewport = viewport
        #: When set, attach to an already-running Chrome instead of launching
        #: our own. Used for hosts that only serve a browser they have cleared.
        self.cdp_url = cdp_url
        self._pw = None
        self._context = None
        self._browser = None
        self._attached = False

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - covered by doctor
            raise BrowserError(
                "playwright is not installed. Run: pip install -e .[browser] "
                "&& python -m playwright install chromium"
            ) from exc

        self._pw = sync_playwright().start()

        if self.cdp_url:
            # Attach to a Chrome the operator already runs. We borrow a tab; we
            # never own the browser, so teardown must not close it.
            try:
                self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            except Exception as exc:
                self._pw.stop()
                self._pw = None
                raise BrowserError(
                    f"could not attach to Chrome at {self.cdp_url}: {exc}\n"
                    f"Start it first with: comic chrome"
                ) from exc
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else self._browser.new_context()
            self._attached = True
            self._context.set_default_timeout(self.timeout_ms)
            return self

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": self.viewport[0], "height": self.viewport[1]},
                user_agent=DEFAULT_UA,
                locale="vi-VN",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            self._pw.stop()
            self._pw = None
            raise BrowserError(f"could not start Chromium: {exc}") from exc
        self._context.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._attached:
            # Borrowed browser: disconnect only. Closing the context here would
            # shut down the operator's own Chrome windows.
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:  # pragma: no cover - best-effort teardown
                    pass
                self._browser = None
            self._context = None
        elif self._context is not None:
            try:
                self._context.close()
            except Exception:  # pragma: no cover - best-effort teardown
                pass
            self._context = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # pragma: no cover
                pass
            self._pw = None

    @property
    def context(self):
        if self._context is None:
            raise BrowserError("session is not started; use it as a context manager")
        return self._context

    # -- capture -----------------------------------------------------------
    def harvest(
        self,
        url: str,
        *,
        scroll: bool = True,
        max_scrolls: int = 400,
        settle_ms: int = 700,
        challenge_waits: int = 8,
    ) -> PageHarvest:
        """Open ``url``, force everything to load, and return images + layout."""
        page = self.context.new_page()
        captured: dict[str, CapturedImage] = {}

        def on_response(response: "Response") -> None:
            ctype = (response.headers or {}).get("content-type", "").split(";")[0].strip()
            if ctype not in IMAGE_TYPES:
                return
            try:
                body = response.body()
            except Exception:
                # Redirects, aborted requests and cache hits have no body. The
                # importer falls back to an in-page fetch for these.
                return
            if len(body) >= MIN_IMAGE_BYTES:
                captured[response.url] = CapturedImage(response.url, body, ctype)

        page.on("response", on_response)

        try:
            response = page.goto(url, wait_until="domcontentloaded")
            status = response.status if response is not None else 0

            page.wait_for_timeout(settle_ms)

            # A managed challenge can clear itself in a browser the host trusts,
            # so give it a moment before concluding anything.
            if status in (403, 503) or looks_like_challenge(page.title() or "", page.content()):
                for _ in range(challenge_waits):
                    page.wait_for_timeout(2_000)
                    if not looks_like_challenge(page.title() or "", page.content()):
                        break
                else:
                    raise BotChallengeError(
                        f"{url} is behind an anti-bot challenge that did not clear "
                        f"for this browser (HTTP {status or 'challenge'})."
                    )

            if status >= 400:
                raise BrowserError(f"{url} returned HTTP {status}")
            try:
                page.evaluate(_UNLAZY_JS)
            except Exception:
                pass

            if scroll:
                self._scroll_to_bottom(page, max_scrolls=max_scrolls, settle_ms=settle_ms)

            page.wait_for_timeout(settle_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                # A site with polling or video never goes idle; not fatal.
                pass

            raw = page.evaluate(_HARVEST_JS)
            harvest = PageHarvest(
                url=page.url,
                title=page.title() or "",
                dom_images=[DomImage(**item) for item in raw],
                captured=captured,
                html=page.content(),
            )
            return harvest
        finally:
            page.close()

    def _scroll_to_bottom(self, page: "Page", *, max_scrolls: int, settle_ms: int) -> None:
        """Step down a viewport at a time until the content stops growing.

        Scrolls whichever element actually scrolls -- the window on most sites,
        an inner container on others. Long-strip readers append as you go, so
        growth is the signal to keep going even once the bottom is reached.
        """
        mode = page.evaluate(_FIND_SCROLLER_JS)
        log.debug("scrolling via %s", mode)

        previous_height = -1
        stable_rounds = 0
        for _ in range(max_scrolls):
            state = page.evaluate(_SCROLL_STEP_JS, mode)
            page.wait_for_timeout(settle_ms)

            at_bottom = state["y"] + state["vh"] >= state["h"] - 50
            if state["h"] == previous_height and at_bottom:
                stable_rounds += 1
                if stable_rounds >= 3:
                    break
            else:
                stable_rounds = 0
            previous_height = state["h"]

        # Return to the top so a screenshot or re-harvest sees a sane state.
        page.evaluate(
            "(mode)=>{const el = mode==='element'"
            " ? document.querySelector('[data-comic-scroller]')"
            " : (document.scrollingElement||document.documentElement);"
            " if (el) el.scrollTop = 0;}",
            mode,
        )
        page.wait_for_timeout(200)

    def fetch_in_page(self, url: str, referer: str) -> bytes | None:
        """Re-request an image using the site's own cookies and referer.

        Fallback for images the response listener never saw -- typically served
        from the browser's memory cache on a repeat visit.
        """
        page = self.context.new_page()
        try:
            page.goto(referer, wait_until="domcontentloaded")
            result = page.evaluate(
                """
                async (u) => {
                  const r = await fetch(u, {credentials: 'include'});
                  if (!r.ok) return null;
                  const buf = new Uint8Array(await r.arrayBuffer());
                  return Array.from(buf);
                }
                """,
                url,
            )
            return bytes(result) if result else None
        except Exception:
            return None
        finally:
            page.close()

    def login(self, url: str, message: str = "") -> None:  # pragma: no cover - interactive
        """Open a visible window and block until the operator closes it."""
        page = self.context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        if message:
            log.info(message)
        # Block until the human closes the tab or the whole window.
        closed = {"value": False}
        page.on("close", lambda _: closed.__setitem__("value", True))
        while not closed["value"]:
            try:
                page.wait_for_timeout(500)
            except Exception:
                break


def iter_image_candidates(harvest: PageHarvest) -> Iterator[DomImage]:
    """DOM images that carry a usable natural size."""
    for image in harvest.dom_images:
        if image.natural_width > 0 and image.natural_height > 0:
            yield image
