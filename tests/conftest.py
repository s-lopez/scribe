"""Fixtures: Settings pointing at fake ffmpeg / whisper-cli / rec / claude executables."""

from __future__ import annotations

import json
import sys
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from scribe.config import HOME, Claude, Ollama, Settings, Tools, Whisper


def _script(path: Path, body: str) -> str:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)


FAKE_FFMPEG = """
    import shutil, sys
    args = sys.argv[1:]
    shutil.copyfile(args[args.index("-i") + 1], args[-1])
"""

# Writes one segment per line with leading spaces, like whisper-cli -otxt.
FAKE_WHISPER = """
    import sys
    args = sys.argv[1:]
    base = args[args.index("-of") + 1]
    audio = open(args[args.index("-f") + 1], "rb").read().decode(errors="ignore")
    for p in (10, 50, 100):
        print(f"whisper_print_progress_callback: progress = {p}%", file=sys.stderr)
    with open(base + ".txt", "w") as fh:
        fh.write(" um hello there\\n so this is a test\\n" if "silence" not in audio else "")
"""

FAKE_REC = """
    import os, signal, sys, time, wave
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
    out = sys.argv[-1]
    with wave.open(out + ".tmp", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\\0\\0" * 16000)
    os.replace(out + ".tmp", out)  # appears only once complete and stoppable
    while True:
        time.sleep(0.05)
"""

# Records argv/env/stdin next to itself, answers with an upper-cased transcript.
FAKE_CLAUDE = """
    import json, os, sys
    from pathlib import Path
    text = sys.stdin.read()
    Path(__file__).with_suffix(".json").write_text(json.dumps(
        {"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ), "stdin": text}))
    if "FAIL" in text:
        print("boom", file=sys.stderr); sys.exit(3)
    print(text.strip().upper())
"""


class FakeOllama:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response: dict[str, Any] = {}

    def reply(self, content: str, **extra: Any) -> None:
        self.response = {"message": {"role": "assistant", "content": content}, **extra}


@pytest.fixture
def ollama() -> Iterator[tuple[FakeOllama, str]]:
    fake = FakeOllama()
    fake.reply("Hello there. So this is a test.", done_reason="stop", prompt_eval_count=50)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            fake.requests.append(json.loads(body))
            payload = json.dumps(fake.response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield fake, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def settings(tmp_path: Path, ollama: tuple[FakeOllama, str]) -> Settings:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    (models / "model.bin").write_bytes(b"fake")
    (models / "vad.bin").write_bytes(b"fake")
    return Settings(
        sessions_dir=tmp_path / "sessions",
        log_file=tmp_path / "logs" / "scribe.log",
        refine_prompt=HOME / "prompts" / "refine.md",
        tools=Tools(
            ffmpeg=_script(bin_dir / "ffmpeg", FAKE_FFMPEG),
            whisper=_script(bin_dir / "whisper-cli", FAKE_WHISPER),
            rec=_script(bin_dir / "rec", FAKE_REC),
            claude=_script(bin_dir / "claude", FAKE_CLAUDE),
            osascript="/usr/bin/true",
            pbcopy="/usr/bin/true",
        ),
        whisper=Whisper(
            model=models / "model.bin", vad_model=models / "vad.bin", vocabulary="AuthKit, Docker."
        ),
        ollama=Ollama(url=ollama[1]),
        claude=Claude(),
    )


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "inbox" / "Voice memo 1.m4a"
    path.parent.mkdir()
    path.write_bytes(b"pretend this is speech")
    return path


def claude_call(settings: Settings) -> dict[str, Any]:
    return json.loads(Path(settings.tools.claude).with_suffix(".json").read_text())
