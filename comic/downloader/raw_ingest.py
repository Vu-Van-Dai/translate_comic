"""Ingesting manually-saved pages.

The escape hatch. When a host cannot or should not be read automatically, the
operator saves the pages themselves -- browser "save image as", a screenshot
sweep, a download-manager extension -- drops them in ``raw/<series>/<chapter>/``
and the pipeline picks up from there. Everything downstream is identical.

The hard part is *order*, because manually-saved filenames are unreliable:
``page2.jpg`` must sort before ``page10.jpg``, and a batch save may carry only
timestamps. So ordering is explicit, previewable, and confirmed before anything
is written.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from comic.downloader.importer import MIN_PAGE_DIMENSION, sniff_image
from comic.paths import ChapterPaths, page_filename, project_root, safe_name
from comic.schemas import PageRecord

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}

_NUM_RE = re.compile(r"(\d+)")


class RawIngestError(RuntimeError):
    """Raised when a raw folder cannot be ingested."""


class OrderBy(str, Enum):
    NAME = "name"       # natural sort: page2 before page10
    MODIFIED = "mtime"  # save order, for batch downloads with opaque names
    CREATED = "ctime"


def raw_dir(series: str, chapter: str, root: Path | None = None) -> Path:
    """Where the operator drops manually-saved pages."""
    base = root or project_root()
    return base / "raw" / safe_name(series) / str(chapter)


def chapter_number(label: str) -> str:
    """``Chapter_069`` -> ``069``. Non-numeric labels pass through unchanged.

    ``removeprefix``, not ``lstrip``: lstrip strips a *character set*, so
    ``"Extra".lstrip("Chapter_")`` silently eats letters.
    """
    return str(label).removeprefix("Chapter_")


def extension_dir(series: str, chapter: str) -> Path:
    """Where the Chrome extension writes, under the browser's download folder."""
    return (
        Path.home() / "Downloads" / "comic-raw"
        / safe_name(series) / chapter_number(chapter)
    )


def chapter_aliases(*labels: str) -> list[str]:
    """Every spelling of a chapter a folder might reasonably use.

    ``--chapter 49`` normalises to ``Chapter_049``, but a folder made by hand or
    by the extension is just as likely to be ``49``. Looking for only the
    padded form is why an obviously-present folder goes unfound.
    """
    forms: list[str] = []
    for label in labels:
        if not label:
            continue
        text = chapter_number(str(label))
        for form in (text, text.lstrip("0") or text, f"Chapter_{text}"):
            if form and form not in forms:
                forms.append(form)
        # Zero-padded, when the label is numeric.
        try:
            forms.append(f"{int(float(text)):03d}")
        except (TypeError, ValueError):
            pass
    return [f for i, f in enumerate(forms) if f not in forms[:i]]


def find_source(
    series: str, chapter: str, root: Path | None = None, *, given: str | None = None
) -> Path | None:
    """First candidate folder that exists and holds files.

    Checks the project's own ``raw/`` tree first, then the extension's download
    folder, so ``import-raw`` needs no ``--from`` in the common case. ``given``
    is what the operator actually typed, which is often the folder's real name.
    """
    for form in chapter_aliases(given or "", str(chapter)):
        for candidate in (raw_dir(series, form, root), extension_dir(series, form)):
            if candidate.is_dir() and any(candidate.iterdir()):
                return candidate
    return None


def natural_key(name: str) -> tuple:
    """Sort key where embedded numbers compare numerically.

    ``page10`` after ``page9``; plain lexicographic sorting gets this wrong and
    silently scrambles a chapter.
    """
    parts = _NUM_RE.split(name.lower())
    return tuple(int(p) if p.isdigit() else p for p in parts)


@dataclass
class RawCandidate:
    path: Path
    width: int
    height: int
    extension: str
    size: int

    @property
    def label(self) -> str:
        return f"{self.path.name} ({self.width}x{self.height}, {self.size // 1024} KB)"


@dataclass
class RawPlan:
    """What ingest *would* do. Printed for confirmation before any write."""

    candidates: list[RawCandidate] = field(default_factory=list)
    rejected: list[tuple[Path, str]] = field(default_factory=list)
    source: Path | None = None

    @property
    def count(self) -> int:
        return len(self.candidates)

    def mapping(self) -> list[tuple[Path, str]]:
        return [
            (c.path, page_filename(i, c.extension))
            for i, c in enumerate(self.candidates, start=1)
        ]


