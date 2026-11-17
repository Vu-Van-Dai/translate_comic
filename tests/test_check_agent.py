"""The agent-answered transcription check: only real changes reach check_response.json."""

from __future__ import annotations

import json

from comic.agents import check_agent
from comic.check import GATE
from comic.paths import ChapterPaths


def test_answer_keeps_only_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    paths.handoff.mkdir(parents=True)
    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"x")
    items = [
        {"region_id": "p001_r01", "page": 1, "kind": "dialogue", "engine_text": "HELO", "sheet": str(sheet)},
        {"region_id": "p001_r02", "page": 1, "kind": "dialogue", "engine_text": "FINE", "sheet": str(sheet)},
        {"region_id": "p001_r03", "page": 1, "kind": "narration", "engine_text": "", "sheet": str(sheet)},
    ]
    paths.gate_request(GATE).write_text(json.dumps({"items": items}), encoding="utf-8")
    sent = {}

    def fake_run(prompt, images, schema, model):
        sent["prompt"], sent["images"] = prompt, images
        return {"items": [
            {"region_id": "p001_r01", "text": "HELLO", "kind": None, "skip": None},
            {"region_id": "p001_r02", "text": "FINE", "kind": None, "skip": None},   # unchanged
            {"region_id": "p001_r03", "text": None, "kind": "sfx", "skip": "no lettering in this box, only art"},
            {"region_id": "p009_r09", "text": "INVENTED", "kind": None, "skip": None},  # unknown id
        ]}

    monkeypatch.setattr(check_agent, "run", fake_run)
    out = check_agent.answer_check(paths)
    answer = json.loads(out.read_text(encoding="utf-8"))["items"]
    assert answer == [
        {"region_id": "p001_r01", "text": "HELLO"},
        {"region_id": "p001_r03", "kind": "sfx", "skip": "no lettering in this box, only art"},
    ]
    assert "p001_r01 (dialogue): \"HELO\"" in sent["prompt"] and len(sent["images"]) == 1
    assert "never translate" in sent["prompt"]


