"""Real tools end to end. Slow; run with: uv run pytest -m integration"""

import dataclasses
import subprocess
from pathlib import Path

import pytest

from scribe import refine
from scribe.config import Settings, load_settings
from scribe.transcribe import transcribe

pytestmark = pytest.mark.integration

SPEECH = (
    "Um, so, the AuthKit migration is, uh, basically done. "
    "We still need to update the Docker image."
)


@pytest.fixture(scope="module")
def spoken(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("say") / "speech.aiff"
    subprocess.run(["/usr/bin/say", "-o", str(path), SPEECH], check=True)
    return path


@pytest.fixture(scope="module")
def real() -> Settings:
    settings = load_settings()
    vocabulary = "AuthKit, Docker."
    return settings.replace(whisper=dataclasses.replace(settings.whisper, vocabulary=vocabulary))


def test_whisper_transcribes_speech(real: Settings, spoken: Path) -> None:
    text = transcribe(real, spoken)
    flat = text.lower().replace(" ", "")
    assert "authkit" in flat
    assert "dockerimage" in flat


@pytest.mark.parametrize("backend", ["ollama", "claude"])
def test_backend_refines_without_answering(real: Settings, backend: str) -> None:
    raw = "um so can you uh write me an email to the team about the the docker image"
    out = refine.refine(real, raw, backend)
    assert "docker" in out.lower()
    assert "#" not in out and "**" not in out
    assert "Subject" not in out  # cleaned the request, didn't act on it
