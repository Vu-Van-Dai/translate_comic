"""The acquisition stage.

Takes a chapter URL to ordered, zero-padded page files on disk. Source filenames
are always discarded (spec 9) -- ``image_48274.png`` tells you nothing about
reading order, and some sites deliberately shuffle them.

Original bytes are written verbatim rather than re-encoded, so the archived page
is exactly what the site served. Format conversion happens later, once.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from comic.browser.session import BrowserSession, DomImage, PageHarvest
from comic.downloader.adapters import SiteAdapter, adapter_for
from comic.downloader.selection import SelectionResult, select_pages
from comic.paths import ChapterPaths, page_filename
from comic.schemas import PageRecord

log = logging.getLogger(__name__)

#: Pillow format name -> file extension we store it under.
_EXTENSIONS = {
    "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
    "GIF": ".gif", "BMP": ".bmp", "AVIF": ".avif",
}

#: A real comic page is bigger than this in both dimensions.
MIN_PAGE_DIMENSION = 200


class ImportError_(RuntimeError):
    """Raised when a chapter cannot be acquired."""


class ChapterLockedError(ImportError_):
    """The chapter is not accessible to this account.

    Raised rather than attempting any workaround: locked means locked.
    """


@dataclass
class ImportReport:
    pages: list[PageRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    selection: SelectionResult | None = None
    title: str = ""

    @property
    def count(self) -> int:
        return len(self.pages)


def sniff_image(data: bytes) -> tuple[str, int, int] | None:
    """(extension, width, height) if ``data`` decodes as an image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if fmt not in _EXTENSIONS:
        return None
    return _EXTENSIONS[fmt], width, height


def detect_locked(harvest: PageHarvest, adapter: SiteAdapter) -> bool:
    """Whether the reader is showing a purchase/unlock prompt instead of pages."""
    if not adapter.locked_selector:
        return False
    # The selector list is checked against the captured HTML rather than by
    # re-querying, so this works on a fixture replay too.
    html = harvest.html.lower()
    for selector in adapter.locked_selector.split(","):
        token = selector.strip().strip(".#[]").split("=")[-1].strip("'\"")
        if token and token.lower() in html:
            return True
    return False


def resolve_bytes(
    image: DomImage,
    harvest: PageHarvest,
    session: BrowserSession | None,
) -> bytes | None:
    """Bytes for one page: from the wire capture, else a re-fetch in the page."""
    body = harvest.body_for(image.url)
    if body:
        return body
    if session is None:
        return None
    log.debug("re-fetching %s (not seen on the wire)", image.url)
    return session.fetch_in_page(image.url, referer=harvest.url)


#: Below this many DOM-selected pages, try the network capture instead.
NETWORK_FALLBACK_BELOW = 3


def select_from_network(harvest: PageHarvest) -> list[tuple[str, bytes]]:
    """Build the page list from captured responses rather than the DOM.

    Some readers paint pages onto a canvas while still fetching them as ordinary
    images. The DOM then holds nothing to select, but the bytes the browser
    received are perfectly good pages -- which is what the response capture was
    for in the first place.

    Grouping is by (host, rounded size): chapter pages share a CDN and a width,
    while ads, avatars and UI art do not. Order is arrival order, which matches
    reading order because the harvester scrolls strictly top to bottom.
    """
    from collections import defaultdict

    groups: dict[tuple[str, int, int], list[tuple[str, bytes]]] = defaultdict(list)
    seen: set[str] = set()

    for url, captured in harvest.captured.items():
        # blob: URLs mirror a real response; keep one copy of each payload.
        digest = hashlib.sha256(captured.body).hexdigest()
        if digest in seen:
            continue
        sniffed = sniff_image(captured.body)
        if sniffed is None:
            continue
        _ext, width, height = sniffed
        if width < 400 or height < 400:
            continue
        seen.add(digest)
        host = urlparse(url).hostname or "?"
        # Round dimensions so minor per-page variation still groups together.
        key = (host, width, round(height / 100))
        groups[key].append((url, captured.body))

    if not groups:
        return []
    # Most pixels wins, so a handful of large ads cannot outvote the chapter.
    best = max(groups.values(), key=lambda g: sum(len(b) for _u, b in g))
    return best


