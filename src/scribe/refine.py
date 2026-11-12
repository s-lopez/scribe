"""Transcript refinement backends: local Ollama or the Claude Code CLI."""

from __future__ import annotations

import json
import os
import pwd
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from scribe.config import Settings, search_path
from scribe.proc import ToolError, run

BACKENDS = ("ollama", "claude")
USER_INSTRUCTION = "Clean up this transcript:"


def system_prompt(settings: Settings) -> str:
    template = settings.refine_prompt.read_text()
    vocabulary = settings.whisper.vocabulary.strip()
    if not vocabulary:
        # No glossary configured: drop the line that would introduce it.
        template = "\n".join(line for line in template.splitlines() if "{vocabulary}" not in line)
    return template.replace("{vocabulary}", vocabulary).strip()


def backend_model(settings: Settings, backend: str) -> str:
    return settings.ollama.model if backend == "ollama" else settings.claude.model


def refine(
    settings: Settings,
    text: str,
    backend: str,
    *,
    log: Callable[[str], None] | None = None,
) -> str:
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}; choose from {', '.join(BACKENDS)}")
    if not text.strip():
        return ""
    prompt = system_prompt(settings)
    result = (refine_ollama if backend == "ollama" else refine_claude)(settings, prompt, text, log)
    result = result.strip()
    if not result:
        raise ToolError(f"{backend} returned an empty result")
    return result


def refine_ollama(
    settings: Settings, prompt: str, text: str, log: Callable[[str], None] | None = None
) -> str:
    o = settings.ollama
    body = {
        "model": o.model,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"{USER_INSTRUCTION}\n\n{text}"},
        ],
        "options": {"temperature": o.temperature, "num_ctx": o.num_ctx},
    }
    request = urllib.request.Request(
        f"{o.url}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    if log:
        log(f"POST {o.url}/api/chat model={o.model} think=false num_ctx={o.num_ctx}")
    try:
        with urllib.request.urlopen(request, timeout=o.timeout_s) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        raise ToolError(f"Ollama error {e.code}: {e.read().decode(errors='replace')}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ToolError(f"Ollama not reachable at {o.url} ({e}). Is it running?") from e
    if log:
        stats = {k: data.get(k) for k in ("done_reason", "prompt_eval_count", "eval_count")}
        log(f"ollama: {stats}")
    if data.get("done_reason") == "length":
        raise ToolError("Ollama hit the context limit; the transcript is too long (raise num_ctx)")
    if (data.get("prompt_eval_count") or 0) >= o.num_ctx:
        raise ToolError("Transcript exceeds Ollama's num_ctx and would be truncated")
    return data.get("message", {}).get("content", "")


def claude_command(settings: Settings, prompt: str) -> list[str]:
    # The prompt is a positional argument and must come before the variadic --tools/
    # --disallowedTools options, or they would swallow it.
    return [
        settings.tools.claude,
        "-p",
        USER_INSTRUCTION,
        "--model", settings.claude.model,
        "--system-prompt", prompt,
        "--no-session-persistence",
        "--strict-mcp-config",
        "--tools", "",
        "--disallowedTools", "mcp__*",
    ]  # fmt: skip


def claude_env() -> dict[str, str]:
    env = dict(os.environ)
    # Use the subscription login, never API credentials.
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    # Claude Code finds its login in the macOS keychain by username ($USER). Raycast and
    # Shortcuts start processes without USER/LOGNAME, which makes claude report "Not logged in".
    account = pwd.getpwuid(os.getuid())
    env.setdefault("USER", account.pw_name)
    env.setdefault("LOGNAME", account.pw_name)
    env.setdefault("HOME", account.pw_dir)
    env["DISABLE_TELEMETRY"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["PATH"] = search_path()
    return env


def refine_claude(
    settings: Settings, prompt: str, text: str, log: Callable[[str], None] | None = None
) -> str:
    # Run in an empty folder so no CLAUDE.md or project settings get picked up.
    with tempfile.TemporaryDirectory(prefix="scribe-claude-") as tmp:
        return run(
            claude_command(settings, prompt),
            input=text,
            cwd=Path(tmp),
            env=claude_env(),
            timeout=settings.claude.timeout_s,
            log=log,
        )
