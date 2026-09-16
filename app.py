#!/usr/bin/env python3
"""Prompt Rack — macOS floating panel. Magnetic snap to Terminal windows on drag."""
import signal
signal.signal(signal.SIGHUP, signal.SIG_IGN)
signal.signal(signal.SIGTTOU, signal.SIG_IGN)
signal.signal(signal.SIGTTIN, signal.SIG_IGN)
import sys, subprocess, threading, os, time, json, atexit, select, ctypes
from AppKit import (NSApplication, NSObject, NSPanel, NSColor, NSScreen,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskNonactivatingPanel,
                    NSBackingStoreBuffered,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSApplicationActivationPolicyAccessory,
                    NSApplicationActivationPolicyRegular,
                    NSViewWidthSizable, NSViewHeightSizable, NSFloatingWindowLevel,
                    NSImage, NSBezierPath, NSButton,
                    NSTitlebarAccessoryViewController, NSView, NSLayoutAttributeTrailing,
                    NSWorkspace, NSWorkspaceDidActivateApplicationNotification)
from Foundation import NSRect, NSURL, NSTimer, NSProcessInfo
import Quartz
from WebKit import WKWebView, WKWebViewConfiguration

APP_DIR = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(APP_DIR, "index.html")
APP_PATH = os.path.join(APP_DIR, "app.py")
APP_NAME = "Prompt Rack"
LAUNCH_MARKER = "--prompt-rack-child"
STATE_DIR = os.environ.get("PROMPT_RACK_STATE_DIR", APP_DIR)
STATE_PATH = os.path.join(STATE_DIR, "state.json")
AUTO_PID_PATH = "/tmp/prompt-rack-auto.pid"
AUTO_BG_LABEL = "com.promptrack.auto-bg"   # launchd job running auto-bg.py: backgrounds long Claude Code Bash calls in every session
AUTO_BG_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{AUTO_BG_LABEL}.plist")
ARGS = [arg for arg in sys.argv[1:] if arg and arg != LAUNCH_MARKER]
AUTO_MANAGER = "--auto-manager" in ARGS
NEW_TERMINAL = "--new-terminal" in ARGS
TARGET_WINDOW_ID = next((int(arg.split("=", 1)[1]) for arg in ARGS if arg.startswith("--window-id=")), None)
MANAGER_PID = next((int(arg.split("=", 1)[1]) for arg in ARGS if arg.startswith("--manager-pid=")), None)
TARGET_TTY = next((arg for arg in ARGS if arg not in {"--auto-manager", "--new-terminal"} and not arg.startswith("--window-id=") and not arg.startswith("--manager-pid=") and not arg.startswith("-psn_")), None)
PRIMARY_INSTANCE = not TARGET_TTY and not TARGET_WINDOW_ID
DOCK_MODES = {"smart", "top", "bottom"}
G = {"tty": TARGET_TTY, "wv": None, "panel": None, "snap_timer": None, "snapping": False, "dock_wid": TARGET_WINDOW_ID, "ignore_until": 0.0, "panel_front": False, "panel_visible": True, "anchor_edge": "bottom" if NEW_TERMINAL else "top", "dock_mode": "smart", "promote_after_submit": True if NEW_TERMINAL else False, "state_mtime": 0.0, "tty_sync_at": 0.0, "window_lock_path": None, "editing": False, "edit_mode": False, "settings_mode": False}
_refs = []  # prevent GC of PyObjC objects when backgrounded
NORMAL_WINDOW_LEVEL = Quartz.CGWindowLevelForKey(Quartz.kCGNormalWindowLevelKey)
TERMINAL_BUNDLE_ID = "com.apple.Terminal"
CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
AX = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
CFStringEncodingUTF8 = 0x08000100
AXErrorSuccess = 0
CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
CF.CFStringCreateWithCString.restype = ctypes.c_void_p
CF.CFRunLoopGetCurrent.argtypes = []
CF.CFRunLoopGetCurrent.restype = ctypes.c_void_p
CF.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
CF.CFRunLoopAddSource.restype = None
CF.CFRelease.argtypes = [ctypes.c_void_p]
CF.CFRelease.restype = None
AXObserverCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
AX.AXIsProcessTrusted.argtypes = []
AX.AXIsProcessTrusted.restype = ctypes.c_bool
AX.AXObserverCreate.argtypes = [ctypes.c_int32, AXObserverCallback, ctypes.POINTER(ctypes.c_void_p)]
AX.AXObserverCreate.restype = ctypes.c_int32
AX.AXObserverAddNotification.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
AX.AXObserverAddNotification.restype = ctypes.c_int32
AX.AXObserverRemoveNotification.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
AX.AXObserverRemoveNotification.restype = ctypes.c_int32
AX.AXObserverGetRunLoopSource.argtypes = [ctypes.c_void_p]
AX.AXObserverGetRunLoopSource.restype = ctypes.c_void_p
AX.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
AX.AXUIElementCreateApplication.restype = ctypes.c_void_p
AX.AXUIElementCopyAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
AX.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
KCF_RUN_LOOP_DEFAULT_MODE = ctypes.c_void_p.in_dll(CF, "kCFRunLoopDefaultMode").value
AX_FOCUSED_WINDOW_ATTR = CF.CFStringCreateWithCString(None, b"AXFocusedWindow", CFStringEncodingUTF8)
AX_FOCUSED_WINDOW_CHANGED = CF.CFStringCreateWithCString(None, b"AXFocusedWindowChanged", CFStringEncodingUTF8)
AX_WINDOW_CREATED = CF.CFStringCreateWithCString(None, b"AXWindowCreated", CFStringEncodingUTF8)
AX_WINDOW_MOVED = CF.CFStringCreateWithCString(None, b"AXMoved", CFStringEncodingUTF8)
AX_WINDOW_RESIZED = CF.CFStringCreateWithCString(None, b"AXResized", CFStringEncodingUTF8)
AX_WINDOW_DESTROYED = CF.CFStringCreateWithCString(None, b"AXUIElementDestroyed", CFStringEncodingUTF8)


