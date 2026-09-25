#!/usr/bin/env python3
"""Prompt Rack — macOS floating panel. Magnetic snap to Terminal windows on drag."""
import signal
signal.signal(signal.SIGHUP, signal.SIG_IGN)
signal.signal(signal.SIGTTOU, signal.SIG_IGN)
signal.signal(signal.SIGTTIN, signal.SIG_IGN)
import subprocess, threading, os, sys, time, json, select, fcntl, traceback, shutil, objc
from AppKit import (NSApplication, NSObject, NSPanel, NSColor, NSScreen,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskNonactivatingPanel,
                    NSBackingStoreBuffered,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSApplicationActivationPolicyAccessory,
                    NSViewWidthSizable, NSViewHeightSizable, NSFloatingWindowLevel,
                    NSImage, NSBezierPath, NSButton, NSAppleScript,
                    NSTitlebarAccessoryViewController, NSView, NSLayoutAttributeTrailing,
                    NSWorkspace, NSWorkspaceDidActivateApplicationNotification, NSWorkspaceDidTerminateApplicationNotification)
from Foundation import NSRect, NSURL, NSTimer, NSProcessInfo
import Quartz
from WebKit import WKWebView, WKWebViewConfiguration
from HIServices import (AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt, AXObserverCreate, AXObserverAddNotification, AXObserverGetRunLoopSource, AXUIElementCreateApplication,
    kAXFocusedWindowChangedNotification, kAXMainWindowChangedNotification, kAXWindowMovedNotification, kAXWindowResizedNotification)
from CoreFoundation import CFRunLoopAddSource, CFRunLoopGetMain, kCFRunLoopDefaultMode

LOG_PATH = os.path.expanduser("~/Library/Logs/Prompt Rack.log")   # every traceback, page error and failed page script lands here; Console.app lists it under Log Reports
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)   # a fresh account may not have ~/Library/Logs yet
sys.stdout = sys.stderr = open(LOG_PATH, "a", buffering=1)
os.dup2(sys.stderr.fileno(), 1)
os.dup2(sys.stderr.fileno(), 2)   # NSLog and C-level errors land in the same file
objc.options.verbose = True   # PyObjC prints the Python traceback whenever an exception crosses into AppKit, WebKit or AX

APP_DIR = os.path.dirname(os.path.abspath(__file__))   # the app's Resources folder, or the source folder
STATE_DIR = os.path.expanduser("~/Library/Application Support/Prompt Rack")
HTML_PATH = os.path.join(APP_DIR, "index.html")
APP_NAME = "Prompt Rack"
STATE_PATH = os.path.join(STATE_DIR, "state.json")
LOCK_PATH = "/tmp/prompt-rack.lock"   # flock held for the app's life; a second launch exits
AUTO_BG_LABEL = "com.promptrack.auto-bg"   # launchd job running auto-bg.py: backgrounds long Claude Code Bash calls in every session
AUTO_BG_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{AUTO_BG_LABEL}.plist")
def auto_bg_running(): return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{AUTO_BG_LABEL}"], capture_output=True).returncode == 0
G = {"wv": None, "panel": None, "snap_timer": None, "snapping": False, "dock_wid": None, "ignore_until": 0.0, "panel_front": False, "panel_visible": False, "anchor_edge": "top", "dock_mode": "smart", "promote_after_submit": False, "window_states": {}, "windows_seeded": False, "state_mtime": 0.0, "editing": False, "edit_mode": False, "settings_mode": False}
_refs = []  # prevent GC of PyObjC objects when backgrounded
TERMINAL_BUNDLE_ID = "com.apple.Terminal"
_apple_scripts = {}
def run_applescript(source):
    script = _apple_scripts.get(source)
    if script is None:
        script = NSAppleScript.alloc().initWithSource_(source)
        _apple_scripts[source] = script
    result, error = script.executeAndReturnError_(None)
    if error:
        raise RuntimeError(str(error))
    text = result.stringValue()
    if text is None:
        raise RuntimeError(f"AppleScript returned no text: {source[:60]!r}")
    return str(text)


