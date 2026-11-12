# scribe

Local dictation and audio transcription for macOS, with optional cleanup of the transcript.

- **Transcription:** [whisper.cpp](https://github.com/ggml-org/whisper.cpp) with the `large-v3` model, fully
  local.
- **Refinement (optional):** turns a raw transcript into clean text: grammar, spelling, punctuation, filler words
  and paragraphs. No markdown, no summary, same language and meaning. Two backends:
  - `ollama`: `gemma4:12b` via [Ollama](https://ollama.com), thinking disabled, fully local.
  - `claude`: [Claude Code](https://code.claude.com/docs/en/setup) with Haiku, using your own Claude subscription
    (see [Privacy](#privacy)).
- **Every run is kept** in a timestamped session folder: audio, raw transcript, edits, every refinement and
  metadata.

There is one pipeline and three ways in:

| Entry point | Input | Result |
| --- | --- | --- |
| Raycast command **Dictate** (hotkey) | microphone | shown for review and editing, optionally refined, then pasted into the app you were in |
| Shortcut **Transcribe with Scribe** (share sheet, Finder Quick Action) | audio files, e.g. from Voice Memos | clipboard + notification |
| Shortcut **Refine with Scribe** (share sheet, Finder Quick Action) | a session folder or any text file | clipboard + notification |

Plus a command line interface for all of it.

> [!IMPORTANT]
> **Tested only on:** macOS 27.2.1 on Apple Silicon (arm64), Homebrew 7.0, Raycast 1.104, Python 3.15,
> Node 26, whisper.cpp 1.9.5, Ollama 0.40 and Claude Code 2.1.
>
> Other setups are untested and come with no guarantees. Intel Macs and non-Homebrew installs should work, since
> tools are looked up on `PATH` and in the usual install locations. The Raycast and Shortcuts integrations,
> clipboard and notifications are macOS-only. The core command line might work on Linux, but that has never been
> tried. Reports and fixes are welcome.

## Requirements

**Required**

- macOS with [Homebrew](https://brew.sh)
- `ffmpeg` (audio conversion), `whisper.cpp` (transcription), `sox` (microphone recording), `uv` (Python
  environment; installs Python 3.15 for you)
- About 3 GB of disk space for the Whisper model. Apple Silicon is strongly recommended for speed.

**Optional**, only for the feature that needs it:

| For | You need |
| --- | --- |
| Local refinement | [Ollama](https://ollama.com/download) and the `gemma4:12b` model (about 8 GB to download; 16 GB of RAM or more recommended) |
| Claude refinement | [Claude Code](https://code.claude.com/docs/en/setup), logged in with a Claude subscription |
| Dictation hotkey | [Raycast](https://www.raycast.com) and Node.js |
| Share sheet / Finder Quick Actions | the Shortcuts app (built into macOS) |

## Installation

### 1. Tools and code

```bash
brew install ffmpeg whisper.cpp sox uv
git clone https://github.com/s-lopez/scribe.git
cd scribe
uv sync
```

The rest of this README uses `<clone>` for the folder you cloned into, e.g. `~/code/scribe`. The Raycast and
Shortcuts setup need its full path.

### 2. Models

The Whisper model and the voice-activity-detection model go into `models/` (gitignored), under exactly these
names:

```bash
mkdir -p models
curl -L -o models/ggml-large-v3.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
curl -L -o models/ggml-silero-v5.1.2.bin \
  https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin
```

`ggml-large-v3.bin` is about 3.1 GB. To use other files, set `whisper.model` and `whisper.vad_model` in your
settings (see [Settings](#settings)).

### 3. Refinement backends (optional)

**Ollama (local):**

```bash
brew install ollama
brew services start ollama       # or install and run the Ollama app instead
ollama pull gemma4:12b
```

**Claude:** install [Claude Code](https://code.claude.com/docs/en/setup), then run `claude` once in a terminal
and log in with your Claude subscription.

### 4. Your settings (optional)

```bash
cp scribe.example.toml scribe.toml
```

Then edit `scribe.toml` (gitignored). The most useful setting is `whisper.vocabulary`: names and jargon you use
often, so they're spelled correctly. See [Settings](#settings).

### 5. Check that it works

macOS's `say` command can produce a test recording:

```bash
say -o /tmp/scribe-test.aiff "Um, so this is, uh, a quick test of scribe."
uv run scribe transcribe /tmp/scribe-test.aiff                   # prints the transcript
uv run scribe transcribe --refine ollama /tmp/scribe-test.aiff   # if you set up Ollama
uv run scribe transcribe --refine claude /tmp/scribe-test.aiff   # if you set up Claude Code
```

The first transcription is slower while the model loads. If something fails, see [Debugging](#debugging).

## Raycast: Dictate

Install Node.js if needed (`brew install node`), then:

```bash
cd raycast
npm install
npm run dev
```

`npm run dev` adds the extension to Raycast, and it stays there after you stop the command with Ctrl-C. The first
time you open **Dictate**, Raycast asks for the *scribe Executable* preference: enter
`<clone>/.venv/bin/scribe`. To get a hotkey, open Raycast Settings → Extensions → Scribe → Dictate and record one.

Press the hotkey: a Raycast window opens and recording starts right away.

1. **Recording.** `↵` stops and transcribes; `Esc` discards the recording.
2. **Review.** The transcript appears in an editable *Transcript* field. Fix anything you like, then:

   | Key | Action |
   | --- | --- |
   | `⌘ ↵` | paste the transcript into the app you were in |
   | `⌘ ⇧ G` | refine it with Gemma (local) |
   | `⌘ ⇧ H` | refine it with Claude Haiku |

3. **Refined.** The refinement of the (edited) transcript appears in a second, editable *Refined* field. Now
   `⌘ ↵` pastes the refined text and `⌘ ⇧ ↵` the transcript. You can refine again with either backend; the new
   result replaces the field's content, and every result is kept in the session.

All actions are also in the action panel (`⌘ K`). Whatever you paste stays on the clipboard and is saved in the
session as `pasted.<ts>.txt`.

## Shortcuts: share sheet and Finder Quick Actions

Create two shortcuts in the Shortcuts app. For each one:

1. In the shortcut's details (ⓘ), turn on **Use as Quick Action** (tick *Finder*) and **Show in Share Sheet**.
2. Add a single **Run Shell Script** action with *Shell: zsh* and *Pass Input: as arguments*, and replace
   `<clone>` in the script with the full path of your clone.

**Transcribe with Scribe:** receives *Files* (audio).

```bash
"<clone>/.venv/bin/scribe" transcribe --ask --background "$@"
```

**Refine with Scribe:** receives *Files* and *Folders* (a session folder, a transcript, or any `.txt`).

```bash
"<clone>/.venv/bin/scribe" refine --ask --background "$@"
```

`--ask` shows a dialog to choose what to do (transcribe only, or refine with Gemma or Claude). `--background`
copies the input into a session right away, because files shared from Voice Memos can disappear, then returns.
The work finishes in the background, the result goes to the clipboard, and a notification tells you when it's
done.

The shortcuts then appear in the share sheet (for example in Voice Memos: Share → Transcribe with Scribe) and in
Finder's right-click menu under Quick Actions.

## Permissions

macOS asks for these the first time they're needed. If something silently doesn't work, check System Settings
→ Privacy & Security:

- **Microphone:** Raycast (recording).
- **Accessibility:** Raycast (pasting into the app you were in).
- **Shortcuts:** Shortcuts → Settings → Advanced → *Allow Running Scripts*.
- **Notifications:** background results are announced through *Script Editor*; allow its notifications in System
  Settings → Notifications.

## Command line

```bash
uv run scribe transcribe memo.m4a                           # prints the transcript
uv run scribe transcribe --refine claude a.m4a b.ogg        # one session per file
uv run scribe refine --backend ollama sessions/<session>    # refine a session's transcript again
uv run scribe refine --backend claude notes.txt             # any text file (creates a session)
uv run scribe --help
```

Add `--copy` to also copy the result to the clipboard.

## Sessions

```text
sessions/2026-11-12_09-30-12_<source name>/
    audio.m4a | audio.<ext>      recording (AAC) or a copy of the input file
    transcript.txt               raw Whisper output (never modified)
    transcript.edited.<ts>.txt   a transcript you edited in Raycast before refining it
    refined.<backend>.<ts>.txt   one per refinement run, never overwritten
    pasted.<ts>.txt              what was finally pasted from Raycast (after any edits)
    meta.json                    source, settings, timings, a log of every run
    scribe.log                   tool output, for debugging
```

Transcribing a file always creates a new session; refining adds files to an existing one. Sessions are never
deleted automatically.

## Settings

Defaults are in [`src/scribe/config.py`](src/scribe/config.py). Your own settings go in `scribe.toml` at the repo
root (gitignored), or in the file named by `$SCRIBE_CONFIG`. It only needs the values you change;
[`scribe.example.toml`](scribe.example.toml) lists every option with its default. For example:

```toml
[whisper]
language = "auto"                                   # or force one, e.g. "en", "es"
vocabulary = "Kubernetes, PostgreSQL, Grafana."     # spelled correctly by Whisper and the refiner

[ollama]
model = "gemma4:12b"

[claude]
model = "haiku"
```

Unknown or mistyped settings are reported as errors rather than ignored. The refinement prompt is
[`prompts/refine.md`](prompts/refine.md); `{vocabulary}` in it is replaced with `whisper.vocabulary`.

## Privacy

Everything runs locally except the `claude` backend, which sends the transcript to Anthropic. It uses the Claude
Code CLI installed on your machine and your own login. It runs Claude Code without tools, MCP servers, project
context or saved session history, with telemetry and non-essential traffic disabled, and with API keys unset so
your subscription is used. Whether your data is used for training depends on your account's "Help improve
Claude" setting (claude.ai → Settings → Privacy); with it off, data is kept for about 30 days. Subscriptions have
no zero-retention option.

## Limits

- Ollama refinement uses a 32k-token context (roughly two hours of speech) and fails with an error rather than
  truncating. Longer recordings would need chunking, which isn't implemented.
- macOS only (see the tested setup at the top).

## Debugging

Every run appends a detailed, timestamped log to `logs/scribe.log`: environment (PATH, user, tools found, which
settings file), recorder output, every command received and event sent, each tool run with exit code and
duration, and full tracebacks.

```bash
tail -f logs/scribe.log
uv run scribe --verbose transcribe memo.m4a   # the same log, also printed to the terminal
```

For the Raycast extension, run `npm run dev` in `raycast/` and watch the terminal: it prints every event and log
line. The Dictate window also shows the live log while the *Show Debug Output* preference is on (the default),
and any failure opens an error view with the end of the log and an *Open Debug Log* action.

## Development

```bash
uv run ruff format && uv run ruff check
uv run pytest                    # fast tests with fake tools
uv run pytest -m integration     # real whisper.cpp, Ollama and Claude Code (slow)
cd raycast && npm run lint && npm run build
```

`npm run lint` checks types, ESLint rules and formatting. Raycast's own `ray lint` (`npm run lint:store`) also
requires `author` in `raycast/package.json` to be a Raycast account, which only matters for publishing to the
Raycast Store.

## License

[MIT](LICENSE)
