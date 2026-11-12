"""Running external tools with useful errors."""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from scribe.debuglog import log as debug


class ToolError(RuntimeError):
    pass


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def run(
    cmd: Sequence[str | Path],
    *,
    log: Callable[[str], None] | None = None,
    input: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """Run a command and return its stdout. Raises ToolError with the stderr tail on failure."""
    args = [str(c) for c in cmd]
    if log:
        log(f"$ {shlex.join(args)}")
    debug.debug("run %s", shlex.join(args)[:500])
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            args,
            input=input,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        debug.error("not found: %s", args[0])
        raise ToolError(f"{args[0]} not found") from e
    except subprocess.TimeoutExpired as e:
        debug.error("timeout after %ss: %s", timeout, args[0])
        raise ToolError(f"{Path(args[0]).name} timed out after {timeout:.0f}s") from e
    if log and result.stderr:
        log(result.stderr)
    debug.debug(
        "exit %s after %.1fs: %s", result.returncode, time.monotonic() - t0, Path(args[0]).name
    )
    if result.returncode != 0:
        debug.error("%s stderr: %s", Path(args[0]).name, _tail(result.stderr or result.stdout))
        detail = _tail(result.stderr or result.stdout)
        raise ToolError(f"{Path(args[0]).name} failed (exit {result.returncode}):\n{detail}")
    return result.stdout


def run_streaming(
    cmd: Sequence[str | Path],
    on_stderr_line: Callable[[str], None],
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """Run a command, feeding each stderr line to a callback (for progress reporting)."""
    args = [str(c) for c in cmd]
    if log:
        log(f"$ {shlex.join(args)}")
    debug.debug("run %s", shlex.join(args)[:500])
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace"
        )
    except FileNotFoundError as e:
        raise ToolError(f"{args[0]} not found") from e
    lines: list[str] = []
    assert proc.stderr is not None
    for line in proc.stderr:
        lines.append(line)
        on_stderr_line(line)
    code = proc.wait()
    if log and lines:
        log("".join(lines))
    debug.debug("exit %s after %.1fs: %s", code, time.monotonic() - t0, Path(args[0]).name)
    if code != 0:
        debug.error("%s stderr: %s", Path(args[0]).name, _tail("".join(lines)))
        raise ToolError(f"{Path(args[0]).name} failed (exit {code}):\n{_tail(''.join(lines))}")
