"""The shared pipeline: every entry point (dictation, files, refinement) goes through here."""

from __future__ import annotations

import fcntl
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

from scribe import refine as refine_mod
from scribe import sessions
from scribe.config import Settings
from scribe.debuglog import log
from scribe.sessions import Session
from scribe.transcribe import transcribe


class Reporter(Protocol):
    def stage(self, stage: str, **info: object) -> None: ...
    def progress(self, percent: int) -> None: ...


class NullReporter:
    def stage(self, stage: str, **info: object) -> None:
        pass

    def progress(self, percent: int) -> None:
        pass


@contextmanager
def job_lock(settings: Settings, reporter: Reporter) -> Iterator[None]:
    """Run heavy jobs (Whisper, Ollama) one at a time; later ones wait their turn."""
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    with (settings.sessions_dir / ".lock").open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("another job holds the lock; waiting")
            reporter.stage("waiting")
            fcntl.flock(fh, fcntl.LOCK_EX)
            log.info("lock acquired after waiting")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_file_session(settings: Settings, src: Path) -> Session:
    """Session for an audio file; the file is copied in right away (shared originals vanish)."""
    src = src.resolve()
    session = sessions.create(settings.sessions_dir, src.stem, kind="file", source=str(src))
    audio_name = f"audio{src.suffix.lower()}"
    shutil.copy2(src, session.path / audio_name)
    session.update_meta(audio=audio_name)
    return session


def new_text_session(settings: Settings, src: Path) -> Session:
    """Session for a transcript that came from elsewhere."""
    src = src.resolve()
    session = sessions.create(settings.sessions_dir, src.stem, kind="text", source=str(src))
    shutil.copyfile(src, session.transcript)
    return session


def transcribe_session(
    settings: Settings, session: Session, reporter: Reporter, audio: Path | None = None
) -> str:
    """Transcribe the session's audio (or a given file) into transcript.txt."""
    audio = audio or session.audio()
    if audio is None:
        raise ValueError(f"Session {session.name} has no audio")
    log.info("transcribing %s (session %s)", audio, session.name)
    reporter.stage("transcribing")
    started, t0 = _now(), time.monotonic()
    text = transcribe(settings, audio, on_progress=reporter.progress, log=session.log)
    session.transcript.write_text(text + "\n" if text else "")
    w = settings.whisper
    session.add_run(
        {
            "step": "transcribe",
            "started": started,
            "seconds": round(time.monotonic() - t0, 1),
            "output": sessions.TRANSCRIPT,
            "model": Path(w.model).name,
            "language": w.language,
            "vocabulary": w.vocabulary,
            "audio_filter": w.audio_filter,
        }
    )
    return text


def refine_session(
    settings: Settings,
    session: Session,
    backend: str,
    reporter: Reporter,
    source: Path | None = None,
) -> tuple[Path, str]:
    """Refine a text file of the session (default: transcript.txt) into a new refined file."""
    source = source or session.transcript
    text = source.read_text()
    log.info("refining %s with %s (session %s)", source.name, backend, session.name)
    reporter.stage("refining", backend=backend)
    started, t0 = _now(), time.monotonic()
    refined = refine_mod.refine(settings, text, backend, log=session.log)
    out = session.refined_path(backend)
    out.write_text(refined + "\n")
    session.add_run(
        {
            "step": "refine",
            "started": started,
            "seconds": round(time.monotonic() - t0, 1),
            "input": source.name,
            "output": out.name,
            "backend": backend,
            "model": refine_mod.backend_model(settings, backend),
        }
    )
    return out, refined


def resolve_refine_input(settings: Settings, path: Path) -> tuple[Session, Path]:
    """A session folder, a text file inside a session, or an outside text file (new session)."""
    path = path.resolve()
    session = sessions.find(path)
    if session is None:
        if not path.is_file():
            raise ValueError(f"Not a session or text file: {path}")
        session = new_text_session(settings, path)
        return session, session.transcript
    # A text file inside the session is refined as-is; the folder, audio or meta.json
    # mean "the session's transcript".
    source = path if path.is_file() and path.suffix == ".txt" else session.transcript
    if not source.is_file():
        raise ValueError(f"Session {session.name} has no transcript yet")
    return session, source


def refine_text(
    settings: Settings, session: Session, text: str, backend: str, reporter: Reporter
) -> tuple[Path, str]:
    """Refine text handed over by the caller (e.g. a transcript edited in Raycast).

    An edited transcript is kept as transcript.edited.<ts>.txt next to the untouched raw
    transcript, so the session shows exactly what was refined.
    """
    raw = session.transcript.read_text().strip() if session.transcript.is_file() else None
    if raw is not None and text.strip() == raw:
        source = session.transcript
    else:
        source = session.path / f"transcript.edited.{sessions.timestamp()}.txt"
        source.write_text(text.strip() + "\n")
        log.info("saved edited transcript %s", source.name)
    return refine_session(settings, session, backend, reporter, source)


def save_pasted(session: Session, text: str, origin: str) -> Path:
    """Keep the text that was finally pasted (after any edits) in the session."""
    out = session.path / f"pasted.{sessions.timestamp()}.txt"
    out.write_text(text.strip() + "\n")
    session.add_run({"step": "paste", "started": _now(), "output": out.name, "origin": origin})
    log.info("saved pasted text %s (from %s)", out.name, origin)
    return out
