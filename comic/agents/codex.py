"""Calling AgentRouter models through the Codex CLI (the operator, 2026-11-08).

AgentRouter refuses plain API calls ("unauthorized client detected") and serves
its supported clients; the Codex CLI is one, already configured on this machine
(``~/.codex/config.toml``: provider ``agentrouter``, key from the environment
variable ``AGENT_ROUTER_TOKEN``). So each call is one ``codex exec``:

- the key is read from ``.secrets/agentrouter.key`` (git-ignored), only into
  that one process's environment;
- read-only sandbox, ephemeral session, working root an empty scratch folder,
  so the agent sees nothing of the project but the images it is handed;
- ``--output-schema`` forces a JSON answer; ``-o`` captures it.

Calls run one at a time with a pause between them: AgentRouter's terms restrict
high-volume automated traffic, and a cut quota stops the pipeline.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from comic.paths import project_root

DEFAULT_MODEL = "gpt-6.1-sol"
PAUSE_S = 5.0
TIMEOUT_S = 900
#: Waits before retrying a call that failed for a passing reason (rate limit,
#: dropped stream). A refusal by the content filter is never retried.
RETRY_WAITS_S = (30, 120)
TRANSIENT = ("rate limit", "stream disconnected", "reconnecting", "timed out", "502", "503", "504")


def is_blocked(exc: Exception) -> bool:
    """AgentRouter's own content filter refused the request. Not retried, not
    rephrased: those lines go back to the agent (the operator, 2026-11-09)."""
    return "content-blocked" in str(exc)


class AgentError(RuntimeError):
    pass


def _key_file() -> Path:
    name = os.environ.get("AGENT_ROUTER_KEY_FILE", "agentrouter.key")
    return project_root() / ".secrets" / Path(name).name


def _key() -> str:
    # One key per batch process (the operator's accounts, 2026-11-10): each
    # batch stays paced one call at a time on its own key.
    f = _key_file()
    if not f.exists():
        raise AgentError(f"no AgentRouter key at {f}")
    return f.read_text(encoding="utf-8").strip()


#: AgentRouter banned the account behind a key: "403 Forbidden: 用户已被封禁"
#: (2026-11-13). Retrying is pointless; the batch moves to ChatGPT only (the
#: operator: "if that happens, switch over to using pure GPT").
BAN_MARKS = ("用户已被封禁", "用戶已被封禁", "has been banned", "account banned")


def is_banned(exc: Exception) -> bool:
    text = str(exc)
    return "403" in text and any(m in text.lower() for m in BAN_MARKS)


def _key_digest() -> str | None:
    import hashlib

    try:
        return hashlib.sha256(_key().encode("utf-8")).hexdigest()[:16]
    except AgentError:
        return None


def key_banned() -> bool:
    """The key file in use was banned. The marker holds the banned key's digest,
    so putting a fresh key into the same file lifts it."""
    marker = _key_file().with_name(_key_file().name + ".banned-marker")
    if not marker.exists():
        return False
    digest = _key_digest()
    return digest is None or marker.read_text(encoding="utf-8").strip() == digest


def _mark_banned(exc: Exception) -> None:
    marker = _key_file().with_name(_key_file().name + ".banned-marker")
    try:
        marker.write_text(_key_digest() or "missing", encoding="utf-8")
        log = project_root() / "workspace" / "_logs" / "agentrouter_bans.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} KEY-BANNED {_key_file().name} -> ChatGPT only | "
                    f"{(str(exc).splitlines() or [''])[-1][:160]}\n")
    except OSError:
        pass


def _codex() -> str:
    exe = shutil.which("codex") or shutil.which("codex.cmd")
    if not exe:
        raise AgentError("the Codex CLI is not installed (npm i -g @openai/codex)")
    return exe


#: AgentRouter's front end rejects large bodies (413 with six full-size PNG
#: sheets, 2026-11-08): send each image as a JPEG no wider than this.
MAX_SIDE = 1600
JPEG_QUALITY = 82


def _shrink(src: Path, dst: Path) -> Path:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        scale = min(1.0, MAX_SIDE / max(im.size))
        if scale < 1.0:
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        im.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return dst


_last_call = 0.0


#: Single pages that may fail in a row before the run counts as an outage.
MAX_SINGLE_FAILURES = 3


#: The ChatGPT plan's usage cap ("You've hit your usage limit ... try again at
#: Nov 13th, 2026 1:14 AM", 2026-11-12). Not a refusal and not a passing blip.
USAGE_LIMIT = "hit your usage limit"
#: Wait used when the reset time cannot be read from the message.
USAGE_LIMIT_FALLBACK_WAIT_S = 1800


def is_usage_limit(exc: Exception) -> bool:
    return USAGE_LIMIT in str(exc).lower()


def usage_limit_reset(exc: Exception):
    """The reset time ChatGPT names in its usage-limit message, or None.

    Two shapes seen: "try again at Nov 13th, 2026 1:14 AM" and, later the same
    day, just "try again at 12:51 PM" (today, or tomorrow if already past)."""
    import re
    from datetime import datetime, timedelta

    text = str(exc)
    m = re.search(r"try again at ([A-Z][a-z]{2})[a-z]* (\d{1,2})(?:st|nd|rd|th)?,? (\d{4}),? (\d{1,2}):(\d{2}) ?([AP]M)",
                  text)
    if m:
        mon, day, year, hh, mm, ampm = m.groups()
        try:
            return datetime.strptime(f"{mon} {day} {year} {hh}:{mm} {ampm}", "%b %d %Y %I:%M %p")
        except ValueError:
            return None
    m = re.search(r"try again at (\d{1,2}):(\d{2}) ?([AP]M)", text)
    if not m:
        return None
    hh, mm, ampm = m.groups()
    t = datetime.strptime(f"{hh}:{mm} {ampm}", "%I:%M %p").time()
    when = datetime.combine(datetime.now().date(), t)
    return when if when > datetime.now() - timedelta(minutes=5) else when + timedelta(days=1)


def _wait_for_reset(exc: Exception) -> None:
    """A ChatGPT-first batch pauses on its chapter until the plan's cap resets,
    instead of failing every chapter after it (the operator, 2026-11-13)."""
    from datetime import datetime

    reset = usage_limit_reset(exc)
    wait = (reset - datetime.now()).total_seconds() + 60 if reset else USAGE_LIMIT_FALLBACK_WAIT_S
    _log_fallback("GPT-LIMIT-WAIT", exc)
    time.sleep(max(60.0, wait))


def is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return not is_blocked(exc) and any(w in text for w in TRANSIENT)


#: Wait before the single retry of a call reported as "content-blocked". The
#: operator (2026-11-11) suspects some of those are an unstable connection,
#: not the filter: KS ch.3 p5/6/17/18 were refused yet hold nothing a filter
#: would object to. The prompt is resent unchanged -- never reworded.
BLOCKED_RETRY_WAIT_S = 60


def _log_error(exc: Exception) -> None:
    """Keep the full error text, so a refusal can be told from a dropped stream."""
    try:
        log = project_root() / "workspace" / "_logs" / "agent_errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} key={os.environ.get('AGENT_ROUTER_KEY_FILE', 'agentrouter.key')}\n{exc}\n")
    except OSError:
        pass


#: The operator's own ChatGPT (Plus) login in the Codex CLI (2026-11-11): a
#: request AgentRouter still refuses after its one unchanged retry is sent there
#: once, unchanged. Refused there too, it stays "content-blocked" and goes back
#: to the operator. Codex's built-in provider id for that login is ``openai``.
FALLBACK_PROVIDER = "openai"
#: Provider that answered the last successful run() -- callers note Plus answers
#: so the agent reviews them (the operator: Plus "can sometimes be incorrect").
last_provider = "agentrouter"


def primary_provider() -> str:
    """``AGENT_PROVIDER=openai`` runs a batch on Codex with the operator's own
    ChatGPT login first, AgentRouter as its fallback (the operator, 2026-11-12:
    AgentRouter may be overloaded; part of the work moves to Codex directly).
    Default: AgentRouter first, ChatGPT as the fallback. A banned key means
    ChatGPT only, with no AgentRouter fallback."""
    if os.environ.get("AGENT_PROVIDER", "").strip().lower() == FALLBACK_PROVIDER or key_banned():
        return FALLBACK_PROVIDER
    return "agentrouter"


def note_plus(paths, gate: str, region_ids) -> None:
    """Record regions answered by the Plus fallback, for the agent to review."""
    if last_provider != FALLBACK_PROVIDER or primary_provider() == FALLBACK_PROVIDER:
        return      # Codex-only runs answer everything there: not a fallback to review
    try:
        log = project_root() / "workspace" / "_logs" / "plus_fallback.log"
        ids = sorted(set(region_ids))
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} PLUS-ANSWERED {gate} | {paths.series} | "
                    f"{paths.workspace.name} | {' '.join(ids)}\n")
    except OSError:
        pass


def _log_fallback(outcome: str, exc: Exception) -> None:
    try:
        log = project_root() / "workspace" / "_logs" / "plus_fallback.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {outcome} | {(str(exc).splitlines() or [''])[-1][:200]}\n")
    except OSError:
        pass


def run(prompt: str, images: list[Path], schema: dict, model: str = DEFAULT_MODEL) -> dict:
    """One Codex call, retried with growing waits when it failed for a passing
    reason; a "blocked" answer is retried once, unchanged, then sent once to the
    operator's ChatGPT login before it counts as blocked."""
    global last_provider
    provider = primary_provider()
    last_provider = provider
    blocked_retried = False
    waits = list(RETRY_WAITS_S)
    while True:
        try:
            return _run_once(prompt, images, schema, model, provider=provider)
        except AgentError as exc:
            _log_error(exc)
            if provider != FALLBACK_PROVIDER and is_banned(exc):
                _mark_banned(exc)
                provider = last_provider = FALLBACK_PROVIDER
                continue
            if provider == FALLBACK_PROVIDER and is_usage_limit(exc):
                _wait_for_reset(exc)
                continue
            if is_blocked(exc):
                if blocked_retried:
                    if _other(provider) != FALLBACK_PROVIDER and key_banned():
                        raise AgentError(f"content-blocked (ChatGPT; AgentRouter key banned): {exc}") from exc
                    return _fallback(prompt, images, schema, model, exc, _other(provider))
                blocked_retried = True
                time.sleep(BLOCKED_RETRY_WAIT_S)
                continue
            if not waits or not is_transient(exc):
                raise
            time.sleep(waits.pop(0))


