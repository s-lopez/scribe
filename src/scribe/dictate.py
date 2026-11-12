"""Microphone dictation, driven over stdin/stdout by the Raycast extension.

Dictation only records and transcribes. Refinement is a separate step on the (possibly edited)
transcript: `scribe refine --stdin --json <session>`.

Protocol (one JSON object per stdout line)::

    -> {"event": "recording", "session": "<path>", "log": "<debug log path>"}
    <- "stop"                                            (stdin line)
    <- "discard"  (or stdin closed, SIGTERM/SIGHUP/SIGINT)
    -> {"event": "discarded", "reason": "..."}
    -> {"event": "stage", "stage": "waiting" | "transcribing"}
    -> {"event": "progress", "percent": 40}
    -> {"event": "transcribed", "text": "...", "session": "<path>"}
    -> {"event": "error", "message": "...", "log": "<debug log path>"}

Once stopped, the session is always processed to the end, even if the caller goes away.
Every step is written to the debug log (settings.log_file).
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import sys
import time
import wave
from pathlib import Path

from scribe import pipeline, sessions
from scribe.audio import Recorder, compress
from scribe.config import Settings
from scribe.debuglog import describe_environment, log
from scribe.events import Emitter, JsonReporter


class _Discard(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _raise_discard(signum: int, frame: object) -> None:
    name = signal.Signals(signum).name
    log.warning("received %s while recording", name)
    raise _Discard(f"received {name}")


def parse_command(line: str) -> str:
    command = line.strip()
    if command in ("stop", "discard"):
        return command
    raise ValueError(f"Unknown command {command!r}")


def _wait_for_command(recorder: Recorder) -> str:
    """Block until a stdin command arrives; EOF means discard."""
    buffer = b""
    fd = sys.stdin.fileno()
    while True:
        if not recorder.running:
            detail = recorder.error()
            log.error("recorder exited by itself: code=%s stderr=%r", recorder.returncode, detail)
            raise RuntimeError(f"Recording stopped unexpectedly. {detail}".strip())
        ready, _, _ = select.select([fd], [], [], 0.25)
        if not ready:
            continue
        chunk = os.read(fd, 1024)
        log.debug("stdin read %r", chunk)
        if not chunk:
            raise _Discard("stdin closed (EOF) before a stop command")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                return parse_command(line.decode())


def wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path)) as w:
            return round(w.getnframes() / w.getframerate(), 1)
    except wave.Error, EOFError, OSError, ZeroDivisionError:
        return None


_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def run(settings: Settings) -> int:
    previous = {sig: signal.getsignal(sig) for sig in _SIGNALS}
    try:
        return _run(settings)
    except Exception as e:
        log.exception("dictate crashed")
        Emitter().emit("error", message=f"scribe crashed: {e}", log=str(settings.log_file))
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        log.info("dictate finished")


def _run(settings: Settings) -> int:
    describe_environment({"rec": settings.tools.rec, "ffmpeg": settings.tools.ffmpeg,
                          "whisper": settings.tools.whisper})  # fmt: skip
    out = Emitter()
    reporter = JsonReporter(out)
    session = sessions.create(settings.sessions_dir, "dictation", kind="dictation")
    log.info("session %s", session.path)
    wav = session.path / "recording.wav"
    recorder = Recorder(settings.tools.rec, wav)

    signal.signal(signal.SIGTERM, _raise_discard)
    signal.signal(signal.SIGHUP, _raise_discard)
    signal.signal(signal.SIGINT, _raise_discard)
    try:
        recorder.start()
        log.info("recorder started pid=%s cmd=%s", recorder.pid, recorder.command)
        out.emit("recording", session=str(session.path), log=str(settings.log_file))
        command = _wait_for_command(recorder)
        log.info("command %s", command)
        if command == "discard":
            raise _Discard("discard command")
    except _Discard as d:
        log.warning("discarding recording: %s", d.reason)
        recorder.kill()
        shutil.rmtree(session.path, ignore_errors=True)
        out.emit("discarded", reason=d.reason)
        return 0
    except Exception as e:
        log.exception("recording failed")
        recorder.kill()
        shutil.rmtree(session.path, ignore_errors=True)
        out.emit("error", message=str(e), log=str(settings.log_file))
        return 1

    # Stopped: from here on, finish the job no matter what happens to the caller.
    for sig in _SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    t0 = time.monotonic()
    recorder.stop()
    size = wav.stat().st_size if wav.exists() else None
    log.info(
        "recorder stopped in %.2fs code=%s wav_bytes=%s stderr=%r",
        time.monotonic() - t0,
        recorder.returncode,
        size,
        recorder.error(),
    )
    return process(settings, session, wav, out, reporter)


def process(
    settings: Settings,
    session: sessions.Session,
    wav: Path,
    out: Emitter,
    reporter: JsonReporter,
) -> int:
    duration = wav_duration(wav)
    log.info("recording duration=%ss", duration)
    session.update_meta(duration_s=duration)
    try:
        with pipeline.job_lock(settings, reporter):
            log.debug("job lock acquired")
            try:
                text = pipeline.transcribe_session(settings, session, reporter, audio=wav)
                log.info("transcribed %d chars", len(text))
            finally:
                # Keep the recording even if transcription failed.
                compress(settings, wav, session.path / "audio.m4a", log=session.log)
                session.update_meta(audio="audio.m4a")
                wav.unlink()
                log.debug("recording compressed to audio.m4a")
        out.emit("transcribed", text=text, session=str(session.path))
        return 0
    except Exception as e:
        log.exception("processing failed")
        session.log(f"failed: {e}")
        out.emit("error", message=str(e), session=str(session.path), log=str(settings.log_file))
        return 1
