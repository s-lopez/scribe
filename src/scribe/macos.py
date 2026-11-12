"""Small macOS integrations: clipboard, notifications, choice dialogs."""

from __future__ import annotations

import subprocess

from scribe.config import Settings


def copy_to_clipboard(settings: Settings, text: str) -> None:
    subprocess.run([settings.tools.pbcopy], input=text, text=True, check=True)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(settings: Settings, title: str, message: str) -> None:
    script = (
        f"display notification {_applescript_string(message[:240])} "
        f"with title {_applescript_string(title)}"
    )
    subprocess.run([settings.tools.osascript, "-e", script], capture_output=True, check=False)


def choose(settings: Settings, prompt: str, options: list[str]) -> str | None:
    """Show a pick-one dialog; returns the chosen option or None when cancelled."""
    items = ", ".join(_applescript_string(o) for o in options)
    script = [
        "activate",
        f"set choice to choose from list {{{items}}} with prompt {_applescript_string(prompt)} "
        f'default items {{{_applescript_string(options[0])}}} with title "Scribe"',
        'if choice is false then return ""',
        "return item 1 of choice",
    ]
    args = [settings.tools.osascript]
    for line in script:
        args += ["-e", line]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    picked = result.stdout.strip()
    return picked if picked in options else None
