"""The translate gate answered by an AgentRouter model (the operator, 2026-11-08).

Reads ``handoff/translate_request.json`` (written by ``comic translate``) and
the series context it points to, translates a few pages per call with the
prompt in ``prompts/translate.md``, and writes ``handoff/translate_response.json``
for ``comic resume`` -- which validates it and pins the forms of address exactly
as it does for an answer written by hand (an established pair always wins).

Each call carries the whole chapter's source text and every line already
translated, so names and forms of address stay consistent across calls.
"""

from __future__ import annotations

import json
from pathlib import Path

from comic.agents import codex
from comic.agents.codex import MAX_SINGLE_FAILURES, DEFAULT_MODEL, AgentError, is_blocked, run
from comic.paths import ChapterPaths

PROMPT = Path(__file__).parent / "prompts" / "translate.md"
PAGES_PER_CALL = 4
#: Pages of context on each side of a batch (source text and lines already
#: translated). AgentRouter's filter judges the whole request, so carrying the
#: whole chapter got every call refused once any page of it was explicit
#: (MID, 2026-11-09: 0-token refusals); the neighbours are what a line needs.
CONTEXT_PAGES = 3

_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["region_id", "target_text", "speaker", "addressee", "voice"],
    "properties": {
        "region_id": {"type": "string"},
        "target_text": {"type": "string"},
        "speaker": {"type": ["string", "null"]},
        "addressee": {"type": ["string", "null"]},
        "voice": {"type": ["string", "null"]},
    },
}
_PAIR = {
    "type": "object", "additionalProperties": False,
    "required": ["speaker", "addressee", "self_term", "other_term", "formality"],
    "properties": {k: {"type": "string"} for k in
                   ("speaker", "addressee", "self_term", "other_term", "formality")},
}
_TERM = {
    "type": "object", "additionalProperties": False, "required": ["source", "target"],
    "properties": {"source": {"type": "string"}, "target": {"type": "string"}},
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["items", "address_pairs", "new_glossary", "chapter_summary"],
    "properties": {
        "items": {"type": "array", "items": _ITEM},
        "address_pairs": {"type": "array", "items": _PAIR},
        "new_glossary": {"type": "array", "items": _TERM},
        "chapter_summary": {"type": ["string", "null"]},
    },
}

SUMMARY = ("Below is the full source text of one comic chapter (region id, kind, text). Write `chapter_summary`: "
           "3 to 6 English sentences on what happens (who, what, how it ends), for translating later "
           "chapters. Answer with the JSON object only.\n\n{script}\n")
SUMMARY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["chapter_summary"],
                  "properties": {"chapter_summary": {"type": "string"}}}

LAST = ("This is the LAST batch of the chapter: also write `chapter_summary` -- 3 to 6 English sentences on "
        "what happens in the whole chapter (who, what, how it ends), for translating later chapters.")
NOT_LAST = "Set `chapter_summary` to null (this is not the last batch)."


