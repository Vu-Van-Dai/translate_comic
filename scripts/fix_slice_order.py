"""Fix a long-strip chapter whose source slices were saved out of order.

Some early comix downloads (My Insatiable Demon, 2026-10-26) saved neighbouring
slices swapped, which cuts bubbles in half at the seam (MID ch.29, ch.30;
the operator, 2026-11-08). Given a fresh download of the same chapter, this:

1. compares the two by pixel content (a fresh download may add end cards --
   slices the old one never had are ignored, never inserted);
2. if the order differs: backs up pages/analysis/masks/studio.json/chapter.json
   to ``_backup_before_reorder``, renames the originals (and the raw folder) into
   the fresh order, re-cuts the strip, and keeps every page that comes out
   pixel-identical -- its analysis, the operator's Studio edits and reviewed mark;
3. a page that comes out pixel-identical to any old page keeps its analysis,
   reviewed mark and translations under its new number (region ids renumbered);
   translations of the other pages are set aside for ``carry_translations.py``;
4. prints the pages that changed, to re-detect with ``comic analyze -p``.

    python scripts/fix_slice_order.py "My Insatiable Demon" 29 raw/_recap_MID/29 [--check-only]
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.paths import ChapterPaths, project_root  # noqa: E402
from comic.schemas import ChapterState, ChapterTranslation  # noqa: E402
from comic.stages import run_slice  # noqa: E402
from comic.state.store import ChapterStore, write_json  # noqa: E402


def pixels(f: Path) -> str:
    with Image.open(f) as im:
        return hashlib.sha1(np.asarray(im.convert("RGB")).tobytes()).hexdigest()


def main(series: str, chapter: str, fresh_dir: str, check_only: bool = False) -> int:
    paths = ChapterPaths(series, chapter)
    W = paths.workspace
    old = sorted(f for f in paths.original.iterdir() if f.is_file())
    fresh = sorted(f for f in Path(fresh_dir).iterdir() if f.is_file())
    # Identical slices (blank spacers) are common: each hash keeps a queue of
    # the saved files that have it, taken in order (WitR ch.14, 2026-11-09).
    groups: dict[str, list[Path]] = {}
    for f in old:
        groups.setdefault(pixels(f), []).append(f)
    fresh_files: list[Path] = []
    for f in fresh:
        queue = groups.get(pixels(f))
        if queue:
            fresh_files.append(queue.pop(0))
    if len(fresh_files) != len(old):
        missing = len(old) - len(fresh_files)
        print(f"{paths.chapter}: {missing} saved slice(s) not in the fresh download -- not touching it")
        return 2
    if fresh_files == old:
        print(f"{paths.chapter}: slice order already correct")
        return 0

    moved = [(f.name, src.name) for f, src in zip(old, fresh_files) if f != src]
    print(f"{paths.chapter}: {len(moved)} slice(s) out of order: " +
          ", ".join(f"{a}<-{b}" for a, b in moved))
    if check_only:
        return 1

    if not any(paths.analysis.glob("page_*.json")) and not paths.translation_file.exists():
        # Not analysed yet: only the source order matters; `comic analyze` cuts it later.
        reorder_files(paths, series, old, fresh_files)
        store = ChapterStore(paths)
        state = store.load()
        if any(paths.pages.glob("*.png")):
            run_slice(paths, state, force=True)
        else:
            # The records still name original/ files: their hash and size moved with the content.
            for rec in state.pages:
                f = paths.original / rec.filename
                if f.exists():
                    rec.sha256 = hashlib.sha256(f.read_bytes()).hexdigest() if rec.sha256 else None
                    with Image.open(f) as im:
                        rec.width, rec.height = im.size
        store.save(state)
        print(f"{paths.chapter}: order fixed (not analysed yet -- nothing else to keep)")
        print("REDETECT ")
        return 1

    backup = W / "_backup_before_reorder"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    for sub in ("pages", "analysis", "masks"):
        if (W / sub).is_dir():
            shutil.copytree(W / sub, backup / sub)
    for name in ("studio.json", "chapter.json", "translation.json", "render_plan.json"):
        if (W / name).exists():
            shutil.copy2(W / name, backup / name)
    # Per-page outputs are numbered by the old cut: move them out of the way,
    # never delete (a PSD there may hold the operator's work).
    for sub in ("cleaned", "translated", "photoshop", "temp"):
        if (W / sub).is_dir():
            if sub == "photoshop" and any((W / sub).rglob("*.psd")):
                print(f"  note: photoshop/ holds PSDs -- moved to {backup.name}/photoshop")
            shutil.move(str(W / sub), str(backup / sub))
    old_state = ChapterState.model_validate_json((backup / "chapter.json").read_text(encoding="utf-8"))
    old_records = {r.page: r for r in old_state.pages}
    # A paged chapter reads its pages straight from original/ (nothing is cut),
    # so a wrong order is a permutation of whole pages: hash them before renaming.
    paged = not any((backup / "pages").glob("*.png")) if (backup / "pages").is_dir() else True
    if paged:
        old_hash = {r.page: pixels(paths.original / r.filename) for r in old_state.pages
                    if (paths.original / r.filename).exists()}
    else:
        old_hash = {int(f.stem): pixels(f) for f in (backup / "pages").glob("*.png")}
    by_hash: dict[str, list[int]] = {}
    for n, h in old_hash.items():
        by_hash.setdefault(h, []).append(n)

    reorder_files(paths, series, old, fresh_files)

    store = ChapterStore(paths)
    state = store.load()
    run_slice(paths, state, force=True)
    for f in paths.pages.glob("*.png"):
        if int(f.stem) > state.page_count:
            f.unlink()

    # A new page that is pixel-identical to an old one (same number or not)
    # keeps that page's analysis, mask, stages, reviewed mark and translations,
    # with its region ids renumbered. Everything else is re-detected.
    for d in (paths.analysis, paths.analysis / "_detected", paths.masks):
        if d.is_dir():
            for f in d.glob("page_*.*"):
                f.unlink()
    moved_from: dict[int, int] = {}                    # new page -> old page
    for rec in state.pages:
        cands = by_hash.get(pixels(paths.page_file(rec.page)), [])
        if len(cands) == 1 and cands[0] in old_records:
            moved_from[rec.page] = cands[0]
    changed = []
    for rec in state.pages:
        o = moved_from.get(rec.page)
        if o is None:
            changed.append(rec.page)
            rec.stages = {}
            continue
        rec.stages = dict(old_records[o].stages)
        rec.width, rec.height = old_records[o].width, old_records[o].height
        if paged:
            rec.sha256, rec.source_url = old_records[o].sha256, old_records[o].source_url
        rec.excluded, rec.held = old_records[o].excluded, old_records[o].held
        rec.hold_reason, rec.hold_cleanup = old_records[o].hold_reason, old_records[o].hold_cleanup
        for sub in ("analysis", "analysis/_detected"):
            src = backup / sub / f"page_{o:03d}.json"
            if src.exists():
                (W / sub).mkdir(parents=True, exist_ok=True)
                (W / sub / f"page_{rec.page:03d}.json").write_text(
                    renumber(src.read_text(encoding="utf-8"), o, rec.page), encoding="utf-8")
        src = backup / "masks" / f"page_{o:03d}.png"
        if src.exists():
            shutil.copy2(src, paths.mask_file(rec.page))
    store.save(state)

    remap = {o: n for n, o in moved_from.items()}
    lost = [r for r in old_state.pages if (r.excluded or r.held) and r.page not in remap]
    for r in lost:
        print(f"  NOTE: old page {r.page} was {'held' if r.held else 'excluded'}"
              f" ({r.hold_reason or 'no reason'}) and was re-cut -- hold/exclude it again")
    tr = W / "translation.json"
    if tr.exists():
        data = json.loads(tr.read_text(encoding="utf-8"))
        kept_entries = []
        for e in data.get("entries", []):
            o = int(e["region_id"][1:4])
            if o in remap:
                e["region_id"] = f"p{remap[o]:03d}" + e["region_id"][4:]
                kept_entries.append(e)
        dropped = len(data.get("entries", [])) - len(kept_entries)
        data["entries"] = kept_entries
        write_json(tr, ChapterTranslation.model_validate(data))
        print(f"  translations: kept {len(kept_entries)}, {dropped} set aside for "
              f"scripts/carry_translations.py")
    sl = W / "split_log.json"
    if sl.exists():
        shutil.copy2(sl, backup / sl.name)
        re_id = lambda rid: f"p{remap[int(rid[1:4])]:03d}" + rid[4:]   # noqa: E731
        data = json.loads(sl.read_text(encoding="utf-8"))
        data = {re_id(k): dict(v, pieces=[re_id(x) for x in v.get("pieces", [])])
                for k, v in data.items() if int(k[1:4]) in remap}
        sl.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    rp = W / "render_plan.json"
    if rp.exists():
        data = json.loads(rp.read_text(encoding="utf-8"))
        data["overrides"] = {f"p{remap[int(k[1:4])]:03d}" + k[4:]: v
                             for k, v in data.get("overrides", {}).items() if int(k[1:4]) in remap}
        data["shown"], data["approved"] = {}, {}
        rp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    studio = W / "studio.json"
    data = json.loads(studio.read_text(encoding="utf-8")) if studio.exists() else {"reviewed": []}
    reviewed = {remap[p] for p in data.get("reviewed", []) if p in remap}
    if tr.exists():
        # A translated chapter's kept pages were proofread before (the old gate
        # flow): only the re-cut pages wait for Studio.
        reviewed |= set(moved_from)
    data["reviewed"] = sorted(reviewed)
    if studio.exists() or reviewed:
        studio.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"{paths.chapter}: {state.page_count} pages (was {len(old_hash)}); "
          f"kept {len(moved_from)}, re-detect {len(changed)}")
    print("REDETECT " + ",".join(map(str, changed)))
    return 1


def reorder_files(paths, series, old, fresh_files) -> None:
    num = "".join(c for c in paths.chapter if c.isdigit() or c == ".").lstrip("0") or "0"
    raw = project_root() / "raw" / series / num
    for folder in (paths.original, raw):
        if not folder.is_dir():
            continue
        files = {f.name: f for f in folder.iterdir() if f.is_file()}
        plan = [(files[src], folder / dst) for dst, src in
                [(f.name, src.name) for f, src in zip(old, fresh_files)] if src in files]
        temps = []
        for src, dst in plan:
            tmp = src.with_name("_reorder_" + src.name)
            src.rename(tmp)
            temps.append((tmp, dst))
        for tmp, dst in temps:
            tmp.rename(dst)



def renumber(text: str, old: int, new: int) -> str:
    """An analysis file of page ``old`` rewritten as page ``new``."""
    data = json.loads(text)
    data["page"] = new
    for r in data.get("regions", []):
        r["page"] = new
        r["id"] = f"p{new:03d}" + r["id"][4:]
    return json.dumps(data, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3], "--check-only" in sys.argv))
