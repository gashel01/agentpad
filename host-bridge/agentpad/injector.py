import os

from pynput.keyboard import Controller, Key

_SPECIAL = {
    "enter": Key.enter,
    "esc": Key.esc,
    "escape": Key.esc,
    "tab": Key.tab,
    "space": Key.space,
    "backspace": Key.backspace,
    "ctrl": Key.ctrl,
    "shift": Key.shift,
    "alt": Key.alt,
    "cmd": Key.cmd,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "page_up": Key.page_up,
    "page_down": Key.page_down,
    "volume_up": Key.media_volume_up,
    "volume_down": Key.media_volume_down,
    "volume_mute": Key.media_volume_mute,
    "play_pause": Key.media_play_pause,
}
# Media keys: they don't type anything into any window (the system handles them), so the
# allowed-apps guard rail doesn't apply to them -- knob volume must work regardless of
# which window is in the foreground.
_MEDIA = {"volume_up", "volume_down", "volume_mute", "play_pause"}


class KeyInjector:
    """Sends keystrokes to the window that has Windows focus --
    which is dangerous without a guard rail: pressing a key on the pad can type
    into ANY window, including another Claude Code session open
    alongside it (seen in practice: a "/clear" bind erased a conversation
    because the wrong window had focus at the moment of
    the keypress).

    `allowed_processes`: list of process names (e.g. "WindowsTerminal.exe")
    in which injection is allowed. Empty/None by default = NO
    injection (safer than injecting everywhere by default) -- explicitly
    configure the target app(s) before using combo/text/macro.
    """

    def __init__(self, allowed_processes: list[str] | None = None):
        self._kb = Controller()
        self.allowed_processes = [p.lower() for p in (allowed_processes or [])]
        # rule supplied by the server according to the profile (see webui/server.py): Agents
        # profile -> only a Claude Code session in the foreground; macropad profile -> the app
        # in the foreground (that's the point of a macropad). An allowed_processes list is still accepted.
        self.policy = None

    def _foreground_process_name(self) -> str | None:
        if os.name != "nt":
            from agentpad.osfront import foreground_process_name
            return foreground_process_name()
        try:
            import win32gui
            import win32process
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return psutil.Process(pid).name()
        except Exception:
            return None

    def _is_allowed(self) -> bool:
        if self.policy is not None:
            try:
                if self.policy():
                    return True
            except Exception:
                pass
        if not self.allowed_processes:
            return False  # not configured -- we block rather than inject blindly
        name = self._foreground_process_name()
        if name is None:
            return False  # can't verify -- out of caution, we don't inject
        return name.lower() in self.allowed_processes

    def send_text(self, text: str, verified: bool = False):
        if not verified and not self._is_allowed():
            return
        self._kb.type(text)

    def send_combo(self, combo: str, verified: bool = False):
        """combo: e.g. 'ctrl+c', 'enter', 'ctrl+shift+n' (separator '+').
        `verified`: the caller has already checked that the foreground window is the right one
        (e.g. the agent mode's mic, which requires a Claude Code session window)."""
        media_only = all(p.strip().lower() in _MEDIA for p in combo.split("+"))
        if not verified and not media_only and not self._is_allowed():
            return
        keys = [self._resolve(part.strip()) for part in combo.split("+")]
        for k in keys:
            self._kb.press(k)
        for k in reversed(keys):
            self._kb.release(k)

    def key_down(self, name: str):
        """Press only, without releasing -- reserved for flows that have already checked the
        foreground window (the agent mode's mic simulates holding Space down)."""
        self._kb.press(self._resolve(name))

    def key_up(self, name: str):
        self._kb.release(self._resolve(name))

    def _resolve(self, name: str):
        low = name.lower()
        if low in _SPECIAL:
            return _SPECIAL[low]
        return name