class PassivePanel(NSPanel):
    def canBecomeKeyWindow(self):
        return G["editing"]

    def canBecomeMainWindow(self):
        return G["editing"]


class PassiveWebView(WKWebView):
    def acceptsFirstResponder(self):
        return G["editing"]

    def becomeFirstResponder(self):
        return G["editing"]

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


def run_webview_js(script):
    def done(result, error):   # a failed page script is logged, never dropped by WebKit (9-25)
        if error:
            print(f"{time.strftime('%F %T')} page script failed: {script[:120]!r}: {error}")
    G["wv"].evaluateJavaScript_completionHandler_(script, done)


def report(msg):   # the one loud path: a timestamped line in ~/Library/Logs/Prompt Rack.log and a toast on the rack (9-25)
    print(f"{time.strftime('%F %T')} {msg}")
    run_webview_js(f"window._toast({json.dumps(msg[:160])})")


def loud(label, fn, *args):   # every AX, AppKit, timer and WebKit entry point runs through here; raised bare, an error unwinds out of app.run and kills the rack (9-25)
    try:
        fn(*args)
    except Exception as e:
        traceback.print_exc()
        report(f"{label} failed: {e}")


def update_titlebar_controls():
    buttons = G["chrome_buttons"]
    buttons["dock"].setTitle_("Docked" if G["dock_wid"] else "Dock")
    buttons["edit"].setTitle_("Done" if G["edit_mode"] else "Edit")
    buttons["autobg"].setTitle_("BG On" if auto_bg_running() else "BG")
    buttons["settings"].setTitle_("Close" if G["settings_mode"] else "Settings")


def read_state_text():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def write_state_text(text):
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise RuntimeError("State payload must be an object")
    if "sets" not in obj or "activeSet" not in obj:
        raise RuntimeError("State payload missing keys")
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, STATE_PATH)
    return os.path.getmtime(STATE_PATH)


def dock_mode_from_state_text(text):
    obj = json.loads(text)
    return (obj["sets"][obj["activeSet"]] if obj["activeSet"] else obj["current"])["ui"]["dock"]


def apply_dock_mode(mode):
    if mode == G["dock_mode"]:
        return
    G["dock_mode"] = mode
    if mode != "smart":
        G["anchor_edge"] = mode
        G["promote_after_submit"] = False
        for wid in G["window_states"]:
            G["window_states"][wid] = (mode, False)
    dock_to_front_terminal()


def sync_state_from_disk(force=False):
    mtime = os.path.getmtime(STATE_PATH)
    if not force and mtime == G["state_mtime"]:   # the rack's own save: the page already holds this state
        return
    text = read_state_text()
    G["state_mtime"] = mtime
    run_webview_js(f"window._loadState({json.dumps(text)})")
    apply_dock_mode(dock_mode_from_state_text(text))


def cg_windows():
    wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    if wins is None:
        raise RuntimeError("CGWindowListCopyWindowInfo returned nothing")
    return wins


def get_terminal_window_frames():
    return [{"id": int(w["kCGWindowNumber"]), "x": int(w["kCGWindowBounds"]["X"]), "y": int(w["kCGWindowBounds"]["Y"]),
             "w": int(w["kCGWindowBounds"]["Width"]), "h": int(w["kCGWindowBounds"]["Height"])}
            for w in cg_windows() if w["kCGWindowOwnerPID"] == TERM_PID]