def scan(folder: Path, order_by: OrderBy = OrderBy.NAME) -> RawPlan:
    """Inspect a raw folder and decide what each file would become."""
    if not folder.exists():
        raise RawIngestError(f"no such folder: {folder}")
    if not folder.is_dir():
        raise RawIngestError(f"not a folder: {folder}")

    folder = folder.resolve()
    plan = RawPlan(source=folder)
    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        raise RawIngestError(f"{folder} is empty")

    if order_by is OrderBy.NAME:
        files.sort(key=lambda p: natural_key(p.name))
    elif order_by is OrderBy.MODIFIED:
        files.sort(key=lambda p: (p.stat().st_mtime, natural_key(p.name)))
    else:
        files.sort(key=lambda p: (p.stat().st_ctime, natural_key(p.name)))

    for path in files:
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            plan.rejected.append((path, "not an image file"))
            continue
        data = path.read_bytes()
        sniffed = sniff_image(data)
        if sniffed is None:
            plan.rejected.append((path, "does not decode as an image"))
            continue
        extension, width, height = sniffed
        if width < MIN_PAGE_DIMENSION or height < MIN_PAGE_DIMENSION:
            plan.rejected.append((path, f"too small ({width}x{height})"))
            continue
        plan.candidates.append(
            RawCandidate(path, width, height, extension, len(data))
        )

    if not plan.candidates:
        reasons = "; ".join(f"{p.name}: {why}" for p, why in plan.rejected[:5])
        raise RawIngestError(f"no usable images in {folder}. {reasons}")
    return plan


#: Below this peak luminance a page is dimmed rather than merely dark: real
#: comic pages contain white. Measured on dimmed bilibili captures: max 153.
DIM_PEAK = 200


def peak_luminance(data: bytes) -> int:
    """Brightest pixel in an image, or 255 if it cannot be read."""
    import io

    import numpy as np
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            arr = np.asarray(im.convert("L"))
    except Exception:
        return 255
    return int(arr.max()) if arr.size else 255


def normalize_levels(data: bytes) -> bytes | None:
    """Rescale a dimmed page so its brightest pixel is white again.

    A capture taken through a dimmed browser loses the top of its range -- white
    speech bubbles land near 60% grey -- which costs OCR the contrast it relies
    on. The mapping is a pure linear stretch, so relative tones are preserved.

    Returns ``None`` when the page is already using the full range.
    """
    import io

    import numpy as np
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            arr = np.asarray(rgb).astype(np.float32)
    except Exception:
        return None

    peak = float(np.asarray(rgb.convert("L")).max())
    if peak >= DIM_PEAK or peak <= 1:
        return None

    stretched = np.clip(arr * (255.0 / peak), 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(stretched).save(out, format="PNG")
    return out.getvalue()


def ingest(plan: RawPlan, paths: ChapterPaths, normalize: bool = False) -> list[PageRecord]:
    """Copy a scanned plan into the chapter workspace, renumbered.

    ``normalize`` is off by default on purpose: the workspace copy is the
    archive of what the source actually gave us, and rewriting its pixels would
    quietly break that. Turn it on for captures taken through a dimmed browser,
    where white never reaches 255 and OCR loses the contrast it needs.
    """
    paths.original.mkdir(parents=True, exist_ok=True)

    records: list[PageRecord] = []
    seen: dict[str, int] = {}
    page_number = 0

    for candidate in plan.candidates:
        data = candidate.path.read_bytes()
        if normalize:
            fixed = normalize_levels(data)
            if fixed is not None:
                data = fixed
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            # A duplicate save is common when sweeping a reader by hand.
            continue
        page_number += 1
        seen[digest] = page_number
        filename = page_filename(page_number, candidate.extension)
        (paths.original / filename).write_bytes(data)
        records.append(
            PageRecord(
                page=page_number,
                filename=filename,
                width=candidate.width,
                height=candidate.height,
                source_url=candidate.path.as_uri(),
                sha256=digest,
            )
        )
    return records
