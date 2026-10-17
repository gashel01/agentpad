"""macOS: the equivalent of the Win32 calls used by the rest of the app (see osfront.py).

A Claude Code session is identified here by the terminal it runs in (its tty, e.g.
/dev/ttys003, given by psutil). Terminal.app and iTerm2 expose each tab's tty via
AppleScript: that's the link between a `claude` process and the window displaying it.

"Window" = ("tty", "/dev/ttys003") for a terminal tab, ("app", "Safari") for any
other foreground application. These values compare like Windows handles.

macOS permissions (requested once, to grant to the application launching agentpad --
Terminal, iTerm2, or python):
  - Automation of Terminal / iTerm2: bring a session to front, close it, tile it;
  - Accessibility: type keys (Confirm, mic...) via pynput.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

REFRESH_S = 0.25          # polling rate for reading the foreground window


# ---- AppleScript ---------------------------------------------------------------------------
def _osa(script: str, timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _q(s: str) -> str:
    """Quoted AppleScript string."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_iterm(name: str) -> bool:
    return (name or "").lower().startswith("iterm")


# ---- foreground application and tab (read continuously, cached) -----------------------
_front = {"handle": None, "name": None, "pid": None}
_front_lock = threading.Lock()
_watcher: Optional[threading.Thread] = None