def install_titlebar_controls(panel):
    controller = TitlebarController.alloc().init()
    container = NSView.alloc().initWithFrame_(NSRect((0, 0), (316, 24)))
    buttons = {}
    specs = [
        ("dock", "Dock", "dock:", "Snap the rack onto the nearest Terminal window"),
        ("dock_up", "↑", "dockTop:", "Dock the rack on top of the front Terminal window"),
        ("dock_down", "↓", "dockBottom:", "Dock the rack under the front Terminal window"),
        ("edit", "Edit", "toggleEdit:", "Edit buttons and combos; Done saves the open combo"),
        ("autobg", "BG", "toggleAutoBg:", "Auto-background: a Claude Bash command still running after 3.5 s moves to the background"),
        ("settings", "Settings", "toggleSettings:", "Theme, size, dock default, saved sets, JSON"),
    ]
    x = 0
    for key, title, action, tip in specs:
        if key in {"dock_up", "dock_down"}:
            width = 28
        elif key == "settings":
            width = 74
        else:
            width = 54
        button = NSButton.alloc().initWithFrame_(NSRect((x, 0), (width, 24)))
        button.setTitle_(title)
        button.setToolTip_(tip)
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


def dock_to_frame(frame):
    panel = G["panel"]
    pf = panel.frame()
    screen_h = NSScreen.screens()[0].frame().size.height
    if G["anchor_edge"] == "bottom":
        anchor_y = screen_h - frame["y"] - frame["h"]
    else:
        anchor_y = screen_h - frame["y"] - pf.size.height
    G["snapping"] = True
    try:
        G["ignore_until"] = time.monotonic() + 0.35
        panel.setFrame_display_(NSRect((frame["x"], anchor_y), (frame["w"], pf.size.height)), True)
        G["dock_wid"] = int(frame["id"])
        run_webview_js("window._setDocked(true)")
    finally:
        G["snapping"] = False


def dock_to_window(wid):
    frames = [f for f in get_terminal_window_frames() if f["id"] == wid]
    if not frames:
        raise RuntimeError(f"Terminal window {wid} is not on screen")
    dock_to_frame(frames[0])


def activate_window_state(wid, frames):
    states = G["window_states"]
    current = G["dock_wid"]
    if current:
        states[current] = (G["anchor_edge"], G["promote_after_submit"])
    live_ids = {int(frame["id"]) for frame in frames}
    for stale in states.keys() - live_ids:
        del states[stale]
    if not G["windows_seeded"]:
        edge = G["dock_mode"] if G["dock_mode"] != "smart" else "top"
        states.update({window_id: (edge, False) for window_id in live_ids})
        G["windows_seeded"] = True
    if wid not in states:
        states[wid] = (("bottom", True) if G["dock_mode"] == "smart" else (G["dock_mode"], False))
    G["anchor_edge"], G["promote_after_submit"] = states[wid]


def remember_window_state():
    if G["dock_wid"]:
        G["window_states"][G["dock_wid"]] = (G["anchor_edge"], G["promote_after_submit"])


def handle_explicit_dock(edge, message):
    G["panel"].orderFrontRegardless()
    if not dock_to_front_terminal():
        run_webview_js("window._toast('No Terminal window to dock')")
        return
    G["anchor_edge"] = edge   # set after docking: docking restores the window's saved edge
    G["promote_after_submit"] = False
    remember_window_state()
    dock_to_window(G["dock_wid"])
    update_titlebar_controls()
    run_webview_js(f"window._toast('{message}')")


def promote_anchor_to_top():
    G["anchor_edge"] = "top"
    G["promote_after_submit"] = False
    remember_window_state()
    dock_to_window(G["dock_wid"])


def snap_to_nearest():
    if G["snapping"]:
        return
    G["snapping"] = True
    try:
        panel = G["panel"]
        pf = panel.frame()
        px0 = pf.origin.x
        px1 = pf.origin.x + pf.size.width
        screen_h = NSScreen.screens()[0].frame().size.height
        py_top = screen_h - (pf.origin.y + pf.size.height)
        py_bottom = screen_h - pf.origin.y
        best = None
        for w in cg_windows():
            if w["kCGWindowOwnerPID"] != TERM_PID or w["kCGWindowLayer"] != 0:
                continue
            b = w["kCGWindowBounds"]
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
        G["anchor_edge"] = best["edge"] if G["dock_mode"] == "smart" else G["dock_mode"]
        dock_to_frame({"id": best["wid"], "x": best["x"], "y": best["y"], "w": best["w"], "h": best["h"]})
        run_webview_js("window._toast('Docked')")
    finally:
        G["snapping"] = False