def answer_translate(paths: ChapterPaths, model: str = DEFAULT_MODEL, progress=print) -> Path:
    request = json.loads(paths.gate_request("translate").read_text(encoding="utf-8"))
    context = Path(request["context_path"]).read_text(encoding="utf-8")
    context = context.split("\n## How to answer")[0]      # the gate's own answer format is replaced here
    template = PROMPT.read_text(encoding="utf-8")
    pages = request["pages"]
    lines = [(p["page"], f"p{p['page']:03d} {i['region_id']} ({i['kind']}): {i['source_text']}")
             for p in pages for i in p["items"]]
    known = {i["region_id"] for p in pages for i in p["items"]}

    # Lines this chapter already has (a re-cut chapter, a partial run) are
    # shown as done, so the new ones stay consistent with them.
    earlier: dict[str, dict] = {}
    if paths.translation_file.exists():
        for e in json.loads(paths.translation_file.read_text(encoding="utf-8")).get("entries", []):
            if e["region_id"] not in known:
                earlier[e["region_id"]] = e
    items: dict[str, dict] = {}
    pairs: dict[tuple[str, str], dict] = {}
    terms: dict[str, dict] = {}
    summary = None
    blocked: list[int] = []
    failures = 0
    queue = [pages[k:k + PAGES_PER_CALL] for k in range(0, len(pages), PAGES_PER_CALL)]
    while queue:
        batch = queue.pop(0)
        lo, hi = batch[0]["page"] - CONTEXT_PAGES, batch[-1]["page"] + CONTEXT_PAGES
        near = lambda rid: lo <= int(rid[1:4]) <= hi          # noqa: E731
        script = "\n".join(t for n, t in lines if lo <= n <= hi)
        todo = "\n".join(
            f"- {i['region_id']} ({i['kind']}, max_chars {i['suggested_max_chars']}): {i['source_text']}"
            for p in batch for i in p["items"])
        done = "\n".join(f"- {r}: {v['target_text']}" + (f"  [{v['speaker']} -> {v['addressee']}]"
                                                          if v.get("speaker") and v.get("addressee") else "")
                         for r, v in sorted({**earlier, **items}.items()) if near(r)) or "(nothing yet)"
        prompt = (template.replace("{context}", context).replace("{script}", script)
                  .replace("{done}", done).replace("{items}", todo)
                  .replace("{last}", NOT_LAST))
        progress(f"pages {batch[0]['page']}-{batch[-1]['page']} "
                 f"({sum(len(p['items']) for p in batch)} regions) -> {model}")
        try:
            out = run(prompt, [Path(p["overlay_path"]) for p in batch], SCHEMA, model)
        except AgentError as exc:
            # A refusal by the filter, or a request the server keeps dropping:
            # find which page it is; the others still go through.
            if len(batch) > 1:
                progress("  failed (" + ("blocked" if is_blocked(exc) else "dropped") +
                         ") -- retrying page by page")
                queue[:0] = [[p] for p in batch]
                continue
            if not is_blocked(exc):
                failures += 1
                if failures >= MAX_SINGLE_FAILURES:
                    raise
            blocked.append(batch[0]["page"])
            progress(f"  page {batch[0]['page']} is left for the agent: "
                     + ("blocked" if is_blocked(exc) else "dropped"))
            continue
        failures = 0
        codex.note_plus(paths, "translate", [i["region_id"] for p in batch for i in p["items"]])
        for it in out.get("items", []):
            if it.get("region_id") in known and (it.get("target_text") or "").strip():
                items[it["region_id"]] = it
        for pr in out.get("address_pairs", []):
            pairs.setdefault((pr["speaker"], pr["addressee"]), pr)
        for t in out.get("new_glossary", []):
            terms.setdefault(t["source"], t)

    # The chapter summary needs the whole chapter, so it is its own small call
    # on the source text alone; if the filter refuses it, the agent writes it.
    if not paths.translation_file.exists() or not earlier:
        try:
            out = run(SUMMARY.replace("{script}", "\n".join(t for _n, t in lines)), [], SUMMARY_SCHEMA, model)
            summary = (out.get("chapter_summary") or "").strip() or None
        except AgentError as exc:
            if not is_blocked(exc):
                raise
            progress("  chapter summary refused by the filter -- left for the agent")

    response = {
        "items": [{k: v for k, v in it.items() if v is not None} for it in items.values()],
        "address_pairs": list(pairs.values()),
        "new_glossary": list(terms.values()),
        "character_notes": [],
        "chapter_summary": summary,
    }
    note = paths.handoff / "translate_blocked.json"
    if blocked:
        note.write_text(json.dumps({"pages": blocked}), encoding="utf-8")
    elif note.exists():
        note.unlink()
    target = paths.gate_response("translate")
    target.write_text(json.dumps(response, ensure_ascii=False, indent=1), encoding="utf-8")
    return target
