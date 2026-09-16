# Prompt Rack

<p align="center">
  <img src="assets/prompt-rack-hero.svg" alt="Prompt Rack floating over a Terminal window" width="100%">
</p>

A tiny macOS prompt launcher for people who live in Terminal with Claude Code, Cursor, Codex, or any other AI coding loop.

Prompt Rack floats on top of a Terminal window, stores reusable prompt buttons, and sends them into the active shell without making you retype the same steering prompts all day.

## Stop Re-Typing Your Best Prompts

Claude, Cursor, and Codex do better work when you keep steering them toward the behaviors you actually want:

- map the codebase before editing
- reuse existing patterns
- debug root cause instead of symptoms
- check blast radius
- review like a senior engineer
- verify with real commands
- write clean PR and commit summaries

Prompt Rack keeps those high-leverage prompts one click away.

## Starter Pack

The default rack is tuned for Claude/Cursor-style productivity workflows:

| Category | Buttons |
|---|---|
| Context | Map First, Find Pattern, Clarify Goal, Spec Slice, Blast Radius, Explain State |
| Build | Implement, Fast Patch, Refactor Safe, UI Polish, Docs, Continue |
| Debug | Root Cause, Trace Flow, Compare Sibling, Kill Fallback, Fix Tests, Prod Triage |
| Review | Hard Review, Security, Perf, Test Gaps, Simplicity, Red Team |
| Ship | Verify, Commit Msg, PR Summary, Release Note, Ship Loop |

You can edit every button and combo from the built-in settings panel.

## Auto-Background Long Commands

Claude Code shows "ctrl+b to run in background" under any Bash call that runs long, and you have to press it by hand, in the right tab, every time. Press **BG** on the rack once and Prompt Rack does it for you in every Claude session on the machine:

- a listener watches the Claude transcripts (event driven, no polling)
- a Bash call still running after 3.5 seconds gets sent to the background in its own Terminal tab
- nothing is focused, raised, or clicked
- a prompt you are halfway through typing is never submitted (the listener uses a chord, `ctrl+x enter`, bound to the background action)
- subagent shell calls are covered too

Requires `brew install fswatch`. BG installs a Claude Code SessionStart hook (so it knows which tab is which session), the chord in `~/.claude/keybindings.json`, and a launchd job. Sessions already open pick up the chord after a restart. Press BG again to unload the job.

## Features

- Auto-background long Claude Code commands in every session
- Dock to the top or bottom of a Terminal window
- Smart docking mode that picks the nearest edge
- Auto-manager mode for one rack per Terminal window
- Prompt buttons, prompt combos, and shift-to-stack sending
- Local JSON state
- Runs from Python source, a py2app bundle, or the Rust wrapper binary

## Install

Download `Prompt-Rack.app.zip` from the [latest release](https://github.com/krushr1/prompt-rack-release/releases/latest), unzip, drag to Applications, and open it. The app is not notarized yet, so the first launch is right-click, Open.

From source:

```bash
python3 -m pip install pyobjc
python3 app.py
```

Auto-manager launcher:

```bash
./launch.sh
```

`launch.sh` tries, in order:

1. `./target/release/prompt-rack`
2. `~/Applications/Prompt Rack.app`
3. `/Applications/Prompt Rack.app`
4. `./dist/Prompt Rack.app`
5. `python3 app.py`

## Rust Binary Wrapper

Build a single launcher binary:

```bash
cargo build --release
./target/release/prompt-rack --auto-manager
```

The Rust binary embeds `app.py`, `index.html`, and the app icon, writes them into:

```text
~/Library/Application Support/Prompt Rack/runtime
```

User state is stored beside that runtime directory as `state.json`.

The wrapper uses the first Python it can find with `AppKit`, `Quartz`, and `WebKit` available. To force a specific interpreter:

```bash
PROMPT_RACK_PYTHON=/opt/homebrew/bin/python3 ./target/release/prompt-rack
```

This hides loose source files from casual binary distribution. It is not cryptographic DRM; determined users can still inspect strings or runtime files.

## py2app Build

```bash
python3 -m pip install pyobjc py2app setuptools
python3 setup.py py2app
```

The app targets macOS and Apple Terminal.

## License

MIT