def _extract_inject_text(payload):
    if not isinstance(payload, str):
        raise RuntimeError("Inject payload must be JSON text")
    items = json.loads(payload)
    if not isinstance(items, list) or not items:
        raise RuntimeError("Inject payload must be a non-empty list")
    if any(not isinstance(item, dict) or not isinstance(item.get("t"), str) or not item["t"] for item in items):
        raise RuntimeError("Inject item missing text")
    return "\n\n".join(item["t"] for item in items)


def inject(text):
    """Paste text into the docked Terminal tab via Terminal do-script."""
    wid = G["dock_wid"]
    script = (
        "on run argv\n"
        "  set messageText to item 1 of argv\n"
        "  set wid to (item 2 of argv) as integer\n"
        "  set payload to (ASCII character 27) & \"[200~\" & messageText & (ASCII character 27) & \"[201~\"\n"
        "  tell application \"Terminal\"\n"
        "    activate\n"
        "    set index of window id wid to 1\n"
        "    do script payload in selected tab of window id wid\n"
        "  end tell\n"
        "  return \"OK\"\n"
        "end run\n"
    )
    result = subprocess.run(
        ["osascript", "-", text, str(wid)],
        input=script,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode or result.stdout.strip() != "OK":
        err = "\n".join(ln for ln in result.stderr.splitlines() if "ApplePersistence" not in ln)   # osascript prints an ApplePersistence line on every run on this Mac
        raise RuntimeError(f"Terminal inject failed (rc {result.returncode}): {err}")
    if G["promote_after_submit"] and G["dock_mode"] == "smart":
        promote_anchor_to_top()


def focus_terminal_window(wid):
    subprocess.run(["osascript", "-e", f'''tell application "Terminal"
activate
set index of window id {int(wid)} to 1
end tell'''], timeout=2, check=True)


FRONT_WINDOW_SCRIPT = '''tell application "Terminal"
if (count windows) is 0 then return ""
set w to front window
set {l, t, r, b} to bounds of w
return ((id of w) as text) & " " & l & " " & t & " " & r & " " & b
end tell'''


def front_terminal_window():
    # Terminal's own window list: current when an AX event fires, while the window server list is still one move behind (9-25).
    out = run_applescript(FRONT_WINDOW_SCRIPT)
    return tuple(int(v) for v in out.split()) if out else None


def get_front_terminal_window_id():
    front = front_terminal_window()
    return front[0] if front else None


def terminal_is_frontmost():
    front = NSWorkspace.sharedWorkspace().frontmostApplication()   # nil while no app is frontmost, e.g. mid-switch or at the lock screen
    return front is not None and front.bundleIdentifier() == TERMINAL_BUNDLE_ID


def dock_to_front_terminal():
    if not terminal_is_frontmost():
        return False
    front = front_terminal_window()
    if not front:
        return False
    wid, left, top, right, bottom = front
    if G["dock_mode"] != "smart":
        G["anchor_edge"] = G["dock_mode"]
    activate_window_state(wid, get_terminal_window_frames())
    dock_to_frame({"id": wid, "x": left, "y": top, "w": right - left, "h": bottom - top})
    sync_panel_front(wid)
    return True


def sync_panel_front(front_wid=None):
    panel = G["panel"]
    front = False
    if terminal_is_frontmost():
        if front_wid is None:
            front_wid = get_front_terminal_window_id()
        front = front_wid is not None and G["dock_wid"] == front_wid
    if front == G["panel_front"]:
        return
    G["panel_front"] = front
    if front:
        panel.setLevel_(NSFloatingWindowLevel)
        if not G["panel_visible"]:
            panel.orderFrontRegardless()
            G["panel_visible"] = True
        return
    panel.orderOut_(None)
    G["panel_visible"] = False


class SnapTimer(NSObject):
    def fire_(self, timer):
        G["snap_timer"] = None
        loud("Snap", snap_to_nearest)

_snap_target = SnapTimer.alloc().init()
_refs.append(_snap_target)

def schedule_snap():
    timer = G["snap_timer"]
    if timer:
        timer.invalidate()
    snap_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.12, _snap_target, 'fire:', None, False)
    G["snap_timer"] = snap_timer

