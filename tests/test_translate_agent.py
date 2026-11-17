"""The agent-answered translate gate: batches carry earlier lines; unknown ids are dropped."""

from __future__ import annotations

import json

from comic.agents import translate_agent
from comic.paths import ChapterPaths


def test_batches_and_response(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    paths.handoff.mkdir(parents=True)
    ctx = tmp_path / "ctx.md"
    ctx.write_text("# S\n## Forms of address\nChris -> Allen: thần / Bệ hạ\n## How to answer\nDROP ME", encoding="utf-8")
    pages = [{"page": n, "overlay_path": str(tmp_path / f"{n}.png"),
              "items": [{"region_id": f"p{n:03d}_r01", "kind": "dialogue", "source_text": f"LINE {n}",
                         "suggested_max_chars": 30}]} for n in range(1, 6)]
    paths.gate_request("translate").write_text(json.dumps({"context_path": str(ctx), "pages": pages}), encoding="utf-8")
    prompts = []

    def fake_run(prompt, images, schema, model):
        if "## Translate these regions" not in prompt:            # the chapter-summary call
            assert not images and "LINE 5" in prompt and "LINE 1" in prompt
            return {"chapter_summary": "S"}
        prompts.append(prompt)
        ids = [l.split()[1] for l in prompt.split("## Translate these regions")[1].splitlines() if l.startswith("- p")]
        return {"items": [{"region_id": r, "target_text": f"VI {r}", "speaker": "Chris", "addressee": "Allen", "voice": None}
                          for r in ids] + [{"region_id": "p099_r99", "target_text": "X", "speaker": None, "addressee": None, "voice": None}],
                "address_pairs": [], "new_glossary": [],
                "chapter_summary": "S" if "LAST batch" in prompt else None}

    monkeypatch.setattr(translate_agent, "run", fake_run)
    out = json.loads(translate_agent.answer_translate(paths).read_text(encoding="utf-8"))
    assert len(prompts) == 2                                   # 4 pages + 1 page
    assert "DROP ME" not in prompts[0] and "thần / Bệ hạ" in prompts[0]
    assert "VI p004_r01" in prompts[1]                         # nearby lines carried forward
    assert "VI p001_r01" not in prompts[1] and "LINE 1" not in prompts[1]   # only pages near the batch
    assert [i["region_id"] for i in out["items"]] == [f"p{n:03d}_r01" for n in range(1, 6)]
    assert out["chapter_summary"] == "S" and "voice" not in out["items"][0]


def test_blocked_batch_is_left_for_the_agent(tmp_path, monkeypatch):
    from comic.agents.codex import AgentError

    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    paths.handoff.mkdir(parents=True)
    ctx = tmp_path / "ctx.md"
    ctx.write_text("# S", encoding="utf-8")
    pages = [{"page": n, "overlay_path": str(tmp_path / f"{n}.png"),
              "items": [{"region_id": f"p{n:03d}_r01", "kind": "dialogue", "source_text": f"LINE {n}",
                         "suggested_max_chars": 30}]} for n in range(1, 6)]
    paths.gate_request("translate").write_text(json.dumps({"context_path": str(ctx), "pages": pages}),
                                               encoding="utf-8")

    def fake_run(prompt, images, schema, model):
        if "## Translate these regions" not in prompt:
            return {"chapter_summary": "S"}
        if "LINE 1" in prompt.split("## Translate these regions")[1]:
            raise AgentError('codex exec failed (1): {"error":{"code":"content-blocked"}}')
        ids = [l.split()[1] for l in prompt.split("## Translate these regions")[1].splitlines()
               if l.startswith("- p")]
        return {"items": [{"region_id": r, "target_text": "VI", "speaker": None,
                           "addressee": None, "voice": None} for r in ids],
                "address_pairs": [], "new_glossary": [], "chapter_summary": None}

    monkeypatch.setattr(translate_agent, "run", fake_run)
    out = json.loads(translate_agent.answer_translate(paths).read_text(encoding="utf-8"))
    assert sorted(i["region_id"] for i in out["items"]) == ["p002_r01", "p003_r01", "p004_r01", "p005_r01"]
    blocked = json.loads((paths.handoff / "translate_blocked.json").read_text(encoding="utf-8"))
    assert blocked == {"pages": [1]}                    # pages 2-4 retried one by one
