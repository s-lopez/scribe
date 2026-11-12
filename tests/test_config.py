from pathlib import Path

import pytest

from scribe import config
from scribe.config import HOME, ConfigError, Settings, load_settings


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "nope.toml")
    assert settings.source is None
    assert settings.whisper.vocabulary == ""
    assert settings.whisper.language == "auto"
    assert settings.ollama.model == "gemma4:12b"


def test_overrides_only_what_is_set(tmp_path: Path) -> None:
    path = tmp_path / "scribe.toml"
    path.write_text(
        'sessions_dir = "elsewhere/sessions"\n'
        "[whisper]\n"
        'vocabulary = "Grafana."\n'
        "threads = 4\n"
        "[ollama]\n"
        "temperature = 0\n"  # an int where a float is expected is fine
        "[tools]\n"
        'claude = "~/bin/claude"\n'
    )
    settings = load_settings(path)
    assert settings.source == path
    assert settings.sessions_dir == HOME / "elsewhere" / "sessions"
    assert settings.whisper.vocabulary == "Grafana."
    assert settings.whisper.threads == 4
    assert settings.whisper.beam_size == 5  # untouched default
    assert settings.ollama.temperature == 0.0
    assert settings.tools.claude == "~/bin/claude"


@pytest.mark.parametrize(
    "content, message",
    [
        ("[whisper]\nvocabulary = 3\n", "expected str"),
        ("[whisper]\nvocabulary_typo = 'x'\n", "unknown setting 'vocabulary_typo'"),
        ("colour = 'blue'\n", "unknown setting 'colour'"),
        ("whisper = 'x'\n", "must be a table"),
        ("[whisper\n", "scribe.toml"),
        ("[whisper]\nthreads = true\n", "expected int"),
    ],
)
def test_invalid_settings_are_reported(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "scribe.toml"
    path.write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_settings(path)


def test_config_path_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "custom.toml"
    path.write_text('[claude]\nmodel = "sonnet"\n')
    monkeypatch.setenv("SCRIBE_CONFIG", str(path))
    assert load_settings().claude.model == "sonnet"


def test_tools_are_found_with_a_minimal_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raycast and Shortcuts start processes with a bare PATH."""
    tool = tmp_path / "whisper-cli"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(config, "TOOL_DIRS", (str(tmp_path),))
    assert config.find_tool("whisper-cli") == str(tool)
    assert config.find_tool("no-such-tool-xyz") == "no-such-tool-xyz"


def test_example_file_is_valid_and_matches_defaults(tmp_path: Path) -> None:
    """scribe.example.toml documents the defaults; uncommenting it must change nothing."""
    text = (HOME / "scribe.example.toml").read_text()
    uncommented = "\n".join(
        line[2:] if line.startswith("# ") and " = " in line else line for line in text.splitlines()
    )
    path = tmp_path / "example.toml"
    path.write_text(uncommented)
    loaded = load_settings(path)
    defaults = Settings()
    for section in ("whisper", "ollama", "claude"):
        expected = getattr(defaults, section)
        got = getattr(loaded, section)
        for name in expected.__dataclass_fields__:
            if section == "whisper" and name == "vocabulary":
                continue  # the example shows a sample vocabulary
            assert getattr(got, name) == getattr(expected, name), f"{section}.{name}"
    assert loaded.sessions_dir == defaults.sessions_dir
    assert loaded.log_file == defaults.log_file
