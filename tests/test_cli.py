import io
import json
from pathlib import Path

import pytest

from scribe import cli, sessions
from scribe.config import Settings


def _only_session(settings: Settings) -> sessions.Session:
    found = [p for p in settings.sessions_dir.iterdir() if p.is_dir()]
    assert len(found) == 1
    return sessions.Session(found[0])


def test_transcribe_file_creates_session(
    settings: Settings, audio_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["transcribe", str(audio_file)], settings) == 0
    assert capsys.readouterr().out.strip() == "um hello there so this is a test"

    session = _only_session(settings)
    assert session.name.endswith("_Voice memo 1")
    assert (session.path / "audio.m4a").read_bytes() == audio_file.read_bytes()
    meta = session.meta()
    assert meta["kind"] == "file"
    assert meta["source"] == str(audio_file.resolve())
    assert meta["runs"][0]["model"] == "model.bin"


def test_transcribe_and_refine(
    settings: Settings, audio_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["transcribe", "--refine", "ollama", str(audio_file)], settings) == 0
    assert capsys.readouterr().out.strip() == "Hello there. So this is a test."
    session = _only_session(settings)
    assert len(list(session.path.glob("refined.ollama.*.txt"))) == 1


def test_refine_failure_keeps_transcript(settings: Settings, tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("FAIL this one")
    assert cli.main(["refine", "--backend", "claude", str(text)], settings) == 1
    session = _only_session(settings)
    assert session.transcript.read_text() == "FAIL this one"
    assert not list(session.path.glob("refined.*"))
    assert "refine failed" in session.log_path.read_text()


def test_refine_outside_text_file_makes_new_session(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = tmp_path / "Meeting notes.txt"
    text.write_text("um so the plan")
    assert cli.main(["refine", "--backend", "claude", str(text)], settings) == 0
    assert capsys.readouterr().out.strip() == "UM SO THE PLAN"
    session = _only_session(settings)
    assert session.meta()["kind"] == "text"
    assert session.name.endswith("_Meeting notes")
    assert session.meta()["runs"][0]["input"] == "transcript.txt"


def test_refine_existing_session_twice_keeps_both(
    settings: Settings, audio_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["transcribe", str(audio_file)], settings)
    session = _only_session(settings)
    cli.main(["refine", "--backend", "claude", str(session.path)], settings)
    cli.main(["refine", "--backend", "ollama", str(session.path / "audio.m4a")], settings)
    assert len(list(session.path.glob("refined.claude.*.txt"))) == 1
    assert len(list(session.path.glob("refined.ollama.*.txt"))) == 1
    assert [r["step"] for r in session.meta()["runs"]] == ["transcribe", "refine", "refine"]
    assert len(list(settings.sessions_dir.iterdir())) == 2  # one session + the lock file


def test_background_copies_input_before_detaching(
    settings: Settings, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    detached: list[list[str]] = []
    monkeypatch.setattr(cli, "_detach", detached.append)
    assert (
        cli.main(["transcribe", "--background", "--refine", "claude", str(audio_file)], settings)
        == 0
    )
    session = _only_session(settings)
    audio_file.unlink()  # the share sheet's temp file may vanish right away
    assert (session.path / "audio.m4a").is_file()
    assert detached == [["_process", "--refine", "claude", str(session.path)]]
    # The worker then does the job on its own.
    assert cli.main(detached[0], settings) == 0
    assert len(list(session.path.glob("refined.claude.*.txt"))) == 1


def test_ask_cancelled_does_nothing(
    settings: Settings, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.macos, "choose", lambda *a: None)
    assert cli.main(["transcribe", "--ask", str(audio_file)], settings) == 0
    assert not settings.sessions_dir.exists()


def test_ask_picks_backend(
    settings: Settings, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.macos, "choose", lambda s, prompt, options: options[2])
    assert cli.main(["transcribe", "--ask", str(audio_file)], settings) == 0
    session = _only_session(settings)
    runs = session.meta()["runs"]
    assert runs[-1]["backend"] == "claude"
    assert json.loads((session.path / "meta.json").read_text()) == session.meta()


def _json_events(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def _transcribed_session(
    settings: Settings, audio_file: Path, capsys: pytest.CaptureFixture[str]
) -> sessions.Session:
    cli.main(["transcribe", str(audio_file)], settings)
    capsys.readouterr()
    return _only_session(settings)


def test_refine_edited_text_from_stdin(
    settings: Settings,
    audio_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _transcribed_session(settings, audio_file, capsys)
    monkeypatch.setattr("sys.stdin", io.StringIO("um hello THERE, edited\n"))
    args = ["refine", "--backend", "claude", "--stdin", "--json", str(session.path)]
    assert cli.main(args, settings) == 0

    events = _json_events(capsys)
    assert events[0] == {"event": "stage", "stage": "refining", "backend": "claude"}
    done = events[-1]
    assert done["event"] == "refined"
    assert done["text"] == "UM HELLO THERE, EDITED"
    # The raw transcript is untouched; the edit is kept and recorded as the refine input.
    assert session.transcript.read_text().strip() == "um hello there so this is a test"
    edited = list(session.path.glob("transcript.edited.*.txt"))
    assert len(edited) == 1
    assert session.meta()["runs"][-1]["input"] == edited[0].name
    assert Path(done["output"]).read_text().strip() == "UM HELLO THERE, EDITED"


def test_refine_unedited_text_uses_transcript(
    settings: Settings,
    audio_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _transcribed_session(settings, audio_file, capsys)
    monkeypatch.setattr("sys.stdin", io.StringIO(session.transcript.read_text()))
    args = ["refine", "--backend", "ollama", "--stdin", "--json", str(session.path)]
    assert cli.main(args, settings) == 0
    assert _json_events(capsys)[-1]["event"] == "refined"
    assert not list(session.path.glob("transcript.edited.*"))
    assert session.meta()["runs"][-1]["input"] == "transcript.txt"


def test_refine_json_reports_errors(
    settings: Settings,
    audio_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _transcribed_session(settings, audio_file, capsys)
    monkeypatch.setattr("sys.stdin", io.StringIO("FAIL"))
    args = ["refine", "--backend", "claude", "--stdin", "--json", str(session.path)]
    assert cli.main(args, settings) == 1
    error = _json_events(capsys)[-1]
    assert error["event"] == "error"
    assert "boom" in error["message"]


def test_pasted_text_is_kept(
    settings: Settings,
    audio_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _transcribed_session(settings, audio_file, capsys)
    monkeypatch.setattr("sys.stdin", io.StringIO("Final, hand-edited text."))
    assert cli.main(["pasted", str(session.path), "--origin", "refined"], settings) == 0
    pasted = list(session.path.glob("pasted.*.txt"))
    assert len(pasted) == 1
    assert pasted[0].read_text() == "Final, hand-edited text.\n"
    run = session.meta()["runs"][-1]
    assert run["step"] == "paste" and run["origin"] == "refined"