def child_launch_cmd():
    if getattr(sys, "frozen", False):
        return [os.path.realpath(sys.executable), LAUNCH_MARKER]
    return [sys.executable, APP_PATH, LAUNCH_MARKER]


CHILD_LAUNCH_CMD = child_launch_cmd()


class PassivePanel(NSPanel):
    def canBecomeKeyWindow(self):
        return bool(G.get("editing"))

    def canBecomeMainWindow(self):
        return bool(G.get("editing"))


class PassiveWebView(WKWebView):
    def acceptsFirstResponder(self):
        return bool(G.get("editing"))

    def becomeFirstResponder(self):
        return bool(G.get("editing"))

    def acceptsFirstMouse_(self, event):
        return True

def _fill_round_rect(x, y, w, h, radius, color):
    color.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSRect((x, y), (w, h)), radius, radius).fill()


def build_app_icon():
    size = 512.0
    icon = NSImage.alloc().initWithSize_((size, size))
    icon.lockFocus()
    _fill_round_rect(24, 24, 464, 464, 112, NSColor.colorWithRed_green_blue_alpha_(0.10, 0.12, 0.16, 1.0))
    _fill_round_rect(92, 110, 328, 30, 15, NSColor.colorWithRed_green_blue_alpha_(0.92, 0.95, 0.98, 1.0))
    _fill_round_rect(92, 236, 328, 30, 15, NSColor.colorWithRed_green_blue_alpha_(0.92, 0.95, 0.98, 1.0))
    _fill_round_rect(92, 362, 328, 30, 15, NSColor.colorWithRed_green_blue_alpha_(0.92, 0.95, 0.98, 1.0))
    _fill_round_rect(116, 150, 86, 68, 20, NSColor.colorWithRed_green_blue_alpha_(0.98, 0.49, 0.24, 1.0))
    _fill_round_rect(214, 150, 86, 68, 20, NSColor.colorWithRed_green_blue_alpha_(0.20, 0.63, 0.95, 1.0))
    _fill_round_rect(312, 150, 86, 68, 20, NSColor.colorWithRed_green_blue_alpha_(0.29, 0.78, 0.54, 1.0))
    _fill_round_rect(140, 276, 118, 58, 18, NSColor.colorWithRed_green_blue_alpha_(0.98, 0.78, 0.24, 1.0))
    _fill_round_rect(278, 276, 94, 58, 18, NSColor.colorWithRed_green_blue_alpha_(0.67, 0.47, 0.96, 1.0))
    icon.unlockFocus()
    return icon


def _state_script(text):
    return f"window._loadState({json.dumps(text)})"


def push_state_to_webview(text):
    wv = G.get("wv")
    if not wv:
        return
    wv.evaluateJavaScript_completionHandler_(_state_script(text), None)


def run_webview_js(script):
    wv = G.get("wv")
    if not wv:
        return
    wv.evaluateJavaScript_completionHandler_(script, None)


def update_titlebar_controls():
    buttons = G.get("chrome_buttons") or {}
    if not buttons:
        return
    buttons["dock"].setTitle_("Docked" if G.get("dock_wid") else "Dock")
    buttons["edit"].setTitle_("Done" if G.get("edit_mode") else "Edit")
    buttons["auto"].setTitle_("Auto On" if auto_manager_running() else "Auto")
    buttons["autobg"].setTitle_("BG On" if auto_bg_running() else "BG")
    buttons["settings"].setTitle_("Close" if G.get("settings_mode") else "Settings")


def read_state_text():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def write_state_text(text):
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise RuntimeError("State payload must be an object")
    if "current" not in obj or "sets" not in obj or "activeSet" not in obj:
        raise RuntimeError("State payload missing keys")
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, STATE_PATH)
    return os.path.getmtime(STATE_PATH)


def normalize_dock_mode(mode):
    return mode if mode in DOCK_MODES else "smart"


def dock_mode_from_state_text(text):
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return "smart"
    cfg = obj.get("current")
    active_set = obj.get("activeSet")
    sets = obj.get("sets")
    if isinstance(active_set, str) and active_set and isinstance(sets, dict):
        active_cfg = sets.get(active_set)
        if isinstance(active_cfg, dict):
            cfg = active_cfg
    if not isinstance(cfg, dict):
        return "smart"
    ui = cfg.get("ui")
    if not isinstance(ui, dict):
        return "smart"
    return normalize_dock_mode(ui.get("dock"))


def apply_dock_mode(mode, redock=False):
    mode = normalize_dock_mode(mode)
    changed = mode != G.get("dock_mode")
    G["dock_mode"] = mode
    if mode != "smart":
        G["anchor_edge"] = mode
    if not redock or not changed:
        return
    wid = int(G.get("dock_wid") or 0)
    if wid and dock_to_window(wid):
        sync_panel_front()
        return
    tty = G.get("tty")
    if tty and dock_to_tty(tty):
        sync_panel_front()
        return
    if dock_to_front_terminal():
        sync_panel_front()


