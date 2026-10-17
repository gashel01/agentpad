"""Everything that depends on the OS, in one place: the foreground window, the application
that owns it, the mouse position...

Windows: exactly the original Win32 calls. macOS: see macos.py (AppleScript for
Terminal / iTerm2, lsappinfo). Elsewhere: nothing (the pad works, without window tracking).
"""
from __future__ import annotations

import os
import sys
from typing import Optional

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
# reading sessions' screen (menus, "esc to interrupt"...): Windows console only;
# elsewhere, session state comes from Claude Code hooks (see hooks/agentpad_hook.py)
HAS_SCREEN = IS_WIN


def foreground():
    """Foreground window, comparable to a session's (AgentSessionManager._session_window)."""
    if IS_WIN:
        try:
            import win32gui
            return win32gui.GetForegroundWindow()
        except Exception:
            return None
    if IS_MAC:
        from agentpad import macos
        return macos.foreground()
    return None


def foreground_process_name() -> Optional[str]:
    """Name of the foreground program, lowercase (e.g. "windowsterminal.exe", "terminal")."""
    if IS_WIN:
        try:
            import psutil
            import win32gui
            import win32process
            _, pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
            return psutil.Process(pid).name().lower()
        except Exception:
            return None
    if IS_MAC:
        from agentpad import macos
        return macos.foreground_process_name()
    return None


def cursor_pos() -> tuple:
    if IS_WIN:
        import ctypes

        class _Pt(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = _Pt()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    if IS_MAC:
        from agentpad import macos
        return macos.cursor_pos()
    return (0, 0)