def reposition_docked_panel():
    if not terminal_is_frontmost() or G["snapping"]:
        sync_panel_front()
        return
    front = front_terminal_window()
    if not front:
        sync_panel_front()
        return
    wid, left, top, right, bottom = front
    if G["dock_wid"] != wid:
        handle_frontmost_change()
        return
    sync_panel_front(wid)
    panel = G["panel"]
    screen_h = NSScreen.screens()[0].frame().size.height
    pf = panel.frame()
    tx, ty, tw, th = left, top, right - left, bottom - top
    anchor_y = screen_h - ty - (th if G["anchor_edge"] == "bottom" else pf.size.height)
    if abs(pf.origin.x - tx) < 2 and abs(pf.origin.y - anchor_y) < 2 and abs(pf.size.width - tw) < 2:
        return
    G["snapping"] = True
    try:
        G["ignore_until"] = time.monotonic() + 0.35
        panel.setFrameOrigin_((tx, anchor_y))
        if abs(pf.size.width - tw) > 2:
            panel.setContentSize_((tw, pf.size.height))
    finally:
        G["snapping"] = False

def handle_frontmost_change():
    if not dock_to_front_terminal():
        sync_panel_front()


class AppWatcher(NSObject):
    def appDidActivate_(self, note):
        loud("App switch", handle_frontmost_change)

    def appDidTerminate_(self, note):
        if note.userInfo()["NSWorkspaceApplicationKey"].bundleIdentifier() == TERMINAL_BUNDLE_ID:   # the AX observer and every window id died with Terminal
            report("Terminal quit; Prompt Rack exits with it")
            app.terminate_(None)

class MainThreadRelay(NSObject):
    def syncState_(self, note):
        loud("State sync", sync_state_from_disk)

class PanelDelegate(NSObject):
    def windowDidMove_(self, note):
        if G["snapping"] or time.monotonic() < G["ignore_until"]:
            return
        if G["dock_wid"]:
            G["dock_wid"] = None
            run_webview_js("window._setDocked(false)")
        schedule_snap()

class TitlebarController(NSObject):
    def dock_(self, sender):
        G["panel"].orderFrontRegardless()
        loud("Dock", snap_to_nearest)
        update_titlebar_controls()

    def dockTop_(self, sender):
        loud("Dock top", handle_explicit_dock, "top", "Docked top")

    def dockBottom_(self, sender):
        loud("Dock bottom", handle_explicit_dock, "bottom", "Docked bottom")

    def toggleEdit_(self, sender):
        G["panel"].orderFrontRegardless()
        run_webview_js("toggleE()")

    def toggleAutoBg_(self, sender):
        loud("BG", toggle_auto_bg)

    def toggleSettings_(self, sender):
        G["panel"].orderFrontRegardless()
        run_webview_js("toggleSettings()")