def sync_state_from_disk(force=False):
    if not os.path.exists(STATE_PATH):
        if force:
            push_state_to_webview("")
        return
    mtime = os.path.getmtime(STATE_PATH)
    if not force and mtime == G.get("state_mtime"):
        return
    text = read_state_text()
    G["state_mtime"] = mtime
    apply_dock_mode(dock_mode_from_state_text(text), redock=bool(G.get("dock_wid") or G.get("tty")))
    push_state_to_webview(text)


def get_terminal_frames():
    try:
        r = subprocess.run(["osascript", "-e", '''tell application "Terminal"
set o to ""
repeat with w in windows
try
set tty_value to (tty of selected tab of w) as text
if tty_value is not "" then
set o to o & (id of w) & "|" & tty_value & linefeed
end if
end try
end repeat
return o
end tell'''], capture_output=True, text=True, timeout=3)
        win_bounds = {}
        for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []:
            if str(w.get("kCGWindowOwnerName", "")) != "Terminal":
                continue
            wid = int(w.get("kCGWindowNumber", 0))
            if not wid:
                continue
            b = w.get("kCGWindowBounds", {})
            win_bounds[wid] = {
                "x": int(float(b["X"])),
                "y": int(float(b["Y"])),
                "w": int(float(b["Width"])),
                "h": int(float(b["Height"])),
            }
        wins = []
        for line in r.stdout.strip().split("\n"):
            if "|" not in line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            wid = int(parts[0].strip())
            bounds = win_bounds.get(wid)
            if not bounds:
                continue
            wins.append({"id": wid, "tty": parts[1].strip(), "x": bounds["x"], "y": bounds["y"], "w": bounds["w"], "h": bounds["h"]})
        return wins
    except Exception:
        return []