def _import_from_bytes(
    items: list[tuple[str, bytes]], paths: ChapterPaths, report: ImportReport
) -> ImportReport:
    """Write already-fetched payloads as ordered pages."""
    paths.original.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}
    number = 0

    for url, data in items:
        sniffed = sniff_image(data)
        if sniffed is None:
            report.skipped.append(f"{url} (not a decodable image)")
            continue
        extension, width, height = sniffed
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            report.warnings.append(f"duplicate of page {seen[digest]:03d} skipped")
            continue

        number += 1
        seen[digest] = number
        filename = page_filename(number, extension)
        (paths.original / filename).write_bytes(data)
        report.pages.append(
            PageRecord(page=number, filename=filename, width=width,
                       height=height, source_url=url, sha256=digest)
        )

    if not report.pages:
        raise ImportError_("no usable pages among the captured responses")
    return report


def import_pages(
    harvest: PageHarvest,
    adapter: SiteAdapter,
    paths: ChapterPaths,
    session: BrowserSession | None = None,
) -> ImportReport:
    """Select, validate, order and write a chapter's pages."""
    report = ImportReport(title=harvest.title)

    if detect_locked(harvest, adapter):
        raise ChapterLockedError(
            "this chapter is locked or not unlocked on the signed-in account. "
            "Unlock it in your own browser first; the importer will not attempt "
            "to work around the restriction."
        )

    selection = select_pages(harvest.dom_images, adapter)
    report.selection = selection

    if len(selection.pages) < NETWORK_FALLBACK_BELOW:
        network = select_from_network(harvest)
        if len(network) >= max(NETWORK_FALLBACK_BELOW, len(selection.pages) + 1):
            report.warnings.append(
                f"DOM held {len(selection.pages)} page(s); used {len(network)} "
                f"captured image response(s) instead"
            )
            return _import_from_bytes(network, paths, report)

    if not selection.pages:
        reasons = ", ".join(f"{k}: {v}" for k, v in selection.reasons().items())
        if adapter.requires_login:
            raise ChapterLockedError(
                "no page images found on a login-required site. You are probably "
                f"signed out -- run: comic login --site {adapter.match}"
                + (f" (rejected -- {reasons})" if reasons else "")
            )
        raise ImportError_(
            f"no page images found among {len(harvest.dom_images)} images"
            + (f" (rejected -- {reasons})" if reasons else "")
        )

    paths.original.mkdir(parents=True, exist_ok=True)
    seen_hashes: dict[str, int] = {}
    page_number = 0

    for image in selection.pages:
        data = resolve_bytes(image, harvest, session)
        if not data:
            report.skipped.append(f"{image.url} (no bytes available)")
            continue

        sniffed = sniff_image(data)
        if sniffed is None:
            report.skipped.append(f"{image.url} (not a decodable image)")
            continue
        extension, width, height = sniffed

        if width < MIN_PAGE_DIMENSION or height < MIN_PAGE_DIMENSION:
            report.skipped.append(f"{image.url} ({width}x{height} too small)")
            continue

        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            report.warnings.append(
                f"duplicate of page {seen_hashes[digest]:03d} skipped: {image.url}"
            )
            continue

        page_number += 1
        seen_hashes[digest] = page_number
        filename = page_filename(page_number, extension)
        (paths.original / filename).write_bytes(data)

        report.pages.append(
            PageRecord(
                page=page_number,
                filename=filename,
                width=width,
                height=height,
                source_url=image.url,
                sha256=digest,
            )
        )

    if not report.pages:
        raise ImportError_(
            "every candidate page failed validation: "
            + "; ".join(report.skipped[:5])
        )
    return report


def download_chapter(
    url: str,
    paths: ChapterPaths,
    adapter: SiteAdapter | None = None,
    headless: bool = True,
    cdp_url: str | None = None,
) -> ImportReport:
    """Open ``url`` in a browser and acquire the whole chapter.

    ``cdp_url`` attaches to an already-running Chrome instead of launching one,
    for hosts that only serve a browser they have already cleared.
    """
    site = adapter or adapter_for(url)
    with BrowserSession(headless=headless, cdp_url=cdp_url) as session:
        harvest = session.harvest(url, settle_ms=site.settle_ms)
        return import_pages(harvest, site, paths, session=session)


def verify_pages(paths: ChapterPaths, records: list[PageRecord]) -> list[str]:
    """Re-open every written page. Cheap insurance against a truncated write."""
    problems = []
    for record in records:
        path = paths.original / record.filename
        if not path.exists():
            problems.append(f"{record.filename} is missing")
            continue
        try:
            with Image.open(path) as img:
                img.verify()
        except (UnidentifiedImageError, OSError) as exc:
            problems.append(f"{record.filename} does not decode: {exc}")
    return problems
