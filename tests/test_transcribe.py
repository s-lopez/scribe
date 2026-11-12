from pathlib import Path

import pytest

from scribe.config import Settings, Whisper
from scribe.proc import ToolError
from scribe.transcribe import normalize, transcribe, whisper_command


def test_whisper_command_uses_settings(settings: Settings, tmp_path: Path) -> None:
    cmd = whisper_command(settings, tmp_path / "in.wav", tmp_path / "out")
    assert cmd[0] == settings.tools.whisper
    assert cmd[cmd.index("-m") + 1] == str(settings.whisper.model)
    assert cmd[cmd.index("--prompt") + 1] == settings.whisper.vocabulary
    assert cmd[cmd.index("-mc") + 1] == "0"
    assert "--vad" in cmd and "-otxt" in cmd


def test_default_model_is_large_v3_not_turbo() -> None:
    assert Path(Whisper().model).name == "ggml-large-v3.bin"


def test_normalize_joins_segments() -> None:
    assert normalize(" Hello there.\n  How are you?\n\n") == "Hello there. How are you?"


def test_transcribe_reports_progress(settings: Settings, audio_file: Path) -> None:
    seen: list[int] = []
    text = transcribe(settings, audio_file, on_progress=seen.append)
    assert text == "um hello there so this is a test"
    assert seen == [10, 50, 100]


def test_transcribe_fails_clearly_without_model(settings: Settings, audio_file: Path) -> None:
    broken = settings.replace(whisper=Whisper(model=Path("/nope.bin")))
    with pytest.raises(ToolError, match="Model not found"):
        transcribe(broken, audio_file)
