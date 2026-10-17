"""Claude Code -> agentpad hook: tells the app what a session is doing.

Claude Code runs this command on every event (prompt submitted, tool, question,
permission, end of response...) with the details as JSON on standard input. We find the
session's `claude` process (an ancestor of this script) and forward the essentials to
http://127.0.0.1:8420/api/hook.

Essential on macOS (no terminal screen reading there); optional on Windows.
Never blocks Claude: short timeout, no output, always exit code 0. Installation:
`agentpad-hooks install` (see install.py).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

URL = os.environ.get("AGENTPAD_URL", "http://127.0.0.1:8420") + "/api/hook"


def _claude_pid() -> int | None:
    """First ancestor that is the `claude` binary (or `node .../claude`)."""
    try:
        import psutil
        p = psutil.Process(os.getppid())
        for _ in range(8):
            if p is None:
                break
            name = p.name().lower()
            if name in ("claude", "claude.exe"):
                return p.pid
            if name in ("node", "node.exe") and any(a.replace("\\", "/").endswith(("/claude", "claude-code/cli.js")) for a in p.cmdline()[1:3]):
                return p.pid
            p = p.parent()
    except Exception:
        pass
    return None


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    event = data.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "")
    body = {"event": event, "pid": _claude_pid(), "session_id": data.get("session_id"),
            "cwd": data.get("cwd"), "tool_name": data.get("tool_name"), "message": data.get("message")}
    if data.get("tool_name") == "AskUserQuestion":
        body["tool_input"] = data.get("tool_input")      # the question and its choices, to answer it from the pad
    try:
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=0.5).close()
    except Exception:
        pass                                             # app closed: nothing to do


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
