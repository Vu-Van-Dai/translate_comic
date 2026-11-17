"""The transcription check answered by an AgentRouter model (the operator, 2026-11-08).

Reads ``handoff/check_request.json`` (written by ``comic check``), sends its
sheets in batches with the prompt in ``prompts/check.md``, and writes
``handoff/check_response.json`` for ``comic resume`` -- which validates it
exactly as it validates an answer written by hand.

Pages held back for their content are not in the request at all (``comic
check`` skips excluded and held pages), so they are never sent.
"""

from __future__ import annotations

import json
from pathlib import Path

from comic.agents import codex
from comic.agents.codex import MAX_SINGLE_FAILURES, DEFAULT_MODEL, AgentError, is_blocked, run
from comic.check import CHECKED_KINDS, GATE
from comic.paths import ChapterPaths

PROMPT = Path(__file__).parent / "prompts" / "check.md"
#: Sheets per call: the Codex CLI adds ~11k tokens per call, so batch them.
SHEETS_PER_CALL = 3

KINDS = sorted(k.value for k in CHECKED_KINDS)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["region_id", "text", "kind", "skip"],
        "properties": {
            "region_id": {"type": "string"},
            "text": {"type": ["string", "null"]},
            "kind": {"type": ["string", "null"], "enum": KINDS + [None]},
            "skip": {"type": ["string", "null"]},
        },
    }}},
}


def _regions_block(items: list[dict]) -> str:
    return "\n".join(
        f"- {i['region_id']} ({i['kind']}): {json.dumps(i['engine_text'], ensure_ascii=False)}"
        + (f"   [{i['note']}]" if i.get("note") else "")
        for i in items
    )


def answer_check(paths: ChapterPaths, model: str = DEFAULT_MODEL, progress=print) -> Path:
    request = json.loads(paths.gate_request(GATE).read_text(encoding="utf-8"))
    template = PROMPT.read_text(encoding="utf-8")
    by_sheet: dict[str, list[dict]] = {}
    for item in request["items"]:
        by_sheet.setdefault(item["sheet"], []).append(item)
    sheets = list(by_sheet)
    known = {i["region_id"]: i for i in request["items"]}
    answers: dict[str, dict] = {}
    blocked: list[int] = []
    failures = 0
    queue = [sheets[k:k + SHEETS_PER_CALL] for k in range(0, len(sheets), SHEETS_PER_CALL)]
    while queue:
        batch = queue.pop(0)
        items = [i for s in batch for i in by_sheet[s]]
        progress(f"sheets {sheets.index(batch[0]) + 1}-{sheets.index(batch[-1]) + 1} of {len(sheets)} "
                 f"({len(items)} regions) -> {model}")
        try:
            out = run(template.replace("{regions}", _regions_block(items)),
                      [Path(s) for s in batch], SCHEMA, model)
        except AgentError as exc:
            # A refusal by the filter, or a request the server keeps dropping
            # (explicit pages often end in "stream disconnected" instead):
            # narrow it down, and leave what still fails for the agent.
            if len(batch) > 1:
                progress("  failed (" + ("blocked" if is_blocked(exc) else "dropped") +
                         ") -- retrying sheet by sheet")
                queue[:0] = [[s] for s in batch]
                continue
            if not is_blocked(exc):
                failures += 1
                if failures >= MAX_SINGLE_FAILURES:
                    raise
            blocked += sorted({i["page"] for i in items})
            progress("  this sheet is left for the agent: " + ("blocked" if is_blocked(exc) else "dropped"))
            continue
        failures = 0
        codex.note_plus(paths, "check", [i["region_id"] for i in items])
        for a in out.get("items", []):
            rid = a.get("region_id")
            if rid not in known:
                continue
            entry = {"region_id": rid}
            text = (a.get("text") or "").strip()
            if text and text != known[rid]["engine_text"].strip():
                entry["text"] = text
            if a.get("kind") and a["kind"] != known[rid]["kind"]:
                entry["kind"] = a["kind"]
            if (a.get("skip") or "").strip():
                entry["skip"] = a["skip"].strip()
            if len(entry) > 1:
                answers[rid] = entry
    note = paths.handoff / "check_blocked.json"
    if blocked:
        note.write_text(json.dumps({"pages": sorted(set(blocked))}), encoding="utf-8")
    elif note.exists():
        note.unlink()
    target = paths.gate_response(GATE)
    target.write_text(json.dumps({"items": list(answers.values())}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    return target
