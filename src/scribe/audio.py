"""Audio: microphone recording (sox), Whisper preprocessing and compression (ffmpeg)."""

from __future__ import annotations

import signal
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from scribe.config import Settings
from scribe.proc import ToolError, run

Log = Callable[[str], None] | None


def to_whisper_wav(settings: Settings, src: Path, dst: Path, log: Log = None) -> None:
    """Any audio/video file -> 16 kHz mono PCM WAV with the configured filter applied."""
    run(
        [
            settings.tools.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-y",
            "-i",
            src,
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-af",
            settings.whisper.audio_filter,
            dst,
        ],
        log=log,
    )


def compress(settings: Settings, src: Path, dst: Path, log: Log = None) -> None:
    """Lossless recording -> mono AAC .m4a for storage."""
    run(
        [
            settings.tools.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-y",
            "-i",
            src,
            "-vn",
            "-ac",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            settings.audio_bitrate,
            dst,
        ],
        log=log,
    )


class Recorder:
    """Records the default input device to a WAV file until stopped.

    sox runs with -V3 so its log (input device, rate, warnings) can be inspected;
    stderr goes to a temp file so a chatty sox can never block on a full pipe.
    """

    def __init__(self, rec: str, out: Path) -> None:
        self.rec = rec
        self.out = out
        self.command = [
            rec, "-q", "-V3", "-c", "1", "-b", "16", "-e", "signed-integer", str(out)
        ]  # fmt: skip
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 (lives as long as the recorder)

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr,
            )
        except FileNotFoundError as e:
            raise ToolError(f"{self.rec} not found (brew install sox)") from e

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.poll() if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def error(self) -> str:
        """Everything sox wrote to stderr so far."""
        self._stderr.seek(0)
        return self._stderr.read().decode(errors="replace").strip()

    def stop(self) -> None:
        """Stop gracefully so sox finalizes the WAV header."""
        if not self.running:
            return
        assert self._proc is not None
        self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

    def kill(self) -> None:
        if self.running:
            assert self._proc is not None
            self._proc.kill()
            self._proc.wait()