def _lsappinfo(*args) -> str:
    try:
        return subprocess.run(["lsappinfo", *args], capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return ""


def _read_front():
    """Foreground application via lsappinfo (no permission needed), then, if it's a
    terminal, the tty of its active tab."""
    asn = _lsappinfo("front").strip()
    if not asn:
        return None, None, None
    m = re.search(r'"pid"\s*=\s*(\d+)', _lsappinfo("info", "-only", "pid", asn))
    pid = int(m.group(1)) if m else None
    name = None
    if pid and psutil is not None:
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = None
    if not name:
        m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', _lsappinfo("info", "-only", "name", asn))
        name = m.group(1) if m else None
    tty = ""
    if name == "Terminal":
        tty = _osa('tell application "Terminal" to get tty of selected tab of front window', 1.5)
    elif _is_iterm(name):
        tty = _osa('tell application "iTerm2" to get tty of current session of current window', 1.5)
    handle = ("tty", tty) if tty else ("app", name)
    return handle, name, pid


def _watch():
    while True:
        try:
            h, name, pid = _read_front()
            with _front_lock:
                _front.update(handle=h, name=name, pid=pid)
        except Exception:
            pass
        time.sleep(REFRESH_S)


def _ensure_watcher():
    global _watcher
    if _watcher is None:
        _watcher = threading.Thread(target=_watch, daemon=True)
        _watcher.start()
        time.sleep(0.05)


def foreground():
    _ensure_watcher()
    with _front_lock:
        return _front["handle"]


def foreground_process_name() -> Optional[str]:
    _ensure_watcher()
    with _front_lock:
        return (_front["name"] or "").lower() or None


def _set_front(handle):
    """After bringing a session to the front: no need to wait for the next read."""
    with _front_lock:
        _front["handle"] = handle


# ---- sessions ------------------------------------------------------------------------------
def session_window(pid: int):
    """A session's terminal tab = its tty."""
    if psutil is None:
        return None
    try:
        tty = psutil.Process(pid).terminal()
    except Exception:
        return None
    return ("tty", tty) if tty else None


_FIND = '''
set target to {tty}
if application "Terminal" is running then
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is target then
          {terminal}
        end if
      end repeat
    end repeat
  end tell
end if
if application "iTerm2" is running then
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if tty of s is target then
            {iterm}
          end if
        end repeat
      end repeat
    end repeat
  end tell
end if
return ""
'''


def _on_tab(handle, terminal: str, iterm: str, timeout: float = 3.0) -> str:
    if not handle or handle[0] != "tty":
        return ""
    return _osa(_FIND.format(tty=_q(handle[1]), terminal=terminal, iterm=iterm), timeout)


def bring_to_front(handle) -> bool:
    ok = _on_tab(
        handle,
        terminal='''set miniaturized of w to false
          set selected of t to true
          set index of w to 1
          activate
          return "ok"''',
        iterm='''tell w to select
            tell t to select
            tell s to select
            activate
            return "ok"''') == "ok"
    if ok:
        _set_front(handle)
    return ok


def minimize(handle) -> bool:
    ok = _on_tab(
        handle,
        terminal='''set miniaturized of w to true
          return "ok"''',
        iterm='''try
              set miniaturized of w to true
            end try
            return "ok"''') == "ok"
    if ok and foreground() == handle:
        _set_front(None)
    return ok


def is_minimized(handle) -> bool:
    return _on_tab(handle,
                   terminal='return (miniaturized of w) as text',
                   iterm='''try
              return (miniaturized of w) as text
            end try
            return "false"''') == "true"


def close_session(pid: int, handle) -> None:
    """Stops Claude (SIGTERM, then SIGKILL if it holds on), then closes its tab if it's not
    doing anything anymore: Terminal only closes a window if it has a single tab, iTerm2
    closes just the session. Nothing is typed into any window."""
    if psutil is not None:
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(3)
            except psutil.TimeoutExpired:
                p.kill()
        except psutil.Error:
            pass
    time.sleep(0.8)
    _on_tab(handle,
            terminal='''if (busy of t) is false and (count of tabs of w) is 1 then close w
          return "ok"''',
            iterm='''tell s to close
            return "ok"''')


def tile(handles: list) -> None:
    """Arranges the session windows in a grid on the main screen (visible area)."""
    handles = [h for h in handles if h and h[0] == "tty"]
    if not handles:
        return
    x0, y0, x1, y1 = _work_area()
    n = len(handles)
    cols = max(1, int(n ** 0.5 + 0.999))
    rows = max(1, -(-n // cols))
    cw, ch = (x1 - x0) // cols, (y1 - y0) // rows
    for i, h in enumerate(handles):
        r, c = divmod(i, cols)
        b = f"{{{x0 + c * cw}, {y0 + r * ch}, {x0 + (c + 1) * cw}, {y0 + (r + 1) * ch}}}"
        _on_tab(h,
                terminal=f'''set miniaturized of w to false
          set bounds of w to {b}
          return "ok"''',
                iterm=f'''set bounds of w to {b}
            return "ok"''')
        bring_to_front(h)


def _work_area():
    """Main screen without the menu bar or the Dock, in AppleScript coordinates (origin
    top-left)."""
    try:
        from AppKit import NSScreen
        scr = NSScreen.mainScreen()
        f, v = scr.frame(), scr.visibleFrame()
        top = int(f.size.height - (v.origin.y + v.size.height))
        return int(v.origin.x), top, int(v.origin.x + v.size.width), top + int(v.size.height)
    except Exception:
        return 0, 25, 1440, 900


def spawn(command: str, cwd: str, app: str = "Terminal") -> None:
    """Opens `command` in a new terminal window, in `cwd`. "; exit": when
    Claude quits, so does the shell, and the window can close."""
    line = f"cd {shlex.quote(cwd or '~')} && {command}; exit"
    if _is_iterm(app):
        script = f'''tell application "iTerm2"
  set w to (create window with default profile)
  tell current session of w to write text {_q(line)}
  activate
end tell'''
    else:
        script = f'''tell application "Terminal"
  do script {_q(line)}
  activate
end tell'''
    subprocess.Popen(["osascript", "-e", script])


# ---- miscellaneous --------------------------------------------------------------------------------
def visible_apps() -> list:
    """Open applications (.app), to link a profile to an application. Read from the process
    list: no permission needed."""
    seen = {}
    if psutil is None:
        return []
    for p in psutil.process_iter(["name", "exe"]):
        try:
            exe = p.info.get("exe") or ""
            if ".app/Contents/MacOS/" not in exe or "/Contents/Frameworks/" in exe:
                continue
            app = exe.split(".app/Contents/MacOS/")[0].rsplit("/", 1)[-1]
            seen.setdefault(p.info["name"], app)
        except Exception:
            continue
    skip = {"finder", "dock", "systemuiserver", "controlcenter", "loginwindow"}
    return [{"exe": e, "title": t} for e, t in sorted(seen.items(), key=lambda x: x[0].lower()) if e.lower() not in skip]


def cursor_pos() -> tuple:
    try:
        from AppKit import NSEvent
        p = NSEvent.mouseLocation()
        return (int(p.x), int(p.y))
    except Exception:
        return (0, 0)
