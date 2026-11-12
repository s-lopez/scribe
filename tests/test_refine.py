from pathlib import Path

import pytest
from conftest import FakeOllama, claude_call

from scribe import refine
from scribe.config import Settings
from scribe.proc import ToolError


def test_system_prompt_includes_vocabulary(settings: Settings) -> None:
    prompt = refine.system_prompt(settings)
    assert "{vocabulary}" not in prompt
    assert "AuthKit" in prompt
    assert "Never translate" in prompt


def test_ollama_disables_thinking_and_sets_context(
    settings: Settings, ollama: tuple[FakeOllama, str]
) -> None:
    fake, _ = ollama
    out = refine.refine(settings, "um hello there", "ollama")
    assert out == "Hello there. So this is a test."
    body = fake.requests[0]
    assert body["model"] == "gemma4:12b"
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"]["num_ctx"] == settings.ollama.num_ctx
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"].endswith("um hello there")


def test_ollama_truncation_is_an_error(settings: Settings, ollama: tuple[FakeOllama, str]) -> None:
    fake, _ = ollama
    fake.reply("partial", done_reason="length")
    with pytest.raises(ToolError, match="context limit"):
        refine.refine(settings, "long text", "ollama")


def test_ollama_unreachable(settings: Settings) -> None:
    from scribe.config import Ollama

    down = settings.replace(ollama=Ollama(url="http://127.0.0.1:9", timeout_s=2))
    with pytest.raises(ToolError, match="not reachable"):
        refine.refine(down, "text", "ollama")


def test_claude_command_keeps_prompt_before_variadic_options(settings: Settings) -> None:
    cmd = refine.claude_command(settings, "SYSTEM")
    assert cmd[1:3] == ["-p", refine.USER_INSTRUCTION]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYSTEM"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd


def test_claude_runs_isolated_with_subscription_auth(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = refine.refine(settings, "um hello there", "claude")
    assert out == "UM HELLO THERE"
    call = claude_call(settings)
    assert call["stdin"] == "um hello there"
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert call["env"]["DISABLE_TELEMETRY"] == "1"
    assert "scribe-claude-" in call["cwd"]
    assert not Path(call["cwd"]).exists()  # temp folder cleaned up


def test_claude_failure_is_reported(settings: Settings) -> None:
    with pytest.raises(ToolError, match="boom"):
        refine.refine(settings, "FAIL please", "claude")


def test_empty_text_skips_backend(settings: Settings) -> None:
    assert refine.refine(settings, "  ", "claude") == ""


def test_unknown_backend(settings: Settings) -> None:
    with pytest.raises(ValueError):
        refine.refine(settings, "x", "gpt")


def test_claude_env_restores_user_for_keychain_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: launched from Raycast, USER is unset and claude can't find its keychain
    login ("Not logged in · Please run /login")."""
    import getpass

    for var in ("USER", "LOGNAME", "HOME"):
        monkeypatch.delenv(var, raising=False)
    env = refine.claude_env()
    assert env["USER"] == getpass.getuser()
    assert env["LOGNAME"] == env["USER"]
    assert Path(env["HOME"]).is_dir()


def test_claude_env_keeps_existing_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER", "someone")
    assert refine.claude_env()["USER"] == "someone"
