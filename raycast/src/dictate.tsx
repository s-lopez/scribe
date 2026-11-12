import {
  Action,
  ActionPanel,
  Clipboard,
  Detail,
  Form,
  Icon,
  Toast,
  closeMainWindow,
  getPreferenceValues,
  popToRoot,
  showToast,
} from "@raycast/api";
import { ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { createInterface } from "node:readline";
import { useEffect, useRef, useState } from "react";

// Thin UI over the scribe CLI; recording, transcription and refinement all happen in Python.
//
//   1. `scribe dictate` records until Enter, then transcribes (protocol: src/scribe/dictate.py).
//   2. The transcript appears in an editable field: paste it, or refine it.
//   3. `scribe refine --stdin --json <session>` refines the (edited) transcript into a second
//      editable field, which can then be pasted.
//   4. `scribe pasted <session>` keeps whatever was finally pasted in the session.
//
// Debugging: scribe runs with --verbose; its log lines are console.logged (see `npm run dev`),
// shown when the "Show Debug Output" preference is on, and kept for error views.

type Backend = "ollama" | "claude";

type DictateEvent =
  | { event: "recording"; session: string; log?: string }
  | { event: "stage"; stage: "waiting" | "transcribing" }
  | { event: "progress"; percent: number }
  | { event: "transcribed"; text: string; session: string }
  | { event: "error"; message: string; session?: string; log?: string }
  | { event: "discarded"; reason?: string };

type RefineEvent =
  | { event: "stage"; stage: "waiting" | "refining"; backend?: Backend }
  | { event: "refined"; text: string; output: string }
  | { event: "error"; message: string };

type Phase =
  | { phase: "starting" }
  | { phase: "recording"; since: number }
  | { phase: "transcribing"; label: string; percent?: number }
  | { phase: "review"; session: string }
  | { phase: "error"; message: string; session?: string };

type Preferences = { scribePath: string; debug?: boolean };

const BACKEND_LABEL: Record<Backend, string> = { ollama: "Gemma (local)", claude: "Claude Haiku" };
const DEBUG_LINES = 40;
// scribe finds its tools itself; this only helps the `scribe` launcher when Raycast's PATH is bare.
const PATH = `/opt/homebrew/bin:/usr/local/bin:${homedir()}/.local/bin:/usr/bin:/bin:${process.env.PATH ?? ""}`;

function expandHome(path: string): string {
  return path.replace(/^~(?=\/|$)/, homedir());
}

function scribePath(): string {
  // Required preference: Raycast asks for it the first time the command runs.
  return expandHome(getPreferenceValues<Preferences>().scribePath.trim());
}

function defaultLogPath(): string {
  // <repo>/.venv/bin/scribe -> <repo>/logs/scribe.log
  return scribePath().replace(/\/\.venv\/bin\/scribe$/, "/logs/scribe.log");
}

function elapsed(since: number, now: number): string {
  const s = Math.max(0, Math.floor((now - since) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function debugLog(...args: unknown[]) {
  console.log(`[scribe ${new Date().toISOString().slice(11, 23)}]`, ...args);
}

function codeBlock(lines: string[]): string {
  return "```\n" + (lines.length ? lines.join("\n") : "(no output yet)") + "\n```";
}

function parseLine<T>(line: string): T | null {
  try {
    return JSON.parse(line) as T;
  } catch {
    debugLog("ignoring non-JSON stdout line", line);
    return null;
  }
}

export default function Command() {
  const { debug } = getPreferenceValues<Preferences>();
  const [view, setView] = useState<Phase>({ phase: "starting" });
  const [now, setNow] = useState(Date.now());
  const [transcript, setTranscript] = useState("");
  const [refined, setRefined] = useState("");
  const [refining, setRefining] = useState<Backend | null>(null);
  const [refineError, setRefineError] = useState<string | null>(null);
  const [stderrLines, setStderrLines] = useState<string[]>([]);
  const [logPath, setLogPath] = useState(defaultLogPath());
  const child = useRef<ChildProcessWithoutNullStreams | null>(null);
  const refiner = useRef<ChildProcessWithoutNullStreams | null>(null);
  const stopped = useRef(false);
  const finished = useRef(false);
  const stderrTail = useRef<string[]>([]);
  const pendingTeardown = useRef<ReturnType<typeof setTimeout> | null>(null);

  function collectStderr(proc: ChildProcessWithoutNullStreams, tag: string) {
    createInterface({ input: proc.stderr }).on("line", (line) => {
      debugLog(`${tag} stderr`, line);
      stderrTail.current = [...stderrTail.current, line].slice(-DEBUG_LINES);
      setStderrLines(stderrTail.current);
    });
  }

  function fail(message: string, session?: string) {
    finished.current = true;
    setView({ phase: "error", message, session });
  }

  // ---- 1. record + transcribe ------------------------------------------------------------

  function start() {
    const cmd = scribePath();
    debugLog("spawning", cmd, "--verbose dictate");
    const proc = spawn(cmd, ["--verbose", "dictate"], { env: { ...process.env, PATH } });
    child.current = proc;
    debugLog("spawned pid", proc.pid);
    // Ignore anything from a process that is no longer the current one.
    const isCurrent = () => child.current === proc;

    collectStderr(proc, "dictate");
    proc.stdin.on("error", (err) => {
      debugLog("stdin error", err);
      if (isCurrent() && !finished.current) fail(`Could not talk to scribe (stdin): ${err.message}`);
    });
    proc.on("error", (err) => {
      debugLog("spawn error", err);
      fail(`Could not start ${cmd}: ${err.message}`);
    });
    proc.on("exit", (code, signal) => {
      debugLog("exit", { pid: proc.pid, code, signal, finished: finished.current, stopped: stopped.current });
      if (isCurrent() && !finished.current) {
        fail(`scribe exited unexpectedly (code ${code ?? "none"}, signal ${signal ?? "none"}).`);
      }
    });

    createInterface({ input: proc.stdout }).on("line", (line) => {
      debugLog("event", line.length > 300 ? line.slice(0, 300) + "…" : line);
      if (!isCurrent()) return;
      const ev = parseLine<DictateEvent>(line);
      if (!ev) return;
      switch (ev.event) {
        case "recording":
          if (ev.log) setLogPath(ev.log);
          setView({ phase: "recording", since: Date.now() });
          break;
        case "stage":
          setView({
            phase: "transcribing",
            label: ev.stage === "waiting" ? "Waiting for another job…" : "Transcribing…",
          });
          break;
        case "progress":
          setView((v) => (v.phase === "transcribing" ? { ...v, percent: ev.percent } : v));
          break;
        case "transcribed":
          finished.current = true;
          setTranscript(ev.text);
          setView({ phase: "review", session: ev.session });
          break;
        case "error":
          if (ev.log) setLogPath(ev.log);
          fail(ev.message, ev.session);
          break;
        case "discarded":
          // Expected only after Esc (when this view is gone). Anything else is a bug worth seeing.
          if (!finished.current) fail(`scribe discarded the recording: ${ev.reason ?? "no reason given"}`);
          break;
      }
    });
  }

  function stopRecording() {
    const proc = child.current;
    debugLog("stop requested", { phase: view.phase, pid: proc?.pid, exitCode: proc?.exitCode });
    if (view.phase !== "recording" || !proc) return;
    if (proc.exitCode !== null || proc.stdin.destroyed) {
      fail(`scribe is no longer running (exit code ${proc.exitCode}).`);
      return;
    }
    stopped.current = true;
    proc.stdin.write("stop\n", (err) => debugLog("stop written", err ?? "ok"));
    setView({ phase: "transcribing", label: "Stopping…" });
  }

  // ---- 2./3. refine the (edited) transcript ---------------------------------------------

  function refine(backend: Backend) {
    if (view.phase !== "review" || refining) return;
    if (!transcript.trim()) {
      showToast({ style: Toast.Style.Failure, title: "Nothing to refine" });
      return;
    }
    setRefining(backend);
    setRefineError(null);
    const args = ["--verbose", "refine", "--backend", backend, "--stdin", "--json", view.session];
    debugLog("spawning refine", args.join(" "));
    const proc = spawn(scribePath(), args, { env: { ...process.env, PATH } });
    refiner.current = proc;
    const isCurrent = () => refiner.current === proc;
    let gotResult = false;

    const failRefine = (message: string) => {
      if (!isCurrent()) return;
      refiner.current = null;
      setRefining(null);
      setRefineError(message);
      showToast({ style: Toast.Style.Failure, title: `Refining with ${BACKEND_LABEL[backend]} failed`, message });
    };

    collectStderr(proc, "refine");
    proc.on("error", (err) => failRefine(`Could not start scribe: ${err.message}`));
    proc.on("exit", (code, signal) => {
      debugLog("refine exit", { pid: proc.pid, code, signal, gotResult });
      if (!gotResult) failRefine(`scribe refine exited (code ${code ?? "none"}, signal ${signal ?? "none"})`);
    });
    createInterface({ input: proc.stdout }).on("line", (line) => {
      debugLog("refine event", line.length > 300 ? line.slice(0, 300) + "…" : line);
      const ev = parseLine<RefineEvent>(line);
      if (!ev || !isCurrent()) return;
      if (ev.event === "refined") {
        gotResult = true;
        refiner.current = null;
        setRefined(ev.text);
        setRefining(null);
      } else if (ev.event === "error") {
        gotResult = true;
        failRefine(ev.message);
      }
    });
    proc.stdin.end(transcript);
  }

  function cancelRefine() {
    const proc = refiner.current;
    refiner.current = null;
    setRefining(null);
    if (proc && proc.exitCode === null) {
      debugLog("killing refine pid", proc.pid);
      proc.kill();
    }
  }

  // ---- 4. paste into the app that was in front ------------------------------------------

  async function paste(text: string, origin: "transcript" | "refined") {
    if (!text.trim()) {
      await showToast({ style: Toast.Style.Failure, title: "Nothing to paste" });
      return;
    }
    cancelRefine();
    if (view.phase === "review") {
      // Synchronous (~0.2 s) so it completes before Raycast closes the window.
      const result = spawnSync(scribePath(), ["--verbose", "pasted", view.session, "--origin", origin], {
        input: text,
        env: { ...process.env, PATH },
        timeout: 5000,
        encoding: "utf-8",
      });
      debugLog("pasted recorded", { status: result.status, error: result.error?.message });
    }
    // Copy first so the text stays on the clipboard, then paste into the app that was in front.
    await Clipboard.copy(text);
    await Clipboard.paste(text);
    await popToRoot({ clearSearchBar: true });
  }

  // ---- lifecycle --------------------------------------------------------------------------

  function teardown() {
    const proc = child.current;
    debugLog("unmount", { pid: proc?.pid, stopped: stopped.current, exitCode: proc?.exitCode });
    finished.current = true;
    cancelRefine();
    if (!proc) return;
    // Esc (or closing the window) while still recording discards the recording.
    if (!stopped.current && proc.exitCode === null) proc.stdin.write("discard\n");
    proc.stdin.end();
  }

  useEffect(() => {
    // React's development mode runs effects twice (mount -> cleanup -> mount) to surface bugs.
    // Starting scribe on each run meant two recorders and two sessions racing for the same
    // folder name. So the real cleanup is deferred by a tick and cancelled on an immediate
    // re-mount: exactly one scribe process per Dictate window.
    if (pendingTeardown.current) {
      clearTimeout(pendingTeardown.current);
      pendingTeardown.current = null;
      debugLog("effect re-run (React dev double-mount): keeping scribe pid", child.current?.pid);
    } else if (!child.current) {
      start();
    }
    return () => {
      pendingTeardown.current = setTimeout(() => {
        pendingTeardown.current = null;
        teardown();
      }, 0);
    };
  }, []);

  useEffect(() => {
    if (view.phase !== "recording") return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [view.phase]);

  // ---- views ------------------------------------------------------------------------------

  const debugSection = debug ? `\n\n---\n\n**Debug output**\n\n${codeBlock(stderrLines.slice(-15))}` : "";
  const logActions = (
    <ActionPanel.Section title="Debug">
      <Action.Open title="Open Debug Log" icon={Icon.Document} target={logPath} />
      <Action.CopyToClipboard title="Copy Debug Output" content={stderrLines.join("\n")} />
    </ActionPanel.Section>
  );

  if (view.phase === "error") {
    return (
      <Detail
        navigationTitle="Scribe"
        markdown={[
          "# ⚠️ Scribe failed",
          codeBlock([view.message]),
          `**Debug output** (full log: \`${logPath}\`)`,
          codeBlock(stderrLines),
        ].join("\n\n")}
        actions={
          <ActionPanel>
            {logActions}
            {view.session ? <Action.ShowInFinder title="Show Session in Finder" path={view.session} /> : null}
            <Action title="Close" icon={Icon.XMarkCircle} onAction={() => closeMainWindow()} />
          </ActionPanel>
        }
      />
    );
  }

  if (view.phase === "recording") {
    const markdown = [
      `# 🔴 Recording  ${elapsed(view.since, now)}`,
      "",
      "**↵** Stop and transcribe  ",
      "**Esc** Discard",
    ].join("\n");
    return (
      <Detail
        navigationTitle="Scribe"
        markdown={markdown + debugSection}
        actions={
          <ActionPanel>
            <Action title="Stop and Transcribe" icon={Icon.Stop} onAction={stopRecording} />
            {logActions}
          </ActionPanel>
        }
      />
    );
  }

  if (view.phase === "review") {
    const hasRefined = refined.trim().length > 0 && !refining;
    const pasteTranscript = (
      <Action
        title="Paste Transcript"
        icon={Icon.Clipboard}
        shortcut={hasRefined ? { modifiers: ["cmd", "shift"], key: "return" } : undefined}
        onAction={() => paste(transcript, "transcript")}
      />
    );
    const refineActions = refining ? (
      <Action title="Cancel Refinement" icon={Icon.XMarkCircle} onAction={cancelRefine} />
    ) : (
      <>
        <Action
          title="Refine with Gemma (Local)"
          icon={Icon.Wand}
          shortcut={{ modifiers: ["cmd", "shift"], key: "g" }}
          onAction={() => refine("ollama")}
        />
        <Action
          title="Refine with Claude Haiku"
          icon={Icon.Stars}
          shortcut={{ modifiers: ["cmd", "shift"], key: "h" }}
          onAction={() => refine("claude")}
        />
      </>
    );
    return (
      <Form
        navigationTitle="Scribe"
        isLoading={refining !== null}
        actions={
          <ActionPanel>
            {hasRefined ? (
              <Action title="Paste Refined Text" icon={Icon.Clipboard} onAction={() => paste(refined, "refined")} />
            ) : null}
            {pasteTranscript}
            <ActionPanel.Section title="Refine">{refineActions}</ActionPanel.Section>
            <Action.ShowInFinder title="Show Session in Finder" path={view.session} />
            {logActions}
          </ActionPanel>
        }
      >
        <Form.TextArea
          id="transcript"
          title="Transcript"
          value={transcript}
          onChange={setTranscript}
          placeholder="No speech detected"
        />
        <Form.TextArea
          id="refined"
          title="Refined"
          value={refined}
          onChange={setRefined}
          placeholder={
            refining
              ? `Refining with ${BACKEND_LABEL[refining]}…`
              : "Refine the transcript above: ⌘⇧G Gemma (local), ⌘⇧H Claude Haiku"
          }
        />
        {refineError ? <Form.Description title="Refinement failed" text={refineError} /> : null}
        <Form.Description
          text={
            hasRefined
              ? "⌘↵ paste refined text · ⌘⇧↵ paste transcript · ⌘⇧G / ⌘⇧H refine again"
              : "⌘↵ paste transcript · ⌘⇧G refine with Gemma · ⌘⇧H refine with Claude"
          }
        />
        {debug ? <Form.Description title="Debug" text={stderrLines.slice(-8).join("\n")} /> : null}
      </Form>
    );
  }

  const label =
    view.phase === "transcribing"
      ? `${view.label}${view.percent !== undefined ? ` ${view.percent}%` : ""}`
      : "Starting microphone…";
  return (
    <Detail
      navigationTitle="Scribe"
      isLoading
      markdown={`# ⏳ ${label}${debugSection}`}
      actions={<ActionPanel>{logActions}</ActionPanel>}
    />
  );
}
