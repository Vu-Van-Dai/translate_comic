"""Studio's view of a chapter: the operator's region review (2026-11-08).

The operator corrects what detection found -- moves, resizes, adds and deletes
boxes, and says what each one is. Their answer is written straight into the
chapter's ``analysis/page_NNN.json``, so every later stage reads it without
knowing Studio exists. The detector's own version is copied once to
``analysis/_detected/`` before the first save, so nothing it found is lost.

Studio's labels are a superset of :class:`TextKind`: logo, watermark and credit
are all erased and never translated, so they are stored as ``erase`` with the
label kept in ``tag``. ``sfx_lama`` (key L, the operator, 2026-11-14) is an SFX
like any other -- checked, translated, set -- stored as ``sfx`` with tag
``lama``: LaMa erases it at cleaning instead of Content-Aware Fill at the PSD
build, for the ones Photoshop would botch.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from comic.paths import ChapterPaths, chapter_sort_key, project_root
from comic.schemas import (
    LAMA_SFX_TAG, BBox, PageAnalysis, ReadingDirection, TextKind, TextRegion, region_id,
    rotated_corners, unrotated_box,
)
from comic.state.store import read_json, write_json
from comic.vision.regions import order_regions

#: Studio label -> (stored kind, tag). Order is the keyboard shortcut order
#: (1-9, 0); ``sfx_lama`` comes after them on its own letter key, L.
LABELS: dict[str, tuple[TextKind, str | None]] = {
    "dialogue": (TextKind.DIALOGUE, None),
    "thought": (TextKind.THOUGHT, None),
    "narration": (TextKind.NARRATION, None),
    "caption": (TextKind.CAPTION, None),
    "shout": (TextKind.SHOUT, None),
    "sfx": (TextKind.SFX, None),
    "logo": (TextKind.ERASE, "logo"),
    "watermark": (TextKind.ERASE, "watermark"),
    "credit": (TextKind.ERASE, "credit"),
    "keep": (TextKind.KEEP, None),
    "sfx_lama": (TextKind.SFX, LAMA_SFX_TAG),
}

#: Tags an ``erase`` region may carry, each one a Studio label.
ERASE_TAGS = ("logo", "watermark", "credit")

REVIEW_FILE = "studio.json"


class StudioError(ValueError):
    """A request Studio refuses; the message is shown to the operator."""


def label_of(region: TextRegion) -> str:
    if region.kind is TextKind.ERASE:
        return region.tag if region.tag in ERASE_TAGS else "logo"
    if region.lama_sfx:
        return "sfx_lama"
    return region.kind.value


def _workspace() -> Path:
    return (project_root() / "workspace").resolve()


def chapter_paths(series: str, chapter: str) -> ChapterPaths:
    """Paths for a chapter named by the browser, refusing anything that would
    resolve outside ``workspace/``."""
    if not series or not chapter or any(p in (".", "..") for p in (series, chapter)):
        raise StudioError("series and chapter are required")
    paths = ChapterPaths(series, chapter)
    root = _workspace()
    target = paths.workspace.resolve()
    if root not in target.parents or not paths.analysis.is_dir():
        raise StudioError(f"no analysed chapter {series!r} / {chapter!r}")
    return paths


def list_series() -> list[dict]:
    out = []
    root = _workspace()
    if not root.is_dir():
        return out
    for series_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not series_dir.is_dir() or series_dir.name.startswith("_"):
            continue
        chapters = [
            c.name for c in series_dir.iterdir()
            if c.is_dir() and any((c / "analysis").glob("page_*.json"))
        ]
        if chapters:
            out.append({"series": series_dir.name,
                        "chapters": sorted(chapters, key=chapter_sort_key)})
    return out


def _review_state(paths: ChapterPaths) -> dict:
    f = paths.workspace / REVIEW_FILE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"reviewed": []}


def _write_review_state(paths: ChapterPaths, state: dict) -> None:
    f = paths.workspace / REVIEW_FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(f)


def reviewed_pages(paths: ChapterPaths) -> set[int]:
    return set(_review_state(paths).get("reviewed", []))


def chapter_info(series: str, chapter: str) -> dict:
    paths = chapter_paths(series, chapter)
    reviewed = reviewed_pages(paths)
    pages = []
    for f in sorted(paths.analysis.glob("page_*.json")):
        try:
            page = int(f.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        analysis = read_json(f, PageAnalysis)
        pages.append({
            "page": page,
            "regions": len(analysis.regions),
            "reviewed": page in reviewed,
            "edited": (paths.analysis / "_detected" / f.name).exists(),
        })
    return {"series": series, "chapter": chapter, "pages": pages,
            "labels": list(LABELS)}


def _region_out(region: TextRegion) -> dict:
    b = region.bbox
    box = [b.x1, b.y1, b.x2, b.y2]
    if region.angle and len(region.polygon) == 4:
        # Studio edits the upright box and its angle; the file keeps the corners.
        box = [round(v) for v in unrotated_box(region.polygon, region.angle)]
    return {
        "id": region.id,
        "label": label_of(region),
        "bbox": box,
        "angle": region.angle,
        "text": region.source_text,
        "confidence": region.ocr_confidence,
        "reviewed": region.ocr_reviewed,
        "edited": region.edited,
        "order": region.reading_order,
    }


def load_page(series: str, chapter: str, page: int) -> dict:
    paths = chapter_paths(series, chapter)
    f = paths.analysis_file(page)
    if not f.exists():
        raise StudioError(f"page {page} has no analysis")
    analysis = read_json(f, PageAnalysis)
    regions = sorted(analysis.regions, key=lambda r: r.reading_order)
    image = paths.page_file(page)
    return {
        "page": page,
        # changes whenever the page file is rewritten (a re-cut strip), so the
        # browser never shows a cached old picture under new boxes (2026-11-08)
        "v": int(image.stat().st_mtime) if image.exists() else 0,
        "width": analysis.width,
        "height": analysis.height,
        "direction": analysis.reading_direction.value,
        "regions": [_region_out(r) for r in regions],
        "reviewed": page in reviewed_pages(paths),
    }


def page_image(series: str, chapter: str, page: int) -> Path:
    paths = chapter_paths(series, chapter)
    f = paths.page_file(page)
    if not f.exists():
        raise StudioError(f"page {page} image missing")
    return f


def _bbox(raw, width: int, height: int) -> BBox:
    try:
        x1, y1, x2, y2 = (int(round(float(v))) for v in raw)
    except (TypeError, ValueError):
        raise StudioError(f"bad box {raw!r}") from None
    x1, x2 = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
    y1, y2 = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
    if x2 - x1 < 4 or y2 - y1 < 4:
        raise StudioError(f"box too small: {raw!r}")
    return BBox(x1=x1, y1=y1, x2=x2, y2=y2)


def _angle(raw) -> float:
    try:
        a = float(raw or 0.0)
    except (TypeError, ValueError):
        raise StudioError(f"bad angle {raw!r}") from None
    a = (a + 180.0) % 360.0 - 180.0
    return 0.0 if abs(a) < 0.05 else round(a, 2)


def _tilted(raw, angle: float, width: int, height: int) -> tuple[BBox, list]:
    """A box the operator turned: its upright bounds and its four corners."""
    try:
        x1, y1, x2, y2 = (float(v) for v in raw)
    except (TypeError, ValueError):
        raise StudioError(f"bad box {raw!r}") from None
    if abs(x2 - x1) < 4 or abs(y2 - y1) < 4:
        raise StudioError(f"box too small: {raw!r}")
    corners = rotated_corners((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), angle)
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    bounds = _bbox([min(xs), min(ys), max(xs), max(ys)], width, height)
    return bounds, [(int(round(x)), int(round(y))) for x, y in corners]


def save_page(series: str, chapter: str, page: int, payload: dict) -> dict:
    """Apply the operator's regions for one page and return the page as saved.

    Unchanged regions keep every field the detector and OCR gave them; a moved
    or resized one loses its tight polygon (it no longer matches) and is marked
    ``edited``; new ones get the next free id on the page.
    """
    paths = chapter_paths(series, chapter)
    f = paths.analysis_file(page)
    if not f.exists():
        raise StudioError(f"page {page} has no analysis")
    analysis = read_json(f, PageAnalysis)
    old = {r.id: r for r in analysis.regions}
    used = {int(r.id.rsplit("_r", 1)[1]) for r in analysis.regions if "_r" in r.id}
    detected = paths.analysis / "_detected" / f.name
    if detected.exists():
        orig = read_json(detected, PageAnalysis)
        used |= {int(r.id.rsplit("_r", 1)[1]) for r in orig.regions if "_r" in r.id}

    items = payload.get("regions")
    if not isinstance(items, list):
        raise StudioError("regions must be a list")

    regions: list[TextRegion] = []
    moved = False
    seen: set[str] = set()
    for item in items:
        label = item.get("label")
        if label not in LABELS:
            raise StudioError(f"unknown label {label!r}")
        kind, tag = LABELS[label]
        angle = _angle(item.get("angle"))
        if angle:
            box, corners = _tilted(item.get("bbox"), angle, analysis.width, analysis.height)
        else:
            box, corners = _bbox(item.get("bbox"), analysis.width, analysis.height), []
        text = item.get("text", "")
        if not isinstance(text, str):
            raise StudioError("text must be a string")
        rid = item.get("id")
        base = old.get(rid) if isinstance(rid, str) else None
        if base is None or rid in seen:
            index = max(used, default=0) + 1
            used.add(index)
            region = TextRegion(
                id=region_id(page, index), page=page, bbox=box, kind=kind, tag=tag,
                angle=angle, polygon=corners,
                source_text=text, edited=True,
                ocr_provider="operator" if text.strip() else None,
                ocr_reviewed=bool(text.strip()),
            )
            moved = True
        else:
            changes: dict = {"kind": kind, "tag": tag}
            if angle:
                # The upright box travels rounded to whole pixels, so corners
                # within a pixel of the stored ones are the same box.
                same = (angle == base.angle and len(base.polygon) == 4 and all(
                    abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1
                    for a, b in zip(corners, base.polygon)))
                if not same:
                    changes.update(bbox=box, polygon=corners, angle=angle, edited=True)
                    moved = True
            elif box != base.bbox or base.angle:
                changes.update(bbox=box, polygon=[], angle=0.0, edited=True)
                moved = True
            if text != base.source_text:
                changes.update(source_text=text, ocr_provider="operator",
                               ocr_reviewed=True, ocr_confidence=1.0)
            region = base.model_copy(update=changes)
        seen.add(region.id)
        regions.append(region)

    if len(regions) != len(old) or {r.id for r in regions} != set(old):
        moved = True
    if moved:
        rtl = analysis.reading_direction is ReadingDirection.RTL
        regions = order_regions(regions, right_to_left=rtl)
    else:
        regions.sort(key=lambda r: r.reading_order)
    regions = [r.model_copy(update={"reading_order": i}) for i, r in enumerate(regions)]

    changed = analysis.model_copy(update={"regions": regions})
    if changed.model_dump(exclude={"analyzed_at"}) != analysis.model_dump(exclude={"analyzed_at"}):
        if not detected.exists():
            detected.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, detected)
        write_json(f, changed)

    if "reviewed" in payload:
        state = _review_state(paths)
        pages = set(state.get("reviewed", []))
        (pages.add if payload["reviewed"] else pages.discard)(page)
        state["reviewed"] = sorted(pages)
        _write_review_state(paths, state)
    return load_page(series, chapter, page)


def set_chapter_reviewed(series: str, chapter: str, reviewed: bool) -> dict:
    """Mark every page of the chapter reviewed (or not) at once (the operator, 2026-11-08)."""
    paths = chapter_paths(series, chapter)
    pages = [int(f.stem.split("_")[1]) for f in paths.analysis.glob("page_*.json")
             if f.stem.split("_")[1].isdigit()]
    state = _review_state(paths)
    state["reviewed"] = sorted(pages) if reviewed else []
    _write_review_state(paths, state)
    return {"reviewed": state["reviewed"]}
