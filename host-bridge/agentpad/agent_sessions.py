"""Tracking agent sessions (Claude Code running in a real terminal on
this machine). Two ways to feed the list:
  - configure()/launch() : manual launch of a named slot (folder +
    command), opened in a native shell (Windows Terminal if available).
  - scan_system() : automatic detection of any `claude` process already
    running (started by the user in their own terminal, not by us).

In both cases, a session's "focus"/"minimize"/"tile" finds the REAL
terminal window (by walking up the process chain -- claude can run
under node, itself under a shell, itself under Windows Terminal)
rather than assuming the process found directly owns a window.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

from agentpad.osfront import IS_MAC, IS_WIN

if IS_MAC:
    from agentpad import macos


def _clean_env() -> dict:
    """Environment for a new session: the app's own, minus the markers of a parent
    Claude Code session (CLAUDECODE, CLAUDE_CODE_*, CLAUDE_PID). Otherwise, if the app was
    launched from Claude Code, the new session thinks it's a sub-session: no transcript, absent
    from /resume, "child" session behavior."""
    return {k: v for k, v in os.environ.items()
            if not (k == "CLAUDECODE" or k == "CLAUDE_PID" or k.startswith("CLAUDE_CODE_"))}


def _find_terminal() -> Optional[str]:
    """Native shell on the machine: Windows Terminal if installed (that's
    what the user normally has at hand), otherwise None (fallback to the
    default console)."""
    return shutil.which("wt") or shutil.which("wt.exe")


@dataclass
class AgentSlot:
    slot_id: str
    name: str = ""
    cwd: str = ""
    command: str = "claude"
    proc: Optional[subprocess.Popen] = None
    started_at: Optional[float] = None
    last_status: str = "offline"
    auto: bool = False  # True = detected by scan_system(), not launched by us


class AgentSessionManager:
    def __init__(self):
        self.slots: dict[str, AgentSlot] = {}
        self._lock = threading.Lock()
        self._shown_pids: set[int] = set()  # windows we've ourselves brought to the front
        self._hwnds: dict[int, int] = {}     # session pid -> terminal window displaying it
        self._tiled = False
        self._titles: dict[int, tuple[str, float]] = {}  # pid -> (tab title, read at)
        self.terminal_app = "Terminal"   # macOS: terminal to open sessions in (Terminal or iTerm2)

    # ---- manual configuration -----------------------------------------
    def configure(self, slot_id: str, name: str = "", cwd: str = "", command: str = "claude"):
        slot_id = str(slot_id)
        with self._lock:
            slot = self.slots.get(slot_id)
            if slot is None:
                slot = AgentSlot(slot_id)
                self.slots[slot_id] = slot
            slot.name, slot.cwd, slot.command = name, cwd, command or "claude"

    def remove(self, slot_id: str):
        with self._lock:
            self.slots.pop(str(slot_id), None)

    def launch(self, slot_id: str) -> dict:
        slot_id = str(slot_id)
        with self._lock:
            slot = self.slots.get(slot_id)
            if slot is None:
                raise ValueError(f"unknown agent slot: {slot_id}")
            if slot.proc is not None and slot.proc.poll() is None:
                return self._describe(slot)  # already running, don't relaunch on top
            slot.proc = self._spawn(slot.command, slot.cwd) or None
            slot.started_at = time.time()
            slot.last_status = "running"
            return self._describe(slot)

    def _spawn(self, command: str, cwd: str) -> subprocess.Popen:
        """Opens `command` in a visible native terminal. Prefers Windows
        Terminal (same experience as sessions opened by hand); -w new
        forces a dedicated window (otherwise WT merges into tabs in an
        existing instance, and we lose the pid -> window mapping)."""
        if IS_MAC:
            macos.spawn(command, cwd, self.terminal_app)
            return None                                  # automatic detection picks it up
        wt = _find_terminal() if os.name == "nt" else None
        if wt:
            # cmd /c: when Claude quits (e.g. "Close" from the pad), the window closes with it
            return subprocess.Popen([wt, "-w", "new", "-d", cwd or ".", "cmd", "/c", command], env=_clean_env())
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        return subprocess.Popen(command, cwd=cwd or None, shell=True, creationflags=flags, env=_clean_env())

    def open_session(self, cwd: str, command: str = "claude"):
        """New Claude session in `cwd`, outside slots: automatic detection picks it up
        as a session opened by hand (it takes the first free agent key)."""
        return self._spawn(command, cwd)

    def kill(self, slot_id: str):
        with self._lock:
            slot = self.slots.get(str(slot_id))
            if slot and slot.proc is not None and slot.proc.poll() is None:
                slot.proc.terminate()

    # ---- automatic detection -------------------------------------------
    # flags that betray an internal-plumbing `claude` process (pty host of
    # a background job, daemon, programmatic continuation) rather than a
    # real session opened by hand in a terminal -- noticed empirically: a
    # running Claude Code background job (including this one itself)
    # spawns several of these, never a real session.
    _INTERNAL_MARKERS = ("--bg-pty-host", "daemon", "--spawned-by", "--reply-on-resume")
    _HEADLESS_ARGS = ("-p", "--print")   # non-interactive runs (scripts, other apps): not a session
    # the Claude desktop app is also called claude.exe (Electron: one main process + renderer, gpu,
    # network... children). None of them is a Claude Code session.
    _DESKTOP_APP_PATHS = ("\\windowsapps\\claude_", "\\anthropicclaude\\", "/claude.app/")

    @classmethod
    def _is_desktop_app(cls, cmdline: list) -> bool:
        exe = (cmdline[0] if cmdline else "").lower()
        return any(a.startswith("--type=") for a in cmdline[1:]) or any(k in exe for k in cls._DESKTOP_APP_PATHS)

    @staticmethod
    def _own_lineage() -> set[int]:
        """Our own pid + all our ancestors, so we never detect
        ourselves as an agent to control."""
        if psutil is None:
            return set()
        lineage = {os.getpid()}
        try:
            proc = psutil.Process(os.getpid())
            for _ in range(10):
                proc = proc.parent()
                if proc is None:
                    break
                lineage.add(proc.pid)
        except Exception:
            pass
        return lineage

    @staticmethod
    def _is_own_session(pid: int, own_session_id: Optional[str]) -> bool:
        """Compares the found process's CLAUDE_CODE_SESSION_ID to our own --
        much more reliable than walking the process lineage: a Claude Code
        background job runs several `claude` processes (pty host, daemon,
        the main process...) that aren't all ancestors of each other, but
        all share the same session id. A real separate session, opened by
        the user in another terminal, has a different session id (or no
        variable at all if launched outside Claude Code)."""
        if not own_session_id or psutil is None:
            return False
        try:
            env = psutil.Process(pid).environ()
        except Exception:
            return False
        return env.get("CLAUDE_CODE_SESSION_ID") == own_session_id

    _TITLE_TTL = 5.0
    _TITLE_PREFIX = re.compile(r"^[^\w]+")   # status symbol Claude Code puts at the start (✳, ◐...)

    def _tab_title(self, pid: int) -> str:
        """Name of the session's terminal tab = its console title, which Claude Code
        sets to the conversation's subject. Read in a throwaway process (see
        console_title.py) and cached for a few seconds. "" if unreadable, or if it's
        still the default title ("Claude Code"): we then fall back to the folder."""
        if os.name != "nt":
            return ""
        now = time.time()
        hit = self._titles.get(pid)
        if hit and now - hit[1] < self._TITLE_TTL:
            return hit[0]
        title = ""
        try:
            r = subprocess.run([sys.executable, "-m", "agentpad.console_title", str(pid)],
                               capture_output=True, timeout=3, creationflags=0x08000000)  # CREATE_NO_WINDOW
            if r.returncode == 0:
                title = self._TITLE_PREFIX.sub("", r.stdout.decode("utf-8", "replace")).strip()
        except Exception:
            title = ""
        if title.lower() == "claude code":
            title = ""
        self._titles[pid] = (title, now)
        return title

    def scan_system(self) -> list[dict]:
        """Real `claude`/`claude.exe` binaries (the CLI sometimes runs under
        node.exe via an npm wrapper, but the binary itself is called
        claude.exe on Windows) -- whether we launched it ourselves or the
        user opened it by hand. Explicitly excludes: internal plumbing
        (bg-pty-host/daemon/spawned-by/reply-on-resume) and our own whole
        process lineage (otherwise THIS job, which is itself a running
        `claude`, would detect itself as an agent to control). One auto
        slot per pid found, recreated/updated on every scan; never saved
        to config.yaml (ephemeral by nature, the pid changes on every
        relaunch)."""
        if psutil is None:
            return []
        own_lineage = self._own_lineage()
        own_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        found: dict[str, tuple] = {}
        for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                info = p.info
                pid = info["pid"]
                if pid in own_lineage:
                    continue
                name = (info.get("name") or "").lower()
                cmdline = info.get("cmdline") or []
                exe = (cmdline[0] if cmdline else "") .lower()
                is_claude_bin = name in ("claude", "claude.exe") or exe.endswith(("claude", "claude.exe"))
                if not is_claude_bin and not IS_WIN and name == "node":
                    is_claude_bin = any(a.endswith(("/claude", "claude-code/cli.js")) for a in cmdline[1:3])
                if not is_claude_bin:
                    continue
                cmd_text = " ".join(cmdline).lower()
                if any(marker in cmd_text for marker in self._INTERNAL_MARKERS):
                    continue
                if self._is_desktop_app(cmdline):
                    continue
                if any(arg in self._HEADLESS_ARGS for arg in cmdline[1:]):
                    continue  # claude -p: driven by another program (no terminal, nothing to show)
                if self._is_own_session(pid, own_session_id):
                    continue
                cwd = info.get("cwd") or ""
                slot_id = f"auto-{pid}"
                folder = os.path.basename(cwd.rstrip("\\/")) or cwd or f"pid {pid}"
                slot = AgentSlot(slot_id, name=self._tab_title(pid) or folder, cwd=cwd, command="claude", auto=True)
                slot.proc = None  # not a process we own -- no .poll()/.terminate()
                slot.last_status = "running"
                found[slot_id] = (slot, pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                continue
        with self._lock:
            # remove old auto-slots that disappeared, add the new/updated ones
            for sid in [s for s, slot in self.slots.items() if slot.auto]:
                if sid not in found:
                    self.slots.pop(sid, None)
            live = {pid for _, pid in found.values()}
            for pid in [p for p in self._titles if p not in live]:
                self._titles.pop(pid, None)
            for sid, (slot, pid) in found.items():
                existing = self.slots.get(sid)
                if existing is None:
                    self.slots[sid] = slot
                else:
                    existing.name = slot.name  # tab title changes as the conversation goes on
                self.slots[sid]._pid = pid  # type: ignore[attr-defined]
        return self.list()

    # ---- windows (focus / minimize / tile) ------------------------------
    def _pid_for_slot(self, slot: AgentSlot) -> Optional[int]:
        if slot.proc is not None:
            return slot.proc.pid
        return getattr(slot, "_pid", None)

    # Processes that never own a session's window: we stop before them. Seen in practice: claude
    # under cmd under explorer.exe -- walking up to explorer opened a FOLDER window.
    _NOT_SESSION_HOSTS = {"explorer.exe", "sihost.exe", "svchost.exe", "services.exe", "wininit.exe",
                          "winlogon.exe", "code.exe", "python.exe", "pythonw.exe"}

    def _window_candidates(self, pid: int) -> set[int]:
        """Fallback when the console can't be found: pid + ancestors (claude under node under
        powershell under a regular terminal), never going past a system shell."""
        candidates = {pid}
        if psutil is None:
            return candidates
        try:
            proc = psutil.Process(pid)
            for _ in range(6):
                proc = proc.parent()
                if proc is None or proc.name().lower() in self._NOT_SESSION_HOSTS:
                    break
                candidates.add(proc.pid)
        except Exception:
            pass
        return candidates

    def _session_window(self, pid: int) -> Optional[int]:
        """Window that really displays the session: its console window, or, under Windows
        Terminal (delegated console, outside the process tree), the owner of its
        PseudoConsoleWindow = the terminal window. Read in a throwaway process, cached.
        macOS: the session's terminal tab, identified by its tty."""
        if IS_MAC:
            return macos.session_window(pid)
        if os.name != "nt":
            return None
        try:
            import win32gui
        except ImportError:
            return None
        h = self._hwnds.get(pid)
        if h and win32gui.IsWindow(h):
            return h
        try:
            r = subprocess.run([sys.executable, "-m", "agentpad.console_title", str(pid), "hwnd"],
                               capture_output=True, timeout=3, creationflags=0x08000000)  # CREATE_NO_WINDOW
            con = int(r.stdout.decode().strip() or 0) if r.returncode == 0 else 0
        except Exception:
            con = 0
        if not con:
            return None
        import ctypes
        owner = ctypes.windll.user32.GetAncestor(con, 3)   # GA_ROOTOWNER
        h = owner if owner and owner != con else con
        if win32gui.GetClassName(h) == "PseudoConsoleWindow":
            return None                                     # console with no visible window
        self._hwnds[pid] = h
        return h

    def _find_hwnds(self, pid: int) -> list:
        if IS_MAC:
            h = macos.session_window(pid)
            return [h] if h else []
        if os.name != "nt":
            return []
        try:
            import win32gui
            import win32process
        except ImportError:
            return []
        h = self._session_window(pid)
        if h:
            return [h]
        candidates = self._window_candidates(pid)
        found = []

        def _cb(hwnd, acc):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if not win32gui.GetWindowText(hwnd):
                return True  # untitled windows = not a real app window
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid in candidates:
                acc.append(hwnd)
            return True

        try:
            win32gui.EnumWindows(_cb, found)
        except Exception:
            pass
        return found

    @staticmethod
    def _safe(fn, *args) -> bool:
        """Calls a win32gui/win32api function and swallows ANY exception
        -- already seen in practice: SetForegroundWindow sometimes refuses
        (normal Windows restriction, a background process doesn't always
        have the right to steal focus), and that exception used to bubble
        up to the serial read thread and silently kill the whole pad.
        These calls are "best effort" by nature, never critical."""
        try:
            fn(*args)
            return True
        except Exception:
            return False

    def session_screen(self, pid: int) -> list[str]:
        """Visible lines of the session's screen (read-only, throwaway process)."""
        if os.name != "nt":
            return []
        try:
            r = subprocess.run([sys.executable, "-m", "agentpad.console_title", str(pid), "screen"],
                               capture_output=True, timeout=2, creationflags=0x08000000)  # CREATE_NO_WINDOW
            return r.stdout.decode("utf-8", "replace").splitlines() if r.returncode == 0 else []
        except Exception:
            return []

    _SEL_ITEM = re.compile(r"^\s*[>❯›]\s*\d+\.\s")

    def needs_attention(self, pid: int, lines: Optional[list] = None) -> bool:
        """The session is waiting for a response: a choice menu or permission prompt is
        open on screen (the usual prompt has disappeared, a numbered option is selected)."""
        if lines is None:
            lines = self.session_screen(pid)
        if not lines:
            return False
        if any("to navigate" in l and ("Enter to select" in l or "Esc to cancel" in l) for l in lines):
            return True
        has_prompt = any(l.startswith(">") and i > 0 and lines[i - 1].lstrip().startswith("─") for i, l in enumerate(lines))
        return not has_prompt and any(self._SEL_ITEM.match(l) for l in lines[-30:])

    _ITEM = re.compile(r"^\s*([>❯›])?\s*(\d+)\.\s+(.*)$")

    @classmethod
    def screen_digest(cls, lines: list, keep: int = 14) -> dict:
        """What we show of a session in the "Now" view: its last lines of
        text (without the input box or the status bar), and the open menu if there is one
        (question + numbered options, which one is selected)."""
        rule = lambda l: l.lstrip().startswith("─")
        end = len(lines)
        for i, l in enumerate(lines):                       # usual input box
            if l.startswith(">") and i > 0 and rule(lines[i - 1]):
                end = i - 1
        menu, first_item = None, None
        items = []
        for i, l in enumerate(lines):
            m = cls._ITEM.match(l)
            if m and (first_item is not None or any(cls._ITEM.match(x) and cls._ITEM.match(x).group(1) for x in lines[i:i + 12])):
                if first_item is None:
                    first_item = i
                items.append({"n": int(m.group(2)), "label": m.group(3).strip(), "selected": bool(m.group(1)), "desc": ""})
            elif items and l.strip() and not rule(l) and l.startswith("     ") and not items[-1]["desc"]:
                items[-1]["desc"] = l.strip()              # description line under an option
        has_footer = any("to navigate" in l for l in lines)
        if items and (has_footer or not any(l.startswith(">") and i > 0 and rule(lines[i - 1]) for i, l in enumerate(lines))):
            q_start = first_item
            while q_start > 0 and not rule(lines[q_start - 1]):
                q_start -= 1
            question = [l.strip() for l in lines[q_start:first_item] if l.strip() and not l.strip().startswith("[")]
            menu = {"question": " ".join(question)[:300], "items": items}
            end = q_start - 1 if q_start > 0 else first_item
        body = [l.rstrip() for l in lines[:max(0, end)]]
        while body and not body[-1].strip():
            body.pop()
        text = [l for l in body if l.strip()][-keep:]
        return {"text": text, "menu": menu}

    def focused_session_window(self) -> Optional[int]:
        """Claude Code session window currently in the foreground, otherwise None."""
        if IS_MAC:
            fg = macos.foreground()
            with self._lock:
                pids = [self._pid_for_slot(s) for s in self.slots.values() if s.auto]
            return fg if fg and any(pid and macos.session_window(pid) == fg for pid in pids) else None
        if os.name != "nt":
            return None
        try:
            import win32gui
        except ImportError:
            return None
        fg = win32gui.GetForegroundWindow()
        with self._lock:
            pids = [self._pid_for_slot(s) for s in self.slots.values() if s.auto]
        for pid in pids:
            if pid and self._session_window(pid) == fg:
                return fg
        return None

    def _bring_to_front(self, h) -> bool:
        """Restores and brings to front. Windows often refuses the foreground to a background
        process: a very brief ALT keypress lifts this lock (usual workaround)."""
        import win32con
        import win32gui
        self._safe(win32gui.ShowWindow, h, win32con.SW_RESTORE if win32gui.IsIconic(h) else win32con.SW_SHOW)
        if self._safe(win32gui.SetForegroundWindow, h):
            return True
        try:
            import win32api
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            ok = self._safe(win32gui.SetForegroundWindow, h)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            return ok
        except Exception:
            return False

    def focus(self, slot_id: str) -> bool:
        """Brings this slot's window to the front, without toggling (no
        minimize if already in front) -- used by the list's "focus"
        button and by the radial menu, unlike toggle_focus() which
        does toggle (used by the 8 keys)."""
        with self._lock:
            slot = self.slots.get(str(slot_id))
            pid = self._pid_for_slot(slot) if slot else None
        if not pid:
            return False
        return self.focus_pid(pid)

    def focus_pid(self, pid: int) -> bool:
        if IS_MAC:
            h = macos.session_window(pid)
            ok = bool(h) and macos.bring_to_front(h)
            if ok:
                self._shown_pids.add(pid)
            return ok
        try:
            import win32con
            import win32gui
        except ImportError:
            return False
        hwnds = self._find_hwnds(pid)
        ok = False
        for h in hwnds:
            ok = self._bring_to_front(h) or ok
        if ok:
            self._shown_pids.add(pid)
        return ok

    def minimize_pid(self, pid: int) -> bool:
        if IS_MAC:
            h = macos.session_window(pid)
            self._shown_pids.discard(pid)
            return bool(h) and macos.minimize(h)
        try:
            import win32con
            import win32gui
        except ImportError:
            return False
        hwnds = self._find_hwnds(pid)
        ok = False
        for h in hwnds:
            ok = self._safe(win32gui.ShowWindow, h, win32con.SW_MINIMIZE) or ok
        self._shown_pids.discard(pid)
        return ok

    def toggle_focus(self, slot_id: str) -> str:
        """Agent key: the session is in front -> hide it; otherwise bring it to front.
        Decided from the window's actual state (not a remembered one: the user may have
        moved or hidden it themselves between two presses). Returns the action taken."""
        with self._lock:
            slot = self.slots.get(str(slot_id))
            pid = self._pid_for_slot(slot) if slot else None
        if not pid:
            return "none"
        if IS_MAC:
            h = macos.session_window(pid)
            if not h:
                return "none"
            if macos.foreground() == h:
                self.minimize_pid(pid)
                return "hidden"
            return "shown" if self.focus_pid(pid) else "none"
        try:
            import win32gui
        except ImportError:
            return "none"
        hwnds = self._find_hwnds(pid)
        if not hwnds:
            return "none"
        h = hwnds[0]
        if win32gui.GetForegroundWindow() == h and not win32gui.IsIconic(h):
            self.minimize_pid(pid)
            return "hidden"
        if self._bring_to_front(h):
            self._shown_pids.add(pid)
            return "shown"
        return "none"

    def toggle_all(self) -> str:
        """On the first call: arranges all tracked sessions in a grid
        on screen. On the next: minimizes them all."""
        with self._lock:
            pids = [self._pid_for_slot(s) for s in self.slots.values()]
        pids = [p for p in pids if p]
        if not pids:
            return "none"
        if self._tiled:
            for pid in pids:
                self.minimize_pid(pid)
            self._tiled = False
            return "hidden"
        self._tile(pids)
        self._tiled = True
        return "shown"

    def _tile(self, pids: list[int]):
        if IS_MAC:
            hs = []
            for pid in pids:
                h = macos.session_window(pid)
                if h and h not in hs:
                    hs.append(h)
            macos.tile(hs)
            self._shown_pids.update(pids)
            return
        try:
            import win32api
            import win32con
            import win32gui
        except ImportError:
            for pid in pids:
                self.focus_pid(pid)
            return
        # one window per session, no duplicates (several tabs = a single window)
        wins = []
        for pid in pids:
            hw = self._find_hwnds(pid)
            if hw and hw[0] not in wins:
                wins.append(hw[0])
        if not wins:
            return
        try:   # work area = screen without the taskbar
            x0, y0, x1, y1 = win32gui.SystemParametersInfo(win32con.SPI_GETWORKAREA)
        except Exception:
            x0, y0, x1, y1 = 0, 0, win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        n = len(wins)
        cols = max(1, int(n ** 0.5 + 0.999))
        rows = max(1, -(-n // cols))
        cw, ch = (x1 - x0) // cols, (y1 - y0) // rows
        for i, h in enumerate(wins):
            r, c = divmod(i, cols)
            self._safe(win32gui.ShowWindow, h, win32con.SW_RESTORE)
            self._safe(win32gui.MoveWindow, h, x0 + c * cw, y0 + r * ch, cw, ch, True)
            self._bring_to_front(h)
        self._shown_pids.update(pids)

    # ---- status -----------------------------------------------------------
    def _describe(self, slot: AgentSlot) -> dict:
        pid = self._pid_for_slot(slot)
        alive = (slot.proc is not None and slot.proc.poll() is None) or (slot.auto and pid is not None)
        if pid is None:
            status = "offline"
        elif slot.proc is not None and not alive:
            status = "done" if (slot.proc.returncode or 0) == 0 else "error"
        elif psutil is not None:
            try:
                p = psutil.Process(pid)
                cpu = p.cpu_percent(interval=0.05)
                status = "running" if cpu > 1.5 else "waiting"
            except Exception:
                status = "running"
        else:
            status = "running"
        slot.last_status = status
        return {
            "slot": slot.slot_id,
            "name": slot.name,
            "cwd": slot.cwd,
            "command": slot.command,
            "alive": alive,
            "status": status,
            "pid": pid,
            "started_at": slot.started_at,
            "auto": slot.auto,
        }

    def list(self) -> list[dict]:
        with self._lock:
            slots = list(self.slots.values())
        return [self._describe(s) for s in slots]
