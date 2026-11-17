"""Give re-cut pages back the Vietnamese they already had (the operator, 2026-11-09).

``fix_slice_order.py`` keeps the translations of every page that came out
pixel-identical and sets the rest aside (the backup's ``translation.json``).
Once the re-cut pages are re-detected, OCR'd and checked, this matches each new
region that has no Vietnamese to a set-aside line with the same English, and
copies it across. A bubble that the old cut had split in half reads differently
now, finds no match, and is left for ``comic translate --missing``.

    python scripts/carry_translations.py "My Insatiable Demon" 5 [--dry-run]
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comic.console import setup_console  # noqa: E402
from comic.paths import ChapterPaths  # noqa: E402
from comic.schemas import ChapterTranslation  # noqa: E402
from comic.state.store import ChapterStore, write_json  # noqa: E402
from comic.translation.gate import wants_translation  # noqa: E402
from comic.stages import analysis_for  # noqa: E402

#: Similarity two readings need to count as the same line. OCR of the new cut
#: is compared with the checked text of the old one, so allow small misreads.
MIN_RATIO = 0.88


def norm(text: str) -> str:
    return re.sub(r"[^0-9A-Z]+", " ", text.upper()).strip()


def main(series: str, chapter: str, dry_run: bool = False) -> int:
    setup_console()
    paths = ChapterPaths(series, chapter)
    W = paths.workspace
    old_file = W / "_backup_before_reorder" / "translation.json"
    if not old_file.exists():
        print(f"{paths.chapter}: no set-aside translations (was it re-cut by fix_slice_order.py?)")
        return 2
    old = json.loads(old_file.read_text(encoding="utf-8"))["entries"]
    cur_data = json.loads(paths.translation_file.read_text(encoding="utf-8")) \
        if paths.translation_file.exists() else {"series": series, "chapter": paths.chapter, "entries": []}
    done = {e["region_id"] for e in cur_data["entries"]}
    # Lines already carried by the fixer (kept pages) are not offered again.
    kept_src = {}
    for e in cur_data["entries"]:
        kept_src.setdefault(norm(e["source_text"]), 0)
        kept_src[norm(e["source_text"])] += 1
    pool = []
    for e in old:
        k = norm(e["source_text"])
        if kept_src.get(k):
            kept_src[k] -= 1          # the kept page's copy of this line
            continue
        if k:
            pool.append(e)

    state = ChapterStore(paths).load()
    todo = []
    for rec in state.pages:
        if rec.excluded:
            continue
        a = analysis_for(paths, rec.page)
        if a is None:
            continue
        todo += [(rec.page, r) for r in a.regions if wants_translation(r) and r.id not in done
                 and norm(r.source_text)]

    # Best pairs first; each old line is used once. Ties go to the nearer page.
    scored = []
    for page, r in todo:
        nr = norm(r.source_text)
        for j, e in enumerate(pool):
            ne = norm(e["source_text"])
            if abs(len(nr) - len(ne)) > max(4, 0.2 * max(len(nr), len(ne))):
                continue
            ratio = difflib.SequenceMatcher(None, nr, ne).ratio()
            if ratio >= MIN_RATIO:
                scored.append((-ratio, abs(page - int(e["region_id"][1:4])), r.id, j, r))
    scored.sort(key=lambda t: (t[0], t[1]))
    used_old, used_new, carried = set(), set(), []
    for neg, _dist, rid, j, r in scored:
        if rid in used_new or j in used_old:
            continue
        used_new.add(rid)
        used_old.add(j)
        e = dict(pool[j])
        e["region_id"] = rid
        e["source_text"] = r.source_text
        e["note"] = f"carried from {pool[j]['region_id']} (re-cut)" + (
            f"; {pool[j]['note']}" if pool[j].get("note") else "")
        carried.append(e)

    left = [r for _p, r in todo if r.id not in used_new]
    unused = [e for j, e in enumerate(pool) if j not in used_old]
    print(f"{paths.chapter}: carried {len(carried)}, still to translate {len(left)}, "
          f"old lines with no match {len(unused)}")
    for r in left:
        print(f"  new   {r.id} ({r.kind.value}): {r.source_text[:70]}")
    for e in unused:
        print(f"  gone  {e['region_id']}: {e['source_text'][:70]}")
    if not dry_run and carried:
        cur_data["entries"] += carried
        write_json(paths.translation_file, ChapterTranslation.model_validate(cur_data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], "--dry-run" in sys.argv))
