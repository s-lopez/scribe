"""Command line interface.

scribe transcribe FILE... [--refine ollama|claude | --ask] [--background] [--copy]
scribe refine PATH...     [--backend ollama|claude | --ask] [--background] [--copy]
scribe dictate            (used by the Raycast extension; JSON lines over stdin/stdout)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scribe import debuglog, dictate, macos, pipeline, sessions
from scribe.config import ConfigError, Settings, load_settings
from scribe.events import Emitter, JsonReporter
from scribe.refine import BACKENDS
from scribe.sessions import Session

TRANSCRIBE_CHOICES = {
    "Transcribe only": None,
    "Transcribe + refine locally (Gemma)": "ollama",
    "Transcribe + refine with Claude": "claude",
}
REFINE_CHOICES = {
    "Refine locally (Gemma)": "ollama",
    "Refine with Claude": "claude",
}


class ConsoleReporter:
    """Progress on stderr, so stdout carries only the resulting text."""

    def stage(self, stage: str, **info: object) -> None:
        detail = f" ({info['backend']})" if "backend" in info else ""
        print(f"{stage.capitalize()}{detail}…", file=sys.stderr, flush=True)

    def progress(self, percent: int) -> None:
        print(f"  {percent}%", file=sys.stderr, flush=True)


class Delivery:
    """Where results go: stdout in a terminal; clipboard + notification in the background."""

    def __init__(self, settings: Settings, *, background: bool, copy: bool) -> None:
        self.settings = settings
        self.background = background
        self.copy = copy or background

    def result(self, session: Session, title: str, text: str) -> None:
        if self.copy:
            macos.copy_to_clipboard(self.settings, text)
        if self.background:
            preview = text or "(no speech detected)"
            macos.notify(self.settings, title, f"Copied to clipboard. {preview}")
        else:
            print(text)
            print(f"Session: {session.path}", file=sys.stderr)

    def failure(self, what: str, error: Exception) -> None:
        if self.background:
            macos.notify(self.settings, "Scribe failed", f"{what}: {error}")
        print(f"error: {what}: {error}", file=sys.stderr)


def _reporter(background: bool) -> pipeline.Reporter:
    return pipeline.NullReporter() if background else ConsoleReporter()


def _detach(args: list[str]) -> None:
    """Continue in a detached process, so Shortcuts / Finder return immediately."""
    subprocess.Popen(
        [sys.executable, "-m", "scribe", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def cmd_transcribe(settings: Settings, args: argparse.Namespace) -> int:
    backend = args.refine
    if args.ask:
        picked = macos.choose(settings, "What should Scribe do?", list(TRANSCRIBE_CHOICES))
        if picked is None:
            return 0
        backend = TRANSCRIBE_CHOICES[picked]

    created: list[Session] = []
    for f in args.files:
        if not f.is_file():
            print(f"Skipping non-file input: {f}", file=sys.stderr)
            continue
        created.append(pipeline.new_file_session(settings, f))
    if not created:
        print("No audio files given.", file=sys.stderr)
        return 1

    if args.background:
        _detach(
            [
                "_process",
                *(["--refine", backend] if backend else []),
                *map(str, (s.path for s in created)),
            ]
        )
        return 0
    return process_sessions(settings, created, backend, background=False, copy=args.copy)


def process_sessions(
    settings: Settings,
    created: list[Session],
    backend: str | None,
    *,
    background: bool,
    copy: bool,
) -> int:
    delivery = Delivery(settings, background=background, copy=copy)
    reporter = _reporter(background)
    status = 0
    for session in created:
        label = session.meta().get("source") or session.name
        label = Path(label).name
        with pipeline.job_lock(settings, reporter):
            try:
                text = pipeline.transcribe_session(settings, session, reporter)
            except Exception as e:
                session.log(f"transcribe failed: {e}")
                delivery.failure(f"Transcribing {label}", e)
                status = 1
                continue
            title = f"Transcribed {label}"
            if backend and text:
                try:
                    _, text = pipeline.refine_session(settings, session, backend, reporter)
                    title = f"Transcribed and refined {label}"
                except Exception as e:
                    session.log(f"refine failed: {e}")
                    delivery.failure(f"Refining {label} (raw transcript kept)", e)
                    status = 1
        delivery.result(session, title, text)
    return status


def cmd_process(settings: Settings, args: argparse.Namespace) -> int:
    found = [s for p in args.sessions if (s := sessions.find(p))]
    return process_sessions(settings, found, args.refine, background=True, copy=True)


def cmd_refine(settings: Settings, args: argparse.Namespace) -> int:
    backend = args.backend
    if args.ask:
        picked = macos.choose(settings, "Refine with:", list(REFINE_CHOICES))
        if picked is None:
            return 0
        backend = REFINE_CHOICES[picked]
    if backend is None:
        print("error: choose a backend with --backend or --ask", file=sys.stderr)
        return 2

    if args.stdin and (len(args.paths) != 1 or args.background):
        print("error: --stdin takes exactly one session and no --background", file=sys.stderr)
        return 2
    if args.json:
        return _refine_json(settings, args, backend)

    delivery = Delivery(settings, background=args.background, copy=args.copy)
    resolved: list[tuple[Session, Path]] = []
    for p in args.paths:
        try:
            resolved.append(pipeline.resolve_refine_input(settings, p))
        except ValueError as e:
            delivery.failure(str(p), e)
    if not resolved:
        return 1
    if args.background:
        # Inputs now live in sessions, so the detached worker can't lose them.
        _detach(
            ["refine", "--backend", backend, "--background-worker", *(str(s) for _, s in resolved)]
        )
        return 0

    background = args.background_worker
    delivery = Delivery(settings, background=background, copy=args.copy or background)
    reporter = _reporter(background)
    status = 0
    for session, source in resolved:
        with pipeline.job_lock(settings, reporter):
            try:
                if args.stdin:
                    out, text = pipeline.refine_text(
                        settings, session, sys.stdin.read(), backend, reporter
                    )
                else:
                    out, text = pipeline.refine_session(
                        settings, session, backend, reporter, source
                    )
            except Exception as e:
                session.log(f"refine failed: {e}")
                delivery.failure(f"Refining {session.name}", e)
                status = 1
                continue
        if not background:
            print(f"Written: {out}", file=sys.stderr)
        delivery.result(session, f"Refined {session.name}", text)
    return status


def _refine_json(settings: Settings, args: argparse.Namespace, backend: str) -> int:
    """Machine-readable refinement for the Raycast extension (events like `dictate`)."""
    out = Emitter()
    reporter = JsonReporter(out)
    try:
        if len(args.paths) != 1:
            raise ValueError("--json takes exactly one session or text file")
        session, source = pipeline.resolve_refine_input(settings, args.paths[0])
        text = sys.stdin.read() if args.stdin else None
        with pipeline.job_lock(settings, reporter):
            if text is not None:
                path, refined = pipeline.refine_text(settings, session, text, backend, reporter)
            else:
                path, refined = pipeline.refine_session(
                    settings, session, backend, reporter, source
                )
        out.emit("refined", text=refined, output=str(path), session=str(session.path))
        return 0
    except Exception as e:
        debuglog.log.exception("refine failed")
        out.emit("error", message=str(e), log=str(settings.log_file))
        return 1


def cmd_pasted(settings: Settings, args: argparse.Namespace) -> int:
    session = sessions.find(args.session)
    if session is None:
        print(f"error: not a session: {args.session}", file=sys.stderr)
        return 1
    pipeline.save_pasted(session, sys.stdin.read(), args.origin)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="mirror the debug log (logs/scribe.log) to stderr",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("transcribe", help="transcribe audio files, each into a new session")
    t.add_argument("files", nargs="+", type=Path)
    g = t.add_mutually_exclusive_group()
    g.add_argument("--refine", choices=BACKENDS, help="also refine with this backend")
    g.add_argument("--ask", action="store_true", help="pick the mode in a dialog")
    t.add_argument(
        "--background", action="store_true", help="return at once; clipboard + notification"
    )
    t.add_argument("--copy", action="store_true", help="also copy the result to the clipboard")
    t.set_defaults(func=cmd_transcribe)

    r = sub.add_parser("refine", help="refine a transcript: session folder or any text file")
    r.add_argument("paths", nargs="+", type=Path)
    g = r.add_mutually_exclusive_group()
    g.add_argument("--backend", choices=BACKENDS)
    g.add_argument("--ask", action="store_true", help="pick the backend in a dialog")
    r.add_argument(
        "--background", action="store_true", help="return at once; clipboard + notification"
    )
    r.add_argument("--background-worker", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--copy", action="store_true", help="also copy the result to the clipboard")
    r.add_argument(
        "--stdin", action="store_true", help="refine the text on stdin (e.g. an edited transcript)"
    )
    r.add_argument("--json", action="store_true", help="JSON-lines events on stdout")
    r.set_defaults(func=cmd_refine)

    k = sub.add_parser("pasted", help="keep the text that was pasted (stdin) in its session")
    k.add_argument("session", type=Path)
    k.add_argument("--origin", default="transcript", help="which text it came from")
    k.set_defaults(func=cmd_pasted)

    d = sub.add_parser("dictate", help="record from the microphone (JSON protocol for Raycast)")
    d.set_defaults(func=lambda settings, args: dictate.run(settings))

    p = sub.add_parser("_process")  # internal: background worker for `transcribe --background`
    p.add_argument("sessions", nargs="+", type=Path)
    p.add_argument("--refine", choices=BACKENDS)
    p.set_defaults(func=cmd_process)
    return parser


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    if settings is None:
        try:
            settings = load_settings()
        except ConfigError as e:
            print(f"scribe: invalid settings: {e}", file=sys.stderr)
            return 2
    debuglog.setup(settings.log_file, verbose=args.verbose)
    debuglog.log.info("scribe %s", " ".join(sys.argv[1:] if argv is None else argv))
    debuglog.log.debug("settings from %s", settings.source or "defaults (no scribe.toml)")
    try:
        return args.func(settings, args)
    except Exception:
        debuglog.log.exception("scribe %s crashed", args.command)
        raise