def auto_manager_running():
    try:
        pid = int(open(AUTO_PID_PATH).read().strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        try:
            os.unlink(AUTO_PID_PATH)
        except OSError:
            pass
        return False

def start_auto_manager():
    if auto_manager_running():
        return True
    with open(os.devnull, "wb") as sink:
        subprocess.Popen(CHILD_LAUNCH_CMD + ["--auto-manager"], start_new_session=True, stdout=sink, stderr=sink)
    deadline = time.time() + 3
    while time.time() < deadline:
        if auto_manager_running():
            return True
        time.sleep(0.1)
    return False

def auto_bg_running():
    return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{AUTO_BG_LABEL}"], capture_output=True).returncode == 0

def _merge_json(path, default, edit):
    obj = json.load(open(path)) if os.path.exists(path) else default
    edit(obj)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def start_auto_bg():
    # Three things make the chord work: a SessionStart hook that maps each Claude session to its tty, the keybinding that turns
    # "ctrl+x enter" into task:background, and the launchd job that watches transcripts. Each write merges into the user's file.
    if not any(os.access(os.path.join(p, "fswatch"), os.X_OK) for p in os.environ.get("PATH", "").split(":") + ["/opt/homebrew/bin", "/usr/local/bin"]):
        subprocess.run(["osascript", "-e", 'display alert "Prompt Rack" message "Auto-background needs fswatch. Run: brew install fswatch, then press BG again."'])
        return
    hook = os.path.join(APP_DIR, "auto-bg-hook.sh")
    claude = os.path.expanduser("~/.claude")
    os.makedirs(claude, exist_ok=True)
    def add_hook(cfg):
        rows = cfg.setdefault("hooks", {}).setdefault("SessionStart", [])
        if not any(h.get("command") == hook for r in rows for h in r.get("hooks", [])):
            rows.append({"hooks": [{"type": "command", "command": hook, "timeout": 3}]})
    def add_chord(kb):
        for ctx, binding in (("Chat", {"ctrl+x enter": None}), ("Task", {"ctrl+x enter": "task:background"})):
            row = next((r for r in kb["bindings"] if r.get("context") == ctx), None)
            if row is None:
                row = {"context": ctx, "bindings": {}}
                kb["bindings"].append(row)
            row["bindings"].update(binding)
    _merge_json(os.path.join(claude, "settings.json"), {}, add_hook)
    _merge_json(os.path.join(claude, "keybindings.json"), {"$schema": "https://www.schemastore.org/claude-code-keybindings.json", "bindings": []}, add_chord)
    with open(AUTO_BG_PLIST, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>\n'
                f'<key>Label</key><string>{AUTO_BG_LABEL}</string>\n<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{os.path.join(APP_DIR, "auto-bg.py")}</string></array>\n'
                '<key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>\n<key>KeepAlive</key><true/>\n<key>RunAtLoad</key><true/>\n'
                f'<key>StandardOutPath</key><string>{os.path.expanduser("~/.prompt-rack/auto-bg.log")}</string>\n<key>StandardErrorPath</key><string>{os.path.expanduser("~/.prompt-rack/auto-bg.log")}</string>\n</dict></plist>\n')
    os.makedirs(os.path.expanduser("~/.prompt-rack/tty"), exist_ok=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", AUTO_BG_PLIST])

def stop_auto_bg():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{AUTO_BG_LABEL}"])

def stop_auto_manager():
    try:
        pid = int(open(AUTO_PID_PATH).read().strip())
    except Exception:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.time() + 2
    while time.time() < deadline:
        if not auto_manager_running():
            return False
        time.sleep(0.1)
    return auto_manager_running()


def install_titlebar_controls(panel):
    controller = TitlebarController.alloc().init()
    container = NSView.alloc().initWithFrame_(NSRect((0, 0), (316, 24)))
    buttons = {}
    specs = [
        ("dock", "Dock", "dock:"),
        ("dock_up", "↑", "dockTop:"),
        ("dock_down", "↓", "dockBottom:"),
        ("edit", "Edit", "toggleEdit:"),
        ("auto", "Auto", "toggleAuto:"),
        ("autobg", "BG", "toggleAutoBg:"),
        ("settings", "Settings", "toggleSettings:"),
    ]
    x = 0
    for key, title, action in specs:
        if key in {"dock_up", "dock_down"}:
            width = 28
        elif key == "settings":
            width = 74
        else:
            width = 54
        button = NSButton.alloc().initWithFrame_(NSRect((x, 0), (width, 24)))
        button.setTitle_(title)
        button.setTarget_(controller)
        button.setAction_(action)
        button.setBordered_(True)
        container.addSubview_(button)
        buttons[key] = button
        x += width + 4
    container.setFrame_(NSRect((0, 0), (x, 24)))
    accessory = NSTitlebarAccessoryViewController.alloc().init()
    accessory.setView_(container)
    accessory.setLayoutAttribute_(NSLayoutAttributeTrailing)
    panel.addTitlebarAccessoryViewController_(accessory)
    G["chrome_buttons"] = buttons
    _refs.extend([controller, container, accessory, *buttons.values()])
    update_titlebar_controls()


def claim_primary_instance():
    with open(AUTO_PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def release_primary_instance():
    if not os.path.exists(AUTO_PID_PATH):
        return
    with open(AUTO_PID_PATH, "r", encoding="utf-8") as f:
        owner = f.read().strip()
    if owner == str(os.getpid()):
        os.unlink(AUTO_PID_PATH)


def window_lock_path(wid):
    return f"/tmp/prompt-rack-window-{int(wid)}.pid"


def release_window_lock():
    path = G.get("window_lock_path")
    if not path:
        return
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            owner = f.read().strip()
        if owner == str(os.getpid()):
            os.unlink(path)
    G["window_lock_path"] = None


def acquire_window_lock(wid):
    path = window_lock_path(wid)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            owner = f.read().strip()
        if owner:
            pid = int(owner)
            try:
                os.kill(pid, 0)
            except OSError:
                pass
            else:
                return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    G["window_lock_path"] = path
    atexit.register(release_window_lock)
    return True


def selected_tty_for_window(wid):
    r = subprocess.run(
        ["osascript", "-e", f'tell application "Terminal" to return tty of selected tab of window id {int(wid)}'],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    tty = r.stdout.strip()
    if not tty:
        raise RuntimeError(f"Terminal window {wid} has no selected tty")
    return tty

def get_running_rack_children():
    proc = subprocess.run(
        ["ps", "-axo", "pid=,args="],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    running = {}
    for raw in proc.stdout.splitlines():
        parts = raw.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid = int(parts[0])
        cmdline = parts[1]
        if LAUNCH_MARKER not in cmdline:
            continue
        argv = cmdline.split()
        if "--auto-manager" in argv:
            continue
        wid = None
        for arg in argv:
            if not arg.startswith("--window-id="):
                continue
            wid = int(arg.split("=", 1)[1])
            break
        if not wid:
            continue
        current = running.get(wid)
        if current is None or pid > current:
            running[wid] = pid
    return running

def manager_loop():
    try:
        with open(AUTO_PID_PATH, "w") as f:
            f.write(str(os.getpid()))
        launched = get_running_rack_children()
        pending_until = {}
        initial_scan = True
        while True:
            frames = get_terminal_frames()
            live = {int(frame["id"]) for frame in frames if frame.get("id")}
            running = get_running_rack_children()
            next_launched = {}
            now = time.monotonic()
            for wid, pid in launched.items():
                if wid not in live:
                    pending_until.pop(wid, None)
                    continue
                try:
                    os.kill(pid, 0)
                except OSError:
                    continue
                next_launched[wid] = pid
            for wid, pid in running.items():
                if wid in live:
                    next_launched[wid] = pid
                    pending_until.pop(wid, None)
            launched = next_launched
            for frame in frames:
                wid = int(frame.get("id") or 0)
                tty = frame.get("tty")
                if not wid or not tty or wid in launched:
                    continue
                wait_until = pending_until.get(wid)
                if wait_until and now < wait_until:
                    continue
                cmd = CHILD_LAUNCH_CMD + [tty, f"--window-id={wid}", f"--manager-pid={os.getpid()}"]
                if not initial_scan:
                    cmd.append("--new-terminal")
                proc = subprocess.Popen(cmd, start_new_session=True)
                launched[wid] = proc.pid
                pending_until[wid] = time.monotonic() + 4.0
            initial_scan = False
            time.sleep(0.75)
    finally:
        try:
            os.unlink(AUTO_PID_PATH)
        except OSError:
            pass

def dock_to_frame(frame):
    panel = G.get("panel")
    if not panel:
        return False
    pf = panel.frame()
    screen_h = NSScreen.screens()[0].frame().size.height
    if G.get("anchor_edge") == "bottom":
        anchor_y = screen_h - frame["y"] - frame["h"]
    else:
        anchor_y = screen_h - frame["y"] - pf.size.height
    G["snapping"] = True
    try:
        G["ignore_until"] = time.monotonic() + 0.35
        panel.setFrame_display_(NSRect((frame["x"], anchor_y), (frame["w"], pf.size.height)), True)
        G["dock_wid"] = int(frame["id"])
        G["tty"] = frame["tty"]
        wv = G.get("wv")
        if wv:
            wv.evaluateJavaScript_completionHandler_("window._setDocked(true)", None)
        return True
    finally:
        G["snapping"] = False

def dock_to_tty(tty):
    for frame in get_terminal_frames():
        if frame.get("tty") == tty:
            return dock_to_frame(frame)
    return False


def dock_to_window(wid):
    for frame in get_terminal_frames():
        if int(frame.get("id") or 0) == int(wid):
            G["tty"] = frame["tty"]
            return dock_to_frame(frame)
    return False


def dock_to_edge(edge):
    G["anchor_edge"] = edge
    G["promote_after_submit"] = False
    wid = int(G.get("dock_wid") or 0)
    if wid and dock_to_window(wid):
        sync_panel_front()
        return True
    tty = G.get("tty")
    if tty and dock_to_tty(tty):
        sync_panel_front()
        return True
    if dock_to_front_terminal():
        sync_panel_front()
        return True
    return False


def handle_explicit_dock(edge, message):
    panel = G.get("panel")
    if panel:
        panel.orderFrontRegardless()
    if dock_to_edge(edge):
        update_titlebar_controls()
        run_webview_js(f"window._toast('{message}')")
        return
    run_webview_js("window._toast('No Terminal window to dock')")


def promote_anchor_to_top():
    tty = G.get("tty")
    if not tty:
        raise RuntimeError("No docked tty to promote")
    G["anchor_edge"] = "top"
    G["promote_after_submit"] = False
    if not dock_to_tty(tty):
        raise RuntimeError(f"Failed to promote dock for {tty}")


def snap_to_nearest():
    if G.get("snapping"):
        return
    G["snapping"] = True
    try:
        panel = G.get("panel")
        if not panel or not _term_pid[0]:
            return
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        if not wins:
            return
        pf = panel.frame()
        px0 = pf.origin.x
        px1 = pf.origin.x + pf.size.width
        screen_h = NSScreen.screens()[0].frame().size.height
        py_top = screen_h - (pf.origin.y + pf.size.height)
        py_bottom = screen_h - pf.origin.y
        best = None
        for w in wins:
            if int(w.get("kCGWindowOwnerPID", 0)) != _term_pid[0]:
                continue
            if int(w.get("kCGWindowLayer", 99)) != 0:
                continue
            b = w.get("kCGWindowBounds", {})
            tx, ty, tw, th = float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"])
            overlap = min(px1, tx + tw) - max(px0, tx)
            top_gap = abs(py_top - ty)
            bottom_gap = abs(py_bottom - (ty + th))
            edge = "top" if top_gap <= bottom_gap else "bottom"
            edge_gap = top_gap if edge == "top" else bottom_gap
            if overlap <= 0 or edge_gap > 140:
                continue
            score = (overlap, -edge_gap)
            if not best or score > best["score"]:
                best = {"wid": int(w["kCGWindowNumber"]), "x": tx, "y": ty, "w": tw, "h": th, "edge": edge, "score": score}
        if not best:
            return
        G["anchor_edge"] = best["edge"] if G.get("dock_mode") == "smart" else G["dock_mode"]
        if not dock_to_frame({"id": best["wid"], "tty": G.get("tty"), "x": best["x"], "y": best["y"], "w": best["w"], "h": best["h"]}):
            return

        def get_tty(wid):
            try:
                r = subprocess.run(["osascript", "-e",
                    f"tell application \"Terminal\" to return tty of selected tab of window id {wid}"],
                    capture_output=True, text=True, timeout=2, check=True)
                tty = r.stdout.strip()
                if not tty:
                    raise RuntimeError(f"Terminal window {wid} has no selected tty")
                G["tty"] = tty
            except Exception as e:
                print(f"TTY lookup failed for window {wid}: {e}", file=sys.stderr, flush=True)

        threading.Thread(target=get_tty, args=(best["wid"],), daemon=True).start()
        wv = G.get("wv")
        if wv:
            wv.evaluateJavaScript_completionHandler_("window._setDocked(true);window._toast('Docked')", None)
    finally:
        G["snapping"] = False


def inject(tty, text):
    esc = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    try:
        subprocess.run(["osascript", "-e", f'''tell application "Terminal"
repeat with w in windows
repeat with t in tabs of w
if tty of t is "{tty}" then
set target_wid to id of w
do script "{esc}" in t
activate
set index of w to 1
delay 0.05
tell application "System Events"
key code 36
end tell
return target_wid
end if
end repeat
end repeat
error "TTY not found"
end tell'''], timeout=5, check=True)
        wid = int(G.get("dock_wid") or 0)
        if wid:
            focus_terminal_window(wid)
        if G.get("promote_after_submit") and G.get("dock_mode") == "smart":
            promote_anchor_to_top()
        return True
    except Exception as e:
        wv = G.get("wv")
        if wv:
            msg = str(e).replace("'", "").replace('"', '')[:50]
            wv.evaluateJavaScript_completionHandler_("window._toast('Inject failed: %s')" % msg, None)
        return False


def focus_terminal_window(wid):
    subprocess.run(["osascript", "-e", f'''tell application "Terminal"
activate
set index of window id {int(wid)} to 1
end tell'''], timeout=2, check=True)


def get_front_terminal_window_id():
    r = subprocess.run(
        ["osascript", "-e", 'tell application "Terminal" to return id of front window'],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    front_id = r.stdout.strip()
    if not front_id:
        raise RuntimeError("Terminal front window id missing")
    return int(front_id)


def frontmost_bundle_id():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app:
        return ""
    return str(app.bundleIdentifier() or "")


def dock_to_front_terminal():
    if frontmost_bundle_id() != TERMINAL_BUNDLE_ID:
        return False
    if G.get("dock_mode") != "smart":
        G["anchor_edge"] = G["dock_mode"]
    wid = get_front_terminal_window_id()
    if not dock_to_window(wid):
        return False
    sync_panel_front()
    return True


def sync_panel_front():
    panel = G.get("panel")
    if not panel:
        return
    front = False
    if frontmost_bundle_id() == TERMINAL_BUNDLE_ID:
        front = int(G.get("dock_wid") or 0) == get_front_terminal_window_id()
    if front == G.get("panel_front"):
        return
    G["panel_front"] = front
    if front:
        panel.setLevel_(NSFloatingWindowLevel)
        if not G.get("panel_visible"):
            panel.orderFrontRegardless()
            G["panel_visible"] = True
        return
    panel.orderOut_(None)
    G["panel_visible"] = False


class SnapTimer(NSObject):
    def fire_(self, timer):
        G["snap_timer"] = None
        snap_to_nearest()

_snap_nstimer = [None]
_snap_target = SnapTimer.alloc().init()
_refs.append(_snap_target)

def schedule_snap():
    timer = G.get("snap_timer")
    if timer:
        timer.invalidate()
    snap_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.12, _snap_target, 'fire:', None, False)
    G["snap_timer"] = snap_timer
    _refs.append(snap_timer)

def _get_terminal_pid():
    wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    for w in wins or []:
        if "Terminal" in str(w.get("kCGWindowOwnerName", "")):
            return int(w["kCGWindowOwnerPID"])
    return None

_term_pid = [_get_terminal_pid()]


def ax_require(result, label):
    if result != AXErrorSuccess:
        raise RuntimeError(f"{label} failed: {result}")


def clear_observed_window():
    observer = G.get("ax_observer")
    window = G.get("ax_window")
    if not observer or not window:
        return
    AX.AXObserverRemoveNotification(observer, window, AX_WINDOW_MOVED)
    AX.AXObserverRemoveNotification(observer, window, AX_WINDOW_RESIZED)
    AX.AXObserverRemoveNotification(observer, window, AX_WINDOW_DESTROYED)
    CF.CFRelease(window)
    G["ax_window"] = None


def observe_focused_terminal_window():
    observer = G.get("ax_observer")
    app_element = G.get("ax_app")
    if not observer or not app_element:
        return
    focused = ctypes.c_void_p()
    result = AX.AXUIElementCopyAttributeValue(app_element, AX_FOCUSED_WINDOW_ATTR, ctypes.byref(focused))
    if result != AXErrorSuccess:
        return
    clear_observed_window()
    window = focused.value
    if not window:
        return
    ax_require(AX.AXObserverAddNotification(observer, window, AX_WINDOW_MOVED, None), "observe move")
    ax_require(AX.AXObserverAddNotification(observer, window, AX_WINDOW_RESIZED, None), "observe resize")
    ax_require(AX.AXObserverAddNotification(observer, window, AX_WINDOW_DESTROYED, None), "observe destroy")
    G["ax_window"] = window


def terminal_ax_callback(observer, element, notification, refcon):
    _main_thread_relay.performSelectorOnMainThread_withObject_waitUntilDone_("refreshDock:", None, False)


def ensure_terminal_observer():
    if not AX.AXIsProcessTrusted():
        return False
    pid = _get_terminal_pid()
    if not pid:
        return False
    if G.get("ax_pid") == pid:
        return True
    callback = AXObserverCallback(terminal_ax_callback)
    observer = ctypes.c_void_p()
    ax_require(AX.AXObserverCreate(pid, callback, ctypes.byref(observer)), "create observer")
    app_element = AX.AXUIElementCreateApplication(pid)
    ax_require(AX.AXObserverAddNotification(observer, app_element, AX_FOCUSED_WINDOW_CHANGED, None), "observe focus")
    ax_require(AX.AXObserverAddNotification(observer, app_element, AX_WINDOW_CREATED, None), "observe create")
    run_loop = CF.CFRunLoopGetCurrent()
    source = AX.AXObserverGetRunLoopSource(observer)
    CF.CFRunLoopAddSource(run_loop, source, ctypes.c_void_p(KCF_RUN_LOOP_DEFAULT_MODE))
    G["ax_pid"] = pid
    G["ax_callback"] = callback
    G["ax_observer"] = observer
    G["ax_app"] = app_element
    _refs.extend([callback, observer, app_element, source])
    observe_focused_terminal_window()
    return True


class FollowTimer(NSObject):
    def fire_(self, timer):
        sync_panel_front()
        if G.get("snapping"):
            return
        try:
            G["snapping"] = True
            wid = G.get("dock_wid")
            if not wid or not _term_pid[0]:
                return
            panel = G.get("panel")
            if not panel:
                return
            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
            if not wins:
                return
            matched = False
            for w in wins:
                if int(w.get("kCGWindowNumber", 0)) != int(wid):
                    continue
                matched = True
                b = w.get("kCGWindowBounds", {})
                screen_h = NSScreen.screens()[0].frame().size.height
                pf = panel.frame()
                tx, ty, tw, th = float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"])
                if G.get("anchor_edge") == "bottom":
                    anchor_y = screen_h - ty - th
                else:
                    anchor_y = screen_h - ty - pf.size.height
                if abs(pf.origin.x - tx) < 2 and abs(pf.origin.y - anchor_y) < 2 and abs(pf.size.width - tw) < 2:
                    return
                G["ignore_until"] = time.monotonic() + 0.35
                panel.setFrameOrigin_((tx, anchor_y))
                if abs(pf.size.width - tw) > 2:
                    panel.setContentSize_((tw, pf.size.height))
                now = time.monotonic()
                if now >= G.get("tty_sync_at", 0.0):
                    G["tty"] = selected_tty_for_window(wid)
                    G["tty_sync_at"] = now + 0.8
                return
            if TARGET_TTY and not matched:
                app.terminate_(None)
        finally:
            G["snapping"] = False


_follow_target = FollowTimer.alloc().init()
_refs.append(_follow_target)
_follow_timer = [None]


def start_follow_timer():
    if _follow_timer[0]:
        return
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.25, _follow_target, 'fire:', None, True)
    _follow_timer[0] = timer
    _refs.append(timer)


def stop_follow_timer():
    timer = _follow_timer[0]
    if not timer:
        return
    timer.invalidate()
    _follow_timer[0] = None

def handle_frontmost_change():
    if frontmost_bundle_id() != TERMINAL_BUNDLE_ID:
        stop_follow_timer()
        sync_panel_front()
        return
    if ensure_terminal_observer():
        stop_follow_timer()
        observe_focused_terminal_window()
    else:
        start_follow_timer()
    target_wid = TARGET_WINDOW_ID
    if target_wid:
        dock_to_window(target_wid)
        sync_panel_front()
        return
    target_tty = TARGET_TTY
    if target_tty:
        dock_to_tty(target_tty)
        sync_panel_front()
        return
    if dock_to_front_terminal():
        return


class AppWatcher(NSObject):
    def appDidActivate_(self, note):
        handle_frontmost_change()

class MainThreadRelay(NSObject):
    def syncState_(self, note):
        sync_state_from_disk(True)

    def terminateRack_(self, note):
        app.terminate_(None)

    def refreshDock_(self, note):
        handle_frontmost_change()

class PanelDelegate(NSObject):
    def windowDidMove_(self, note):
        if G.get("snapping"):
            return
        if time.monotonic() < G.get("ignore_until", 0.0):
            return
        if G.get("dock_wid"):
            G["dock_wid"] = None
            G["tty"] = None
            wv = G.get("wv")
            if wv:
                wv.evaluateJavaScript_completionHandler_("window._setDocked(false)", None)
        schedule_snap()

class TitlebarController(NSObject):
    def dock_(self, sender):
        panel = G.get("panel")
        if panel:
            panel.orderFrontRegardless()
        snap_to_nearest()
        update_titlebar_controls()

    def dockTop_(self, sender):
        handle_explicit_dock("top", "Docked top")

    def dockBottom_(self, sender):
        handle_explicit_dock("bottom", "Docked bottom")

    def toggleEdit_(self, sender):
        panel = G.get("panel")
        if panel:
            panel.orderFrontRegardless()
        run_webview_js("window.toggleE&&window.toggleE()")

    def toggleAuto_(self, sender):
        if auto_manager_running():
            stop_auto_manager()
        else:
            start_auto_manager()
        run_webview_js(f"window._setAuto({'true' if auto_manager_running() else 'false'})")
        update_titlebar_controls()

    def toggleAutoBg_(self, sender):
        stop_auto_bg() if auto_bg_running() else start_auto_bg()
        update_titlebar_controls()

    def toggleSettings_(self, sender):
        panel = G.get("panel")
        if panel:
            panel.orderFrontRegardless()
        run_webview_js("window.toggleSettings&&window.toggleSettings()")


class Handler(NSObject):
    def userContentController_didReceiveScriptMessage_(self, uc, msg):
        data = msg.body()
        action = data.get("action", "")
        payload = data.get("payload", "")

        if action == "setTarget":
            G["tty"] = str(payload)
        elif action == "resize":
            panel = G.get("panel")
            if panel:
                try:
                    content_h = int(payload)
                except (ValueError, TypeError):
                    return
                f = panel.frame()
                content_rect = panel.contentRectForFrameRect_(f)
                target_frame = panel.frameRectForContentRect_(
                    NSRect((0, 0), (content_rect.size.width, content_h)))
                dy = target_frame.size.height - f.size.height
                if abs(dy) > 1:
                    panel.setFrame_display_(
                        NSRect((f.origin.x, f.origin.y - dy), (f.size.width, target_frame.size.height)), True)
        elif action == "autoDock":
            snap_to_nearest()
        elif action == "getAutoState":
            G["wv"].evaluateJavaScript_completionHandler_(
                f"window._setAuto({'true' if auto_manager_running() else 'false'})", None)
        elif action == "toggleAuto":
            enabled = not auto_manager_running()
            if enabled:
                start_auto_manager()
            else:
                stop_auto_manager()
            G["wv"].evaluateJavaScript_completionHandler_(
                f"window._setAuto({'true' if auto_manager_running() else 'false'})", None)
        elif action == "loadState":
            sync_state_from_disk(True)
        elif action == "saveState":
            G["state_mtime"] = write_state_text(str(payload))
        elif action == "syncChrome":
            try:
                chrome = json.loads(str(payload) or "{}")
            except json.JSONDecodeError:
                chrome = {}
            G["edit_mode"] = bool(chrome.get("editing"))
            G["settings_mode"] = bool(chrome.get("settings"))
            apply_dock_mode(chrome.get("dockMode"), redock=bool(G.get("dock_wid") or G.get("tty")))
            update_titlebar_controls()
        elif action == "setEditing":
            was_editing = bool(G.get("editing"))
            editing = str(payload) == "1"
            G["editing"] = editing
            if was_editing and not editing:
                wid = int(G.get("dock_wid") or 0)
                if wid:
                    focus_terminal_window(wid)
        elif action == "inject":
            tty = G.get("tty")
            if tty:
                if inject(tty, str(payload)):
                    G["wv"].evaluateJavaScript_completionHandler_("window._toast('Sent')", None)
                return
            G["wv"].evaluateJavaScript_completionHandler_("window._toast('Drag near Terminal to dock')", None)

def watch_state_file():
    vnode_flags = (
        select.KQ_NOTE_WRITE |
        select.KQ_NOTE_EXTEND |
        select.KQ_NOTE_ATTRIB |
        select.KQ_NOTE_LINK |
        select.KQ_NOTE_RENAME |
        select.KQ_NOTE_DELETE
    )
    revoke_flag = getattr(select, "KQ_NOTE_REVOKE", 0)
    open_flag = getattr(os, "O_EVTONLY", os.O_RDONLY)
    while True:
        try:
            fd = os.open(STATE_PATH, open_flag)
        except FileNotFoundError:
            time.sleep(0.1)
            continue
        kq = select.kqueue()
        try:
            event = select.kevent(
                fd,
                filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                fflags=vnode_flags | revoke_flag,
            )
            kq.control([event], 0, None)
            while True:
                ready = kq.control(None, 1, None)
                if not ready:
                    continue
                _main_thread_relay.performSelectorOnMainThread_withObject_waitUntilDone_("syncState:", None, False)
                fflags = ready[0].fflags
                if fflags & (select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE | revoke_flag):
                    break
        finally:
            kq.close()
            os.close(fd)


def watch_manager_exit():
    if not MANAGER_PID:
        return
    try:
        os.kill(MANAGER_PID, 0)
    except OSError:
        _main_thread_relay.performSelectorOnMainThread_withObject_waitUntilDone_("terminateRack:", None, False)
        return
    kq = select.kqueue()
    try:
        event = select.kevent(
            MANAGER_PID,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
            fflags=select.KQ_NOTE_EXIT,
        )
        kq.control([event], 0, None)
        while True:
            ready = kq.control(None, 1, None)
            if not ready:
                continue
            _main_thread_relay.performSelectorOnMainThread_withObject_waitUntilDone_("terminateRack:", None, False)
            return
    finally:
        kq.close()


# --- Launch ---
if PRIMARY_INSTANCE and auto_manager_running():
    os._exit(0)

if AUTO_MANAGER:
    manager_loop()
    os._exit(0)

app = NSApplication.sharedApplication()
NSProcessInfo.processInfo().setProcessName_(APP_NAME)
app.setApplicationIconImage_(build_app_icon())
_main_thread_relay = MainThreadRelay.alloc().init()
_refs.append(_main_thread_relay)
if PRIMARY_INSTANCE:
    claim_primary_instance()
    atexit.register(release_primary_instance)

app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

# An accessory app has no menu bar, so Cmd+C/V/X/A had no responder inside the web view's fields.
from AppKit import NSMenu, NSMenuItem
_menubar = NSMenu.alloc().init()
_edit_item = NSMenuItem.alloc().init()
_menubar.addItem_(_edit_item)
_edit = NSMenu.alloc().initWithTitle_("Edit")
for _title, _sel, _key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
    _edit.addItemWithTitle_action_keyEquivalent_(_title, _sel, _key)
_edit_item.setSubmenu_(_edit)
app.setMainMenu_(_menubar)

screen = NSScreen.screens()[0].visibleFrame()
w, h = min(screen.size.width * 0.6, 1100), 190
x = screen.origin.x + (screen.size.width - w) / 2
y = screen.origin.y + screen.size.height - h - 10

style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable | NSWindowStyleMaskNonactivatingPanel
panel = PassivePanel.alloc().initWithContentRect_styleMask_backing_defer_(
    NSRect((x, y), (w, h)), style, NSBackingStoreBuffered, False)
panel.setTitle_("Prompt Rack")
panel.setLevel_(NSFloatingWindowLevel)
panel.setFloatingPanel_(True)
panel.setHidesOnDeactivate_(False)
panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
panel.setMovableByWindowBackground_(True)
panel.setTitleVisibility_(1)
panel.setBackgroundColor_(NSColor.colorWithRed_green_blue_alpha_(0.96, 0.96, 0.97, 0.98))
panel.setToolbarStyle_(4)
G["panel"] = panel
G["panel_visible"] = False
install_titlebar_controls(panel)

# Delegate for magnetic snap
delegate = PanelDelegate.alloc().init()
_refs.append(delegate)
panel.setDelegate_(delegate)

config = WKWebViewConfiguration.alloc().init()
handler = Handler.alloc().init()
_refs.append(handler)
config.userContentController().addScriptMessageHandler_name_(handler, "bridge")

wv = PassiveWebView.alloc().initWithFrame_configuration_(panel.contentView().bounds(), config)
wv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
wv.setValue_forKey_(False, "drawsBackground")
panel.contentView().addSubview_(wv)
G["wv"] = wv

url = NSURL.fileURLWithPath_(HTML_PATH)
wv.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())
sync_state_from_disk(True)

panel.orderOut_(None)
# Panel is floating level — visible without activation
_refs.extend([panel, wv, config])

watcher = AppWatcher.alloc().init()
_refs.append(watcher)
NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
    watcher, 'appDidActivate:', NSWorkspaceDidActivateApplicationNotification, None)

threading.Thread(target=watch_state_file, daemon=True).start()

if PRIMARY_INSTANCE:
    handle_frontmost_change()
elif TARGET_TTY:
    if TARGET_WINDOW_ID:
        if not acquire_window_lock(TARGET_WINDOW_ID):
            os._exit(0)
        G["tty"] = selected_tty_for_window(TARGET_WINDOW_ID)
        dock_to_window(TARGET_WINDOW_ID)
    else:
        G["tty"] = TARGET_TTY
        dock_to_tty(TARGET_TTY)
    if ensure_terminal_observer():
        observe_focused_terminal_window()
    else:
        start_follow_timer()
    sync_panel_front()

app.run()
