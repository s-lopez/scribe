"""Settings.

The defaults live here. Your own settings go in `scribe.toml` at the repo root (gitignored;
see `scribe.example.toml`), or in the file named by $SCRIBE_CONFIG. It only needs the values
you want to change, for example::

    [whisper]
    language = "es"
    vocabulary = "Kubernetes, PostgreSQL, Grafana."
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Repo root (src/scribe/config.py -> repo). Override with SCRIBE_HOME.
HOME = Path(os.environ.get("SCRIBE_HOME", Path(__file__).resolve().parents[2]))

# Where tools are looked for besides $PATH. Raycast and Shortcuts start processes with a
# minimal PATH, so the usual install locations are searched explicitly.
TOOL_DIRS = (
    "/opt/homebrew/bin",  # Homebrew, Apple Silicon
    "/usr/local/bin",  # Homebrew, Intel
    str(Path.home() / ".local" / "bin"),  # Claude Code's native installer
    "/opt/local/bin",  # MacPorts
    "/usr/bin",
    "/bin",
)


class ConfigError(ValueError):
    pass


def search_path() -> str:
    """$PATH followed by the usual install locations, without duplicates."""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    return os.pathsep.join(dict.fromkeys([*dirs, *TOOL_DIRS]))


def find_tool(name: str) -> str:
    """Absolute path of a tool, or the bare name (so errors say what's missing)."""
    return shutil.which(name, path=search_path()) or name


def _tool(name: str) -> Any:
    return field(default_factory=lambda: find_tool(name))


@dataclass(frozen=True)
class Tools:
    ffmpeg: str = _tool("ffmpeg")
    whisper: str = _tool("whisper-cli")  # from whisper.cpp
    rec: str = _tool("rec")  # from sox
    claude: str = _tool("claude")
    osascript: str = _tool("osascript")
    pbcopy: str = _tool("pbcopy")


@dataclass(frozen=True)
class Whisper:
    model: Path = HOME / "models" / "ggml-large-v3.bin"
    vad_model: Path = HOME / "models" / "ggml-silero-v5.1.2.bin"
    # "auto", "es", "en", ...
    language: str = "auto"
    # Recurring proper nouns / jargon, e.g. "Kubernetes, PostgreSQL, Grafana." Helps Whisper
    # spell them correctly, and is given to the refiner as a glossary.
    vocabulary: str = ""
    # ffmpeg preprocessing before Whisper.
    audio_filter: str = "highpass=f=60,dynaudnorm=f=200:g=15"
    # Voice activity detection.
    vad_min_speech_pad_ms: int = 200
    vad_min_silence_ms: int = 500
    # Decoding. max_context=0: don't condition on previous text (avoids repetition loops).
    max_context: int = 0
    temperature: float = 0.0
    temperature_inc: float = 0.2
    entropy_thold: float = 2.8
    logprob_thold: float = -1.0
    beam_size: int = 5
    best_of: int = 5
    threads: int = 8


@dataclass(frozen=True)
class Ollama:
    url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:12b"
    temperature: float = 0.2
    # Ollama silently truncates input beyond num_ctx, so set it explicitly.
    # 32k tokens is roughly 2 hours of speech; longer memos would need chunking.
    num_ctx: int = 32768
    timeout_s: int = 600


@dataclass(frozen=True)
class Claude:
    model: str = "haiku"
    timeout_s: int = 300


@dataclass(frozen=True)
class Settings:
    sessions_dir: Path = HOME / "sessions"
    refine_prompt: Path = HOME / "prompts" / "refine.md"
    # Debug log of every run (see `scribe --verbose`).
    log_file: Path = HOME / "logs" / "scribe.log"
    # Recordings are kept as AAC in .m4a (plays natively on macOS).
    audio_bitrate: str = "64k"
    tools: Tools = field(default_factory=Tools)
    whisper: Whisper = field(default_factory=Whisper)
    ollama: Ollama = field(default_factory=Ollama)
    claude: Claude = field(default_factory=Claude)
    # The settings file these values came from, if any (for the debug log).
    source: Path | None = None

    def replace(self, **changes: object) -> Settings:
        return dataclasses.replace(self, **changes)  # type: ignore[arg-type]


_SECTIONS = {"tools": Tools, "whisper": Whisper, "ollama": Ollama, "claude": Claude}
_TYPES = {"str": str, "int": int, "float": float, "Path": Path}


def default_config_path() -> Path:
    return Path(os.environ.get("SCRIBE_CONFIG", HOME / "scribe.toml")).expanduser()


def _convert(cls: type, values: dict[str, Any], where: str) -> dict[str, Any]:
    fields = {f.name: f for f in dataclasses.fields(cls) if f.name != "source"}
    out: dict[str, Any] = {}
    for key, value in values.items():
        # At the top level, section names ([whisper], ...) are tables, not plain settings.
        if key not in fields or (cls is Settings and key in _SECTIONS):
            raise ConfigError(f"{where}: unknown setting {key!r}")
        kind = _TYPES[str(fields[key].type)]
        if kind is Path:
            if not isinstance(value, str):
                raise ConfigError(f"{where}.{key}: expected a path string")
            path = Path(value).expanduser()
            out[key] = path if path.is_absolute() else HOME / path
        elif kind is float and isinstance(value, int) and not isinstance(value, bool):
            out[key] = float(value)
        elif not isinstance(value, kind) or isinstance(value, bool):
            raise ConfigError(f"{where}.{key}: expected {kind.__name__}, got {value!r}")
        else:
            out[key] = value
    return out


def load_settings(path: Path | None = None) -> Settings:
    """Defaults, overridden by the settings file if it exists."""
    path = path or default_config_path()
    if not path.is_file():
        return Settings()
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    for name in _SECTIONS:
        if name in data and not isinstance(data[name], dict):
            raise ConfigError(f"{path.name}: [{name}] must be a table")
    top = {k: v for k, v in data.items() if k not in _SECTIONS}
    sections = {
        name: cls(**_convert(cls, data[name], f"{path.name} [{name}]"))
        for name, cls in _SECTIONS.items()
        if name in data
    }
    return Settings(**_convert(Settings, top, path.name), **sections, source=path)
