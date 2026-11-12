"""Debug log: every run appends timestamped details to logs/scribe.log (rotated at 1 MB).

`scribe --verbose ...` mirrors the log to stderr (the Raycast extension shows it in `npm run dev`).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import stat
import sys
from pathlib import Path

log = logging.getLogger("scribe")

_FORMAT = "%(asctime)s.%(msecs)03d pid=%(process)d %(levelname)-5s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup(log_file: Path, *, verbose: bool = False) -> None:
    """Configure the "scribe" logger once per process."""
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    log.setLevel(logging.DEBUG)
    log.propagate = False
    formatter = logging.Formatter(_FORMAT, _DATEFMT)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except OSError as e:
        print(f"scribe: cannot write debug log {log_file}: {e}", file=sys.stderr)
    if verbose:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        log.addHandler(stream)


def _fd_kind(fd: int) -> str:
    try:
        mode = os.fstat(fd).st_mode
    except OSError as e:
        return f"unavailable ({e.strerror})"
    for check, name in (
        (stat.S_ISFIFO, "pipe"),
        (stat.S_ISSOCK, "socket"),
        (stat.S_ISCHR, "tty/char device"),
        (stat.S_ISREG, "file"),
    ):
        if check(mode):
            return name
    return oct(mode)


def describe_environment(tools: dict[str, str]) -> None:
    """Log what usually goes wrong when launched from Raycast or Shortcuts."""
    log.info("start argv=%s", sys.argv)
    log.debug("python=%s cwd=%s ppid=%s", sys.executable, os.getcwd(), os.getppid())
    log.debug("PATH=%s", os.environ.get("PATH", ""))
    log.debug(
        "USER=%s LOGNAME=%s HOME=%s",
        os.environ.get("USER", "(unset)"),
        os.environ.get("LOGNAME", "(unset)"),
        os.environ.get("HOME", "(unset)"),
    )
    log.debug(
        "stdin=%s stdout=%s stderr=%s",
        _fd_kind(0),
        _fd_kind(1),
        _fd_kind(2),
    )
    for name, path in tools.items():
        ok = os.path.isfile(path) and os.access(path, os.X_OK)
        (log.debug if ok else log.warning)("tool %s=%s %s", name, path, "ok" if ok else "MISSING")
