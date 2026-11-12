"""Speech to text with whisper.cpp."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from scribe import audio
from scribe.config import Settings
from scribe.proc import ToolError, run_streaming

_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")


def whisper_command(settings: Settings, wav: Path, output_base: Path) -> list[str]:
    w = settings.whisper
    cmd = [
        settings.tools.whisper,
        "-m", str(w.model),
        "-f", str(wav),
        "-l", w.language,
        "-otxt",
        "-of", str(output_base),
        "--vad",
        "--vad-model", str(w.vad_model),
        "-vp", str(w.vad_min_speech_pad_ms),
        "-vsd", str(w.vad_min_silence_ms),
        "-mc", str(w.max_context),
        "--temperature", str(w.temperature),
        "--temperature-inc", str(w.temperature_inc),
        "--entropy-thold", str(w.entropy_thold),
        "--logprob-thold", str(w.logprob_thold),
        "--beam-size", str(w.beam_size),
        "--best-of", str(w.best_of),
        "--suppress-nst",
        "-t", str(w.threads),
        "--print-progress",
    ]  # fmt: skip
    if w.vocabulary:
        cmd += ["--prompt", w.vocabulary]
    return cmd


def normalize(raw: str) -> str:
    """Whisper writes one segment per line with leading spaces; join into running text."""
    return " ".join(line.strip() for line in raw.splitlines() if line.strip())


def transcribe(
    settings: Settings,
    src: Path,
    *,
    on_progress: Callable[[int], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> str:
    """Transcribe any audio file and return the text."""
    for model in (settings.whisper.model, settings.whisper.vad_model):
        if not Path(model).is_file():
            raise ToolError(f"Model not found: {model}")

    def on_line(line: str) -> None:
        if on_progress and (m := _PROGRESS.search(line)):
            on_progress(int(m.group(1)))

    with tempfile.TemporaryDirectory(prefix="scribe-") as tmp:
        wav = Path(tmp) / "input.wav"
        base = Path(tmp) / "transcript"
        audio.to_whisper_wav(settings, src, wav, log=log)
        run_streaming(whisper_command(settings, wav, base), on_line, log=log)
        out = base.with_suffix(".txt")
        if not out.is_file():
            raise ToolError("whisper-cli produced no transcript")
        return normalize(out.read_text(errors="replace"))
