"""Site adapters.

Site support is data, not code. An adapter is a YAML file of *hints* that steer
the generic importer -- it never does the extraction itself. That keeps a site
change to a one-line edit instead of a rewrite, and means an unknown host still
works via the pure-generic defaults.

Write a Python adapter only when YAML genuinely cannot express a site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

from comic.schemas import PageKind, ReadingDirection

SITES_DIR = Path(__file__).parent / "sites"


class AdapterError(ValueError):
    """Raised when an adapter file is malformed."""


@dataclass(frozen=True)
class SiteAdapter:
    """Hints for one host. Every field has a working generic default."""

    match: str
    name: str = ""
    page_kind: PageKind = PageKind.PAGED
    reading_direction: ReadingDirection = ReadingDirection.TTB
    source_language: str = "en"

    #: Optional CSS scope. When set, images outside it are dropped before the
    #: width/cluster heuristics run.
    container_selector: str | None = None
    #: Below this natural width an image is chrome, not a comic page.
    min_image_width: int = 600
    #: Images whose URL matches any of these are never pages.
    url_blocklist: tuple[str, ...] = ()

    #: Extract the chapter number and series slug from a chapter URL.
    chapter_number_regex: str | None = None
    series_slug_regex: str | None = None
    #: Human-readable series title, read from the page itself.
    series_title_selector: str | None = None

    requires_login: bool = False
    #: If any of these selectors matches, the chapter is locked -- refuse it
    #: rather than trying to work around the restriction.
    locked_selector: str | None = None
    login_url: str | None = None

    #: Long-strip sources sometimes need extra settle time per scroll step.
    settle_ms: int = 700
    notes: str = ""

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == self.match or host.endswith(f".{self.match}")

    def chapter_number(self, url: str) -> str | None:
        return _first_group(self.chapter_number_regex, url)

    def series_slug(self, url: str) -> str | None:
        return _first_group(self.series_slug_regex, url)

    def is_blocked_url(self, url: str) -> bool:
        lowered = url.lower()
        return any(token.lower() in lowered for token in self.url_blocklist)


def _first_group(pattern: str | None, text: str) -> str | None:
    if not pattern:
        return None
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(1) if match.groups() else match.group(0)


#: Used for hosts with no adapter file. Deliberately conservative.
GENERIC = SiteAdapter(
    match="*",
    name="generic",
    notes="Fallback for unknown hosts; see suggest_adapter for the starter file.",
)


def suggest_adapter(url: str, pages: list[Path], directory: Path | None = None) -> Path | None:
    """Write a starter adapter for a host that did not have one.

    The generic defaults are paged, top-to-bottom and English, and two of those
    are wrong for most new sources: a Korean webtoon read through them is never
    re-cut into pages and is handed an English recogniser. Nothing downstream
    can infer either fact, so the operator has to state them -- and they will
    not know to, unless a file appears saying so.

    What is written is measured, not guessed: page shape comes from the
    heights that actually arrived. Language cannot be measured from geometry,
    so it is left at the default and flagged in the file. Returns the path
    written, or None if the host already has an adapter.
    """
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    for adapter in load_adapters(directory):
        if adapter.matches(url):
            return None

    target = (directory or SITES_DIR) / f"{host}.yaml"
    if target.exists():
        return None

    kind, direction, why = _infer_shape(pages)
    target.write_text(
        f"# Written automatically after a generic import of {host}.\n"
        f"# {why}\n"
        f"# CHECK source_language by eye -- it cannot be measured from the\n"
        f"# images, and a wrong value hands the chapter the wrong OCR engine.\n"
        f'match: "{host}"\n'
        f"name: {host.split('.')[0]}\n"
        f"page_kind: {kind.value}\n"
        f"reading_direction: {direction.value}\n"
        f"source_language: en\n"
        f"min_image_width: 600\n",
        encoding="utf-8",
    )
    load_adapters.cache_clear()
    return target


def _infer_shape(pages: list[Path]) -> tuple[PageKind, ReadingDirection, str]:
    """Paged or long-strip, decided by the shape of what arrived."""
    from PIL import Image

    ratios = []
    for path in pages:
        try:
            with Image.open(path) as handle:
                if handle.width:
                    ratios.append(handle.height / handle.width)
        except OSError:
            continue
    if not ratios:
        return PageKind.PAGED, ReadingDirection.TTB, "no images could be measured"

    tall = sum(1 for r in ratios if r > 2.5)
    if tall * 2 > len(ratios):
        return (
            PageKind.LONGSTRIP,
            ReadingDirection.TTB,
            f"{tall} of {len(ratios)} images are over 2.5x taller than wide",
        )
    return (
        PageKind.PAGED,
        ReadingDirection.TTB,
        f"images are page-shaped ({tall} of {len(ratios)} tall); "
        f"set reading_direction: rtl for Japanese manga",
    )

_ENUM_FIELDS = {"page_kind": PageKind, "reading_direction": ReadingDirection}
_TUPLE_FIELDS = {"url_blocklist"}
_KNOWN_FIELDS = set(SiteAdapter.__dataclass_fields__)


def load_adapter_file(path: Path) -> SiteAdapter:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AdapterError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdapterError(f"{path.name} must contain a mapping")
    if "match" not in raw:
        raise AdapterError(f"{path.name} is missing the required 'match' key")

    unknown = set(raw) - _KNOWN_FIELDS
    if unknown:
        raise AdapterError(
            f"{path.name} has unknown key(s): {', '.join(sorted(unknown))}. "
            f"Known keys: {', '.join(sorted(_KNOWN_FIELDS))}"
        )

    for key, enum_type in _ENUM_FIELDS.items():
        if key in raw:
            try:
                raw[key] = enum_type(raw[key])
            except ValueError as exc:
                valid = ", ".join(e.value for e in enum_type)
                raise AdapterError(
                    f"{path.name}: {key}={raw[key]!r} is not valid (expected: {valid})"
                ) from exc

    for key in _TUPLE_FIELDS:
        if key in raw:
            raw[key] = tuple(raw[key] or ())

    for key in ("chapter_number_regex", "series_slug_regex"):
        if raw.get(key):
            try:
                re.compile(raw[key])
            except re.error as exc:
                raise AdapterError(f"{path.name}: {key} is not a valid regex: {exc}") from exc

    raw.setdefault("name", path.stem)
    return SiteAdapter(**raw)


@lru_cache(maxsize=1)
def load_adapters(directory: Path | None = None) -> tuple[SiteAdapter, ...]:
    """All adapters on disk. Cached; call ``load_adapters.cache_clear()`` in tests."""
    target = directory or SITES_DIR
    if not target.exists():
        return ()
    adapters = []
    for path in sorted(target.glob("*.yaml")):
        adapters.append(load_adapter_file(path))
    return tuple(adapters)


def adapter_for(url: str, directory: Path | None = None) -> SiteAdapter:
    """The adapter for ``url``'s host, or the generic fallback."""
    for adapter in load_adapters(directory):
        if adapter.matches(url):
            return adapter
    return GENERIC


def describe_adapters(directory: Path | None = None) -> list[tuple[str, str]]:
    """(host, summary) pairs for ``comic sites``."""
    rows = []
    for adapter in load_adapters(directory):
        bits = [adapter.page_kind.value, adapter.reading_direction.value]
        if adapter.requires_login:
            bits.append("login required")
        rows.append((adapter.match, ", ".join(bits)))
    return rows
