"""JSON-lines events on stdout, for programs driving scribe (the Raycast extension)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from scribe.debuglog import log

# Fields holding dictated or refined text: logged by size only, so logs/scribe.log keeps no content.
_TEXT_FIELDS = ("text",)


def _redacted(event: str, data: dict[str, Any]) -> str:
    shown = {
        k: f"<{len(v)} chars>" if k in _TEXT_FIELDS and isinstance(v, str) else v
        for k, v in data.items()
    }
    return json.dumps({"event": event, **shown}, ensure_ascii=False)


class Emitter:
    """JSON lines on stdout; silently stops if the reader went away."""

    def __init__(self) -> None:
        self.alive = True

    def emit(self, event: str, **data: Any) -> None:
        payload = json.dumps({"event": event, **data}, ensure_ascii=False)
        log.debug("emit %s", _redacted(event, data)[:300])
        if not self.alive:
            log.debug("emit skipped: reader is gone")
            return
        try:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
        except OSError as e:
            log.warning("stdout closed (%s); continuing without a reader", e)
            self.alive = False
            # Keep the interpreter from failing on the final flush at exit.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())


class JsonReporter:
    """pipeline.Reporter that turns stages and progress into events."""

    def __init__(self, emitter: Emitter) -> None:
        self.emitter = emitter
        self._last_percent = -1

    def stage(self, stage: str, **info: object) -> None:
        self.emitter.emit("stage", stage=stage, **info)

    def progress(self, percent: int) -> None:
        if percent != self._last_percent:
            self._last_percent = percent
            self.emitter.emit("progress", percent=percent)