def handle_bridge(action, payload):
    if action == "error":   # the page's window.onerror and unhandled promise rejections
        report(f"page error: {payload}")
    elif action == "resize":
        panel = G["panel"]
        content_h = int(payload)
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
    elif action == "loadState":   # the page has loaded: hand it the state, then dock where it can show "Docked"
        if os.path.exists(STATE_PATH):
            sync_state_from_disk(True)
        else:
            run_webview_js("resetConfig()")   # first run: the page saves its starter rack, which creates state.json
        handle_frontmost_change()
    elif action == "saveState":
        first_save = not os.path.exists(STATE_PATH)
        G["state_mtime"] = write_state_text(str(payload))
        if first_save:
            threading.Thread(target=watch_state_file, daemon=True).start()   # first run: state.json exists only now
    elif action == "syncChrome":
        chrome = json.loads(str(payload))
        G["edit_mode"] = chrome["editing"]
        G["settings_mode"] = chrome["settings"]
        apply_dock_mode(chrome["dockMode"])
        update_titlebar_controls()
    elif action == "setEditing":
        was_editing = G["editing"]
        G["editing"] = str(payload) == "1"
        if G["editing"] and not was_editing:   # a non-activating panel only shows a caret once it is key and the web view is first responder
            G["panel"].makeKeyWindow()
            G["panel"].makeFirstResponder_(G["wv"])
        if was_editing and not G["editing"] and G["dock_wid"]:
            focus_terminal_window(G["dock_wid"])
    elif action == "inject":
        if not G["dock_wid"]:
            run_webview_js("window._toast('Drag near Terminal to dock')")
            return
        inject(_extract_inject_text(payload))
        run_webview_js("window._toast('Sent')")
    else:
        raise RuntimeError(f"unknown bridge action {action!r}")


def _merge_json(path, empty, edit):   # edits the user's own Claude file in place; a file that does not exist yet starts from empty
    obj = json.load(open(path)) if os.path.exists(path) else empty
    edit(obj)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def install_auto_bg():
    # Three things make the chord work: a SessionStart hook that maps each Claude session to its tty, the keybinding that turns
    # "ctrl+x enter" into task:background, and the launchd job that watches transcripts. Each write merges into the user's file.
    job_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"   # the launchd job's PATH; fswatch must be on it
    if not shutil.which("fswatch", path=job_path):
        raise RuntimeError("Auto-background needs fswatch: run brew install fswatch, then press BG again")
    hook = os.path.join(APP_DIR, "auto-bg-hook.sh")
    claude = os.path.expanduser("~/.claude")
    os.makedirs(claude, exist_ok=True)
    def add_hook(cfg):
        rows = cfg.setdefault("hooks", {}).setdefault("SessionStart", [])
        if not any(h.get("command") == hook for r in rows for h in r["hooks"]):   # only command hooks carry a command
            rows.append({"hooks": [{"type": "command", "command": hook, "timeout": 3}]})
    def add_chord(kb):
        for ctx, binding in (("Chat", {"ctrl+x enter": None}), ("Task", {"ctrl+x enter": "task:background"})):
            rows = [r for r in kb["bindings"] if r["context"] == ctx]
            if not rows:
                rows = [{"context": ctx, "bindings": {}}]
                kb["bindings"].append(rows[0])
            rows[0]["bindings"].update(binding)
    _merge_json(os.path.join(claude, "settings.json"), {}, add_hook)
    _merge_json(os.path.join(claude, "keybindings.json"), {"$schema": "https://www.schemastore.org/claude-code-keybindings.json", "bindings": []}, add_chord)
    log = os.path.expanduser("~/.prompt-rack/auto-bg.log")
    os.makedirs(os.path.expanduser("~/.prompt-rack/tty"), exist_ok=True)
    os.makedirs(os.path.dirname(AUTO_BG_PLIST), exist_ok=True)
    with open(AUTO_BG_PLIST, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>\n'
                f'<key>Label</key><string>{AUTO_BG_LABEL}</string>\n<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{os.path.join(APP_DIR, "auto-bg.py")}</string></array>\n'
                f'<key>EnvironmentVariables</key><dict><key>PATH</key><string>{job_path}</string></dict>\n<key>KeepAlive</key><true/>\n<key>RunAtLoad</key><true/>\n'
                f'<key>StandardOutPath</key><string>{log}</string>\n<key>StandardErrorPath</key><string>{log}</string>\n</dict></plist>\n')
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", AUTO_BG_PLIST], check=True)


def toggle_auto_bg():
    if auto_bg_running():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{AUTO_BG_LABEL}"], check=True)
    else:
        install_auto_bg()
    update_titlebar_controls()