def _other(provider: str) -> str:
    return "agentrouter" if provider == FALLBACK_PROVIDER else FALLBACK_PROVIDER


def _fallback(prompt, images, schema, model, blocked: AgentError,
              provider: str = FALLBACK_PROVIDER) -> dict:
    waits = list(RETRY_WAITS_S)
    while True:
        try:
            answer = _run_once(prompt, images, schema, model, provider=provider)
            global last_provider
            last_provider = provider
            _log_fallback("PLUS-OK", blocked)
            return answer
        except AgentError as exc:
            _log_error(exc)
            if provider != FALLBACK_PROVIDER and is_banned(exc):
                _mark_banned(exc)
                raise AgentError(f"content-blocked (ChatGPT; AgentRouter key banned): {exc}") from exc
            if is_transient(exc) and waits:
                time.sleep(waits.pop(0))
                continue
            if provider == FALLBACK_PROVIDER and is_usage_limit(exc):
                # ChatGPT is capped, not refusing: the pages stay blocked and the
                # retry watcher sends them to ChatGPT after the reset.
                _log_fallback("PLUS-LIMIT", exc)
                raise AgentError(f"content-blocked (ChatGPT usage limit, retry after reset): {exc}") from exc
            _log_fallback("PLUS-REFUSED", exc)
            # Keep the "content-blocked" marker so callers hand it back as before.
            raise AgentError(f"content-blocked (AgentRouter and ChatGPT): {exc}") from exc


