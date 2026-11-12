import json
import os
import threading
import time
from pathlib import Path

import pytest

from scribe import dictate
from scribe.config import Settings


def test_parse_command() -> None:
    assert dictate.parse_command("stop\n") == "stop"
    assert dictate.parse_command("discard") == "discard"
    with pytest.raises(ValueError):
        dictate.parse_command("stop claude")


def _run_dictation(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str | None,
) -> tuple[int, list[dict]]:
    """Run `scribe dictate`, sending `command` once recording is underway (None = close stdin)."""
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr("sys.stdin", os.fdopen(read_fd, "r"))

    def controller() -> None:
        deadline = time.monotonic() + 10
        while not list(settings.sessions_dir.glob("*/recording.wav")):
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
        if command:
            os.write(write_fd, (command + "\n").encode())
        os.close(write_fd)

    thread = threading.Thread(target=controller)
    thread.start()
    code = dictate.run(settings)
    thread.join()
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return code, events


def test_dictation_stops_after_transcription(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, events = _run_dictation(settings, monkeypatch, capsys, "stop")
    assert code == 0
    assert events[0]["event"] == "recording"
    assert {"event": "stage", "stage": "transcribing"} in events
    done = events[-1]
    assert done["event"] == "transcribed"
    assert done["text"] == "um hello there so this is a test"

    session = Path(done["session"])
    files = sorted(p.name for p in session.iterdir())
    assert "audio.m4a" in files and "recording.wav" not in files
    assert "transcript.txt" in files
    assert not any(f.startswith("refined.") for f in files)  # refinement is a separate step
    meta = json.loads((session / "meta.json").read_text())
    assert meta["kind"] == "dictation"
    assert meta["audio"] == "audio.m4a"
    assert meta["duration_s"] == 1.0
    assert [r["step"] for r in meta["runs"]] == ["transcribe"]


@pytest.mark.parametrize("command", ["discard", None])
def test_discard_removes_the_session(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str | None,
) -> None:
    code, events = _run_dictation(settings, monkeypatch, capsys, command)
    assert code == 0
    assert events[-1]["event"] == "discarded"
    assert events[-1]["reason"] == (
        "discard command" if command else "stdin closed (EOF) before a stop command"
    )
    assert not [p for p in settings.sessions_dir.iterdir() if p.is_dir()]


def test_debug_log_records_the_session(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scribe import debuglog

    debuglog.setup(settings.log_file)
    _run_dictation(settings, monkeypatch, capsys, "stop")
    text = settings.log_file.read_text()
    for expected in ("stdin=", "recorder started", "stdin read b'stop", "transcribed", "emit"):
        assert expected in text