class Handler(NSObject):
    def userContentController_didReceiveScriptMessage_(self, uc, msg):
        data = msg.body()
        loud(data["action"], handle_bridge, data["action"], data["payload"])


def watch_state_file():
    flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_LINK | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE | select.KQ_NOTE_REVOKE
    while True:   # an atomic save replaces state.json, so each replace reopens the watch on the new file
        fd = os.open(STATE_PATH, os.O_EVTONLY)
        kq = select.kqueue()
        kq.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR, fflags=flags)], 0, None)
        while True:
            ev = kq.control(None, 1, None)[0]
            _main_thread_relay.performSelectorOnMainThread_withObject_waitUntilDone_("syncState:", None, False)
            if ev.fflags & (select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE | select.KQ_NOTE_REVOKE):
                break
        kq.close()
        os.close(fd)


def watch_active_terminal():
    # Listener, not a poll (9-22). Terminal's AX observer fires on window focus, main, move, resize; app switches arrive from NSWorkspace below.
    # The callback must be a PyObjC closure: without objc.callbackFor, AXObserverCreate raises "Callable argument is not a PyObjC closure", which killed both 9-22 rewrites.
    # AX trust: macOS asks once (System Settings > Privacy & Security > Accessibility); an untrusted app gets no events, so it stops here instead of docking blind.
    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}): raise RuntimeError("Accessibility is off for Prompt Rack; macOS just asked to turn it on")
    @objc.callbackFor(AXObserverCreate)
    def on_change(obs, el, note, ref): loud("Window event", reposition_docked_panel)
    err, obs = AXObserverCreate(TERM_PID, on_change, None)
    if err: raise RuntimeError(f"AXObserverCreate failed with {err}")
    term = AXUIElementCreateApplication(TERM_PID)
    for n in (kAXFocusedWindowChangedNotification, kAXMainWindowChangedNotification, kAXWindowMovedNotification, kAXWindowResizedNotification):
        err = AXObserverAddNotification(obs, term, n, None)
        if err: raise RuntimeError(f"AXObserverAddNotification {n} failed with {err}")
    CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(obs), kCFRunLoopDefaultMode)
    return obs   # no dock here: the loadState bridge action docks once the page can show it


# --- Launch ---
print(f"{time.strftime('%F %T')} Prompt Rack start, pid {os.getpid()}")
_lock = open(LOCK_PATH, "w")
try: fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: print("another Prompt Rack holds the lock; this launch exits"); os._exit(0)
_terminals = [a for a in NSWorkspace.sharedWorkspace().runningApplications() if a.bundleIdentifier() == TERMINAL_BUNDLE_ID]
if len(_terminals) != 1: raise RuntimeError(f"Prompt Rack docks to Terminal; found {len(_terminals)} running")
TERM_PID = _terminals[0].processIdentifier()

app = NSApplication.sharedApplication()
NSProcessInfo.processInfo().setProcessName_(APP_NAME)
app.setApplicationIconImage_(build_app_icon())
_main_thread_relay = MainThreadRelay.alloc().init()
_refs.append(_main_thread_relay)

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
config.preferences().setValue_forKey_(True, "allowFileAccessFromFileURLs")   # without it a file:// page reports every error as "Script error." with no line (9-25)
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

panel.orderOut_(None)
# Panel is floating level — visible without activation
_refs.extend([panel, wv, config])

watcher = AppWatcher.alloc().init()
_refs.append(watcher)
NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
    watcher, 'appDidActivate:', NSWorkspaceDidActivateApplicationNotification, None)
NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
    watcher, 'appDidTerminate:', NSWorkspaceDidTerminateApplicationNotification, None)

os.makedirs(STATE_DIR, exist_ok=True)
if os.path.exists(STATE_PATH):   # on a first run the watcher starts at the first save instead
    threading.Thread(target=watch_state_file, daemon=True).start()
_ax_observer = watch_active_terminal()   # held for the app's life; a collected observer stops firing

app.run()
