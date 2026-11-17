---
name: agentrouter-addon
description: AgentRouter ($400 credit, user 2026-11-08) is an ADD-ON for transcription check/QA via the Codex CLI; when credit runs out everything returns to me
metadata:
  type: project
---

User (2026-11-08): use AgentRouter for QA and transcription extraction to save tokens while the $400 free credit
lasts; it is only an add-on -- when the credit is gone, I handle everything again. The user changed the
"one agent" rule for this.

- AgentRouter rejects plain API calls ("unauthorized client detected") -- only supported clients. Route used:
  the Codex CLI already configured (`~/.codex/config.toml`, provider agentrouter, env `AGENT_ROUTER_TOKEN`,
  model gpt-6.1-sol, vision works). Do NOT spoof a client to call the API directly.
- Key in `.secrets/agentrouter.key` (git-ignored). Code: comic/agents/codex.py (one paced `codex exec`, read-only,
  ephemeral, empty cwd, --output-schema), comic/agents/check_agent.py, prompt comic/agents/prompts/check.md
  (user reviews prompts). `comic check --agent` answers + applies; plain `comic check` unchanged (fallback).
- ~11k tokens overhead per Codex call -> batch 6 sheets/call. Terms restrict high-volume automated use: keep paced.
- TRANSLATION too (user 2026-11-08): `comic translate --agent` (comic/agents/translate_agent.py, prompt
  prompts/translate.md): whole context file (forms of address, glossary, summaries) + whole chapter source
  + lines already translated, 4 pages/call with overlay images; resume validates + pins pairs (established wins).
  Trial NH ch.8 p2-5: pronouns all correct; fixed in prompt: keep names used as vocatives, speakers from image.
- Held pages (minors' content) are never in a check request, so never sent. I do not write prompts aimed at that
  content.
- **Content filter (2026-11-09):** AgentRouter refuses adult sexual pages with `content-blocked` (MID ch.0 p15).
  Never reword/obfuscate to get past it. Batches that are blocked get retried page by page (sheet by sheet for the check); pages
  still refused are written to `handoff/translate_blocked.json` / `check_blocked.json` and I answer them myself
  (`comic translate --missing` / `comic check -p`), adult content translated normally per [[content-boundaries]].
  Re-cut batch: `workspace/_tools/recut_agent.sh "<series>" ch...` logs `NEEDS-ME` lines.

- **Five keys (user, 2026-11-10):** `.secrets/agentrouter.key` + `agentrouter_2..5.key` (user's own accounts: two $400,
  two $200, which is which not stated). `AGENT_ROUTER_KEY_FILE=agentrouter_N.key` picks one per process
  (codex.py `_key`). Rule: ONE batch per key, each still paced one call at a time -- never rotate keys to dodge a
  rate limit. When a key's credit runs out, move its batch to another key.
- **2026-11-11 (operator):** suspects "blocked" pages are often an unstable connection, not the filter (KS 3
  p5/6/17/18 were innocuous). codex.run now logs every failure's full text to workspace/_logs/agent_errors.log
  and resends a "content-blocked" call ONCE unchanged after 60 s before leaving the page to me. Check that log
  before assuming a refusal. (The error the code matches is the server's own JSON `{"error":{"code":"content-blocked"}}`.)

**Plus fallback (user 2026-11-11):** the user logged the Codex CLI into their own ChatGPT Plus account (Google sign-in;
`codex login status` = "Logged in using ChatGPT"). comic/agents/codex.py: a request AgentRouter refuses
("content-blocked", after its one unchanged retry) is sent ONCE unchanged to provider `openai` (that login, same
model gpt-6.1-sol); log workspace/_logs/plus_fallback.log (PLUS-OK / PLUS-REFUSED). Refused there too -> stays
content-blocked -> NEEDS-ME -> I (Claude) translate/handle them myself (content rules apply), THEN tell the user (user correction 2026-11-11: "you should be the one translating it ... before letting me know").
**Plus is not trusted blindly** (but never delete its lines -- refine only, [[plus-review-keep]]): every Plus answer is logged as `PLUS-ANSWERED <gate> | series | Chapter | region ids` in plus_fallback.log -- I review those lines (translation and check) before the chapter is handed over for PS.
AgentRouter calls name `-c model_provider=agentrouter` explicitly, so config.toml's default may be changed freely.

**Ban auto-switch (user 2026-11-13: "keys could get banned any moment; if so, switch to pure GPT"):** comic/agents/codex.py
detects 403 "用户已被封禁" -> writes .secrets/<keyfile>.banned-marker (digest of that key) + workspace/_logs/agentrouter_bans.log,
and every later call on that key file runs ChatGPT only (no AgentRouter fallback). A fresh key written into the same file lifts
it. A chapter already mid-run with old code may still fail: release its lock in workspace/_locks/agent_2026-11-12.