def test_transient_errors_are_retried_blocks_once(monkeypatch):
    from comic.agents import codex

    calls = []
    monkeypatch.setattr(codex, "_log_error", lambda exc: None)

    def flaky(prompt, images, schema, model, provider="agentrouter"):
        calls.append(1)
        if len(calls) < 3:
            raise codex.AgentError("codex exec failed (1): ERROR: rate limit exceeded")
        return {"ok": True}

    monkeypatch.setattr(codex, "_run_once", flaky)
    monkeypatch.setattr(codex.time, "sleep", lambda s: None)
    assert codex.run("p", [], {}) == {"ok": True} and len(calls) == 3

    monkeypatch.setattr(codex, "_log_fallback", lambda *a: None)

    def blocked(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        raise codex.AgentError('{"error":{"code":"content-blocked"}}')

    calls.clear()
    monkeypatch.setattr(codex, "_run_once", blocked)
    import pytest
    with pytest.raises(codex.AgentError) as err:
        codex.run("p", [], {})
    # one unchanged retry on AgentRouter, then once on the Plus login, then refused
    assert calls == ["agentrouter", "agentrouter", "openai"] and codex.is_blocked(err.value)

    def plus_answers(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        if provider == "agentrouter":
            raise codex.AgentError('{"error":{"code":"content-blocked"}}')
        return {"ok": True}

    calls.clear()
    monkeypatch.setattr(codex, "_run_once", plus_answers)
    assert codex.run("p", [], {}) == {"ok": True} and codex.last_provider == "openai"


def test_codex_first_batches_fall_back_to_agentrouter(monkeypatch):
    """AGENT_PROVIDER=openai (the operator, 2026-11-12): a batch runs on the
    ChatGPT login first; a refusal is retried once there, then AgentRouter."""
    import pytest

    from comic.agents import codex

    calls = []
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setattr(codex, "_log_error", lambda exc: None)
    monkeypatch.setattr(codex, "_log_fallback", lambda *a: None)
    monkeypatch.setattr(codex.time, "sleep", lambda s: None)

    def ok(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        return {"ok": True}

    monkeypatch.setattr(codex, "_run_once", ok)
    assert codex.run("p", [], {}) == {"ok": True} and calls == ["openai"]

    def blocked(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        raise codex.AgentError('{"error":{"code":"content-blocked"}}')

    calls.clear()
    monkeypatch.setattr(codex, "_run_once", blocked)
    with pytest.raises(codex.AgentError) as err:
        codex.run("p", [], {})
    assert calls == ["openai", "openai", "agentrouter"] and codex.is_blocked(err.value)


LIMIT = ("codex exec failed (1): ERROR: You've hit your usage limit. Upgrade to Pro "
         "(https://chatgpt.com/explore/pro) or try again at Nov 13th, 2026 1:14 AM.")


def test_a_chatgpt_first_batch_waits_out_the_usage_limit(monkeypatch):
    """2026-11-12: the cap made every later chapter fail at once. A ChatGPT-first
    batch now pauses until the reset time named in the message, then goes on."""
    from comic.agents import codex

    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setattr(codex, "_log_error", lambda exc: None)
    monkeypatch.setattr(codex, "_log_fallback", lambda *a: None)
    slept, calls = [], []
    monkeypatch.setattr(codex.time, "sleep", lambda s: slept.append(s))

    def capped_once(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        if len(calls) == 1:
            raise codex.AgentError(LIMIT)
        return {"ok": True}

    monkeypatch.setattr(codex, "_run_once", capped_once)
    assert codex.run("p", [], {}) == {"ok": True}
    assert calls == ["openai", "openai"] and slept and min(slept) >= 60
    assert str(codex.usage_limit_reset(Exception(LIMIT))) == "2026-11-13 01:14:00"


def test_a_capped_fallback_leaves_the_pages_blocked_for_later(monkeypatch):
    """AgentRouter-first: when ChatGPT is capped, the fallback does not wait --
    the pages stay blocked and are retried after the reset."""
    import pytest

    from comic.agents import codex

    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.setattr(codex, "_log_error", lambda exc: None)
    noted = []
    monkeypatch.setattr(codex, "_log_fallback", lambda outcome, exc: noted.append(outcome))
    monkeypatch.setattr(codex.time, "sleep", lambda s: None)

    def run_once(prompt, images, schema, model, provider="agentrouter"):
        if provider == "agentrouter":
            raise codex.AgentError('{"error":{"code":"content-blocked"}}')
        raise codex.AgentError(LIMIT)

    monkeypatch.setattr(codex, "_run_once", run_once)
    with pytest.raises(codex.AgentError) as err:
        codex.run("p", [], {})
    assert codex.is_blocked(err.value) and "PLUS-LIMIT" in noted


BANNED = ("codex exec failed (1): ERROR: unexpected status 403 Forbidden: 用户已被封禁 "
          "(request id: x), url: https://agentrouter.org/v1/responses")


def test_a_banned_key_moves_the_batch_to_chatgpt_only(tmp_path, monkeypatch):
    import pytest

    from comic.agents import codex

    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "k.key").write_text("sk-old", encoding="utf-8")
    monkeypatch.setattr(codex, "project_root", lambda: tmp_path)
    monkeypatch.setenv("AGENT_ROUTER_KEY_FILE", "k.key")
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.setattr(codex.time, "sleep", lambda s: None)
    calls = []

    def run_once(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        if provider == "agentrouter":
            raise codex.AgentError(BANNED)
        return {"ok": True}

    monkeypatch.setattr(codex, "_run_once", run_once)
    assert codex.run("p", [], {}) == {"ok": True} and calls == ["agentrouter", "openai"]
    assert codex.key_banned() and codex.primary_provider() == "openai"   # the rest of the batch too
    # a refusal is no longer sent to the banned key
    calls.clear()

    def refused(prompt, images, schema, model, provider="agentrouter"):
        calls.append(provider)
        raise codex.AgentError('{"error":{"code":"content-blocked"}}')

    monkeypatch.setattr(codex, "_run_once", refused)
    with pytest.raises(codex.AgentError) as err:
        codex.run("p", [], {})
    assert calls == ["openai", "openai"] and codex.is_blocked(err.value)
    # a fresh key in the same file lifts the ban
    (tmp_path / ".secrets" / "k.key").write_text("sk-new", encoding="utf-8")
    assert not codex.key_banned() and codex.primary_provider() == "agentrouter"