def _run_once(prompt: str, images: list[Path], schema: dict, model: str,
              provider: str = "agentrouter") -> dict:
    """One Codex call: prompt + images in, a JSON object matching ``schema`` out."""
    global _last_call
    wait = PAUSE_S - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    with tempfile.TemporaryDirectory(prefix="comic_agent_") as tmp:
        tmp = Path(tmp)
        schema_file = tmp / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        out_file = tmp / "answer.json"
        work = tmp / "work"
        work.mkdir()
        cmd = [_codex(), "exec", "--skip-git-repo-check", "--ephemeral",
               "--sandbox", "read-only", "--color", "never", "-C", str(work),
               # Name the provider here, so ~/.codex/config.toml's default can be the
               # operator's own ChatGPT (Plus) login without moving these calls off AgentRouter.
               "-c", f"model_provider={provider}",
               "-m", model, "--output-schema", str(schema_file), "-o", str(out_file)]
        for n, image in enumerate(images):
            cmd += ["-i", str(_shrink(image, tmp / f"img{n:02d}.jpg"))]
        env = dict(os.environ, AGENT_ROUTER_TOKEN=_key()) if provider == "agentrouter" else dict(os.environ)
        try:
            proc = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8",
                                  capture_output=True, env=env, timeout=TIMEOUT_S)
        finally:
            _last_call = time.time()
        if proc.returncode != 0 or not out_file.exists():
            # Codex echoes the whole prompt on stderr; keep the lines that say what went wrong.
            text = (proc.stderr or "") + "\n" + (proc.stdout or "")
            errors = [ln for ln in text.splitlines()
                      if any(w in ln.lower() for w in ("error", "failed", "denied", "limit", "status"))]
            last = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()][-4:]
            tail = "\n".join(dict.fromkeys(errors[-6:] + last)) or text[-600:]
            raise AgentError(f"codex exec failed ({proc.returncode}): {tail}")
        raw = out_file.read_text(encoding="utf-8").strip()
    try:
        return json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise AgentError(f"answer is not JSON: {raw[:300]}") from None
