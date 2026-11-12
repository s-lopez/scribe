"""Session folders: one per recording / audio file / outside text, holding every artifact.

Layout::

    sessions/2026-11-12_09-30-12_<label>/
        audio.m4a | audio.<ext>        recording (compressed) or copy of the input file
        transcript.txt                 raw Whisper output (or the imported text)
        refined.<backend>.<ts>.txt     one per refinement run, never overwritten
        meta.json                      source, settings and a log of every run
        scribe.log                     tool output, for debugging
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

TIMESTAMP_FMT = "%Y-%m-%d_%H-%M-%S"
TRANSCRIPT = "transcript.txt"
META = "meta.json"
LOG = "scribe.log"
_MAX_LABEL = 60


def timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime(TIMESTAMP_FMT)


def slugify(label: str) -> str:
    """Folder-safe label: keeps accents and spaces, drops path separators and control chars."""
    text = unicodedata.normalize("NFC", label)
    text = re.sub(r"[/:\\\x00-\x1f]", "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text[:_MAX_LABEL].rstrip(" .-") or "session"


@dataclass(frozen=True)
class Session:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def transcript(self) -> Path:
        return self.path / TRANSCRIPT

    @property
    def log_path(self) -> Path:
        return self.path / LOG

    def meta(self) -> dict[str, Any]:
        return json.loads((self.path / META).read_text())

    def write_meta(self, meta: dict[str, Any]) -> None:
        tmp = self.path / f".{META}.tmp"
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.path / META)

    def update_meta(self, **fields: Any) -> None:
        meta = self.meta()
        meta.update(fields)
        self.write_meta(meta)

    def add_run(self, run: dict[str, Any]) -> None:
        meta = self.meta()
        meta.setdefault("runs", []).append(run)
        self.write_meta(meta)

    def audio(self) -> Path | None:
        name = self.meta().get("audio")
        return self.path / name if name else None

    def refined_path(self, backend: str, now: datetime | None = None) -> Path:
        return self.path / f"refined.{backend}.{timestamp(now)}.txt"

    def log(self, text: str) -> None:
        with self.log_path.open("a") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")


def create(
    sessions_dir: Path,
    label: str,
    kind: str,
    source: str | None = None,
    now: datetime | None = None,
) -> Session:
    """Create a new, uniquely named session folder with an initial meta.json."""
    now = now or datetime.now()
    sessions_dir.mkdir(parents=True, exist_ok=True)
    base = f"{timestamp(now)}_{slugify(label)}"
    # mkdir is the atomic claim: two processes starting in the same second (e.g. two
    # dictations, or a dictation and a Quick Action) each get their own folder.
    n = 1
    while True:
        path = sessions_dir / (base if n == 1 else f"{base}-{n}")
        try:
            path.mkdir()
            break
        except FileExistsError:
            n += 1
    session = Session(path)
    session.write_meta(
        {
            "created": now.isoformat(timespec="seconds"),
            "kind": kind,  # "dictation" | "file" | "text"
            "source": source,
            "audio": None,
            "runs": [],
        }
    )
    return session


def find(path: Path) -> Session | None:
    """The session a path belongs to: the session folder itself or a file inside it."""
    path = path.resolve()
    for candidate in (path, path.parent):
        if candidate.is_dir() and (candidate / META).is_file():
            return Session(candidate)
    return None
