"""Start agentpad when you log in, in the background (no console, no browser tab).

Windows: a value in HKCU\\...\\Run. macOS: a LaunchAgent. Linux: an XDG autostart entry.
Everything is per user, nothing needs admin rights.

    python -m agentpad.autostart on | off | status
"""
import os
import sys
from pathlib import Path

NAME = "agentpad"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAC_PLIST = Path.home() / "Library/LaunchAgents/com.agentpad.gui.plist"
LINUX_DESKTOP = Path.home() / ".config/autostart/agentpad.desktop"


def _python() -> str:
    """The interpreter running agentpad, windowless on Windows (pythonw)."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def command(config_path) -> list[str]:
    return [_python(), "-m", "agentpad.webui.server", "--background", "--config", str(Path(config_path).resolve())]


def _quote(args: list[str]) -> str:
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in args)


def enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, NAME)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return MAC_PLIST.exists()
    return LINUX_DESKTOP.exists()


def enable(config_path):
    args = command(config_path)
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _quote(args))
    elif sys.platform == "darwin":
        from xml.sax.saxutils import escape
        items = "".join(f"<string>{escape(a)}</string>" for a in args)
        MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
        MAC_PLIST.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0"><dict><key>Label</key><string>com.agentpad.gui</string>'
            f'<key>ProgramArguments</key><array>{items}</array>'
            f'<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
    else:
        LINUX_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        LINUX_DESKTOP.write_text("[Desktop Entry]\nType=Application\nName=agentpad\n"
                                 f"Exec={_quote(args)}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")


def disable():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, NAME)
        except FileNotFoundError:
            pass
    elif sys.platform == "darwin":
        MAC_PLIST.unlink(missing_ok=True)
    else:
        LINUX_DESKTOP.unlink(missing_ok=True)


def main():
    cfg = os.environ.get("AGENTPAD_CONFIG") or str(Path.cwd() / "config.yaml")
    cmd = (sys.argv[1:] or ["status"])[0]
    if cmd == "on":
        enable(cfg)
        print(f"agentpad will start when you log in ({cfg})")
    elif cmd == "off":
        disable()
        print("agentpad will no longer start when you log in")
    else:
        print("on" if enabled() else "off")


if __name__ == "__main__":
    main()
