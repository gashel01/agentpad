"""Console title of another process (= terminal tab name), Windows only.

Attaching to another process's console detaches the calling process from its own: this
must therefore run in a throwaway process, never in the server. Usage:
    python -m agentpad.console_title <pid>        -> writes the title in UTF-8 to stdout
    python -m agentpad.console_title <pid> hwnd   -> writes the handle of its console window
    python -m agentpad.console_title <pid> screen -> writes the visible lines of its screen
(under Windows Terminal, it's a "PseudoConsoleWindow" whose owner is the terminal
window that displays the session -- see AgentSessionManager._session_window)
"""
import ctypes
import sys


def _screen(k) -> str:
    """Visible lines of the attached console (read-only)."""
    from ctypes import wintypes

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class RECT(ctypes.Structure):
        _fields_ = [("L", ctypes.c_short), ("T", ctypes.c_short), ("R", ctypes.c_short), ("B", ctypes.c_short)]

    class CSBI(ctypes.Structure):
        _fields_ = [("size", COORD), ("cur", COORD), ("attr", wintypes.WORD), ("win", RECT), ("max", COORD)]

    k.CreateFileW.restype = wintypes.HANDLE
    h = k.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
    info = CSBI()
    if not k.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
        return ""
    w, lines = info.size.X, []
    for y in range(info.win.T, info.win.B + 1):
        buf = ctypes.create_unicode_buffer(w)
        n = wintypes.DWORD()
        k.ReadConsoleOutputCharacterW(h, buf, w, COORD(0, y), ctypes.byref(n))
        lines.append(buf.value.rstrip())
    k.CloseHandle(h)
    return "\n".join(lines)


def main() -> int:
    if sys.platform != "win32" or len(sys.argv) < 2:
        return 1
    k = ctypes.windll.kernel32
    k.FreeConsole()
    if not k.AttachConsole(int(sys.argv[1])):
        return 2
    if len(sys.argv) > 2 and sys.argv[2] == "screen":
        sys.stdout.buffer.write(_screen(k).encode("utf-8"))
        k.FreeConsole()
        return 0
    if len(sys.argv) > 2 and sys.argv[2] == "hwnd":
        h = k.GetConsoleWindow() or 0
        k.FreeConsole()
        sys.stdout.write(str(h))
        return 0
    buf = ctypes.create_unicode_buffer(1024)
    n = k.GetConsoleTitleW(buf, 1024)
    k.FreeConsole()
    sys.stdout.buffer.write((buf.value if n else "").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
