#!/usr/bin/env python3
# auto-bg: sends every long Claude Code Bash call to the background, in every Claude session, without touching focus.
# fswatch (brew install fswatch) reports each transcript write under ~/.claude/projects. A Bash tool_use row with no
# tool_result 3.5 s later (again at 5 s and 8 s) gets ctrl+x typed into that session's Terminal tab. Terminal's `do script`
# forces an Enter after it, and Prompt Rack binds the chord "ctrl+x enter" to task:background in ~/.claude/keybindings.json,
# so a draft sitting in the composer is never submitted. A result arriving first cancels the timers; a spare chord is a no-op.
# The tab for a transcript comes from ~/.prompt-rack/tty/<session-uuid> ("ttysNNN PID"), written by auto-bg-hook.sh at
# SessionStart, because Claude does not keep its transcript open and lsof cannot map it. Subagent transcripts live in
# <parent-uuid>/subagents/agent-*.jsonl and use the parent's tab. Kept alive by launchd; Prompt Rack's BG button loads and unloads it.
# The log (~/.prompt-rack/auto-bg.log) holds failures only: a keystroke that finds no tab raises with osascript's own error.
import glob, json, os, subprocess, threading
D = os.path.expanduser("~/.claude/projects")
M = os.path.expanduser("~/.prompt-rack/tty/")
SCRIPT = 'tell application "Terminal"\nrepeat with w in windows\nrepeat with t in tabs of w\nif tty of t is "/dev/%s" then\ndo script (ASCII character 24) in t\nreturn "ok"\nend if\nend repeat\nend repeat\nend tell'
def tty_of(path):
    d, b = os.path.split(path)
    if os.path.basename(d) == "subagents": b = os.path.basename(os.path.dirname(d)) + ".jsonl"
    m = M + b[:-6]
    if not os.path.exists(m): return None   # a session started before BG was on, or outside Terminal, has no map: no tab to type into
    tty = open(m).read().split()[0]
    return None if tty == "??" else tty   # "??" = a headless session (claude -p, a spawned agent): no tab
def press(tty):
    r = subprocess.run(["osascript", "-e", SCRIPT % tty], capture_output=True, text=True)
    if r.stdout.strip() != "ok": raise RuntimeError(f"no Terminal tab on /dev/{tty}: {r.stderr.strip()}")
pos, timers = {p: os.path.getsize(p) for p in glob.glob(D + "/**/*.jsonl", recursive=True)}, {}   # seeded, so nothing from before this start is replayed
for ev in subprocess.Popen(["fswatch", "-r", "-l", "0.02", "-e", ".*", "-i", r"\.jsonl$", D], stdout=subprocess.PIPE, text=True).stdout:
    path = ev.strip()
    try: f = open(path)
    except FileNotFoundError: continue   # fswatch reports deletions too; a deleted transcript has nothing to read
    with f:
        f.seek(pos.get(path, 0)); new = f.read(); new = new[:new.rfind("\n") + 1]; pos[path] = pos.get(path, 0) + len(new.encode())   # whole lines only; a new transcript starts at 0
    for line in new.split("\n")[:-1]:   # not splitlines(): it also breaks on U+2028 inside a JSON string
        msg = json.loads(line).get("message")   # summary and snapshot rows have no message; system rows carry null
        if not msg or isinstance(msg["content"], str): continue   # a typed user turn is a plain string, never a tool call
        for c in msg["content"]:
            if c["type"] == "tool_use" and c["name"] == "Bash" and not c["input"].get("run_in_background") and (tty := tty_of(path)):   # run_in_background is optional; absent means foreground
                timers[c["id"]] = [threading.Timer(s, press, [tty]) for s in (3.5, 5.0, 8.0)]
                for t in timers[c["id"]]: t.start()
            if c["type"] == "tool_result" and c["tool_use_id"] in timers:
                for t in timers.pop(c["tool_use_id"]): t.cancel()
