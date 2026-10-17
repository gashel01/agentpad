"""Installs / removes the agentpad hooks in Claude Code's settings (~/.claude/settings.json).

  agentpad-hooks install     adds the hooks (backs up the file first)
  agentpad-hooks uninstall   removes only the agentpad hooks
  agentpad-hooks status      says whether they are installed

Other hooks and settings are never touched.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
MARK = "agentpad.hooks.agentpad_hook"
# tracked events; tool events take a "matcher" (empty = all tools)
EVENTS = {"UserPromptSubmit": None, "PreToolUse": "", "PostToolUse": "", "Notification": None,
          "Stop": None, "SessionEnd": None}


def command() -> str:
    return f'"{sys.executable}" -m {MARK}'


def _load() -> dict:
    if not SETTINGS.exists():
        return {}
    return json.loads(SETTINGS.read_text(encoding="utf-8") or "{}")


def _save(cfg: dict):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        shutil.copy2(SETTINGS, SETTINGS.with_name(f"settings.json.avant_agentpad_{time.strftime('%Y%m%d_%H%M%S')}"))
    SETTINGS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _ours(entry: dict) -> bool:
    return any(MARK in (h.get("command") or "") for h in entry.get("hooks") or [])


def installed() -> bool:
    try:
        hooks = _load().get("hooks") or {}
    except Exception:
        return False
    return all(any(_ours(e) for e in hooks.get(ev) or []) for ev in EVENTS)


def install() -> str:
    cfg = _load()
    hooks = cfg.setdefault("hooks", {})
    for ev, matcher in EVENTS.items():
        lst = [e for e in hooks.get(ev) or [] if not _ours(e)]     # replaces an older version
        entry = {"hooks": [{"type": "command", "command": command(), "timeout": 5}]}
        if matcher is not None:
            entry = {"matcher": matcher, **entry}
        hooks[ev] = lst + [entry]
    _save(cfg)
    return f"agentpad hooks installed in {SETTINGS}"


def uninstall() -> str:
    cfg = _load()
    hooks = cfg.get("hooks") or {}
    for ev in list(hooks):
        lst = [e for e in hooks[ev] or [] if not _ours(e)]
        if lst:
            hooks[ev] = lst
        else:
            hooks.pop(ev)
    if not hooks:
        cfg.pop("hooks", None)
    _save(cfg)
    return "agentpad hooks removed"


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "install":
        print(install())
    elif action == "uninstall":
        print(uninstall())
    else:
        print("installed" if installed() else "not installed")


if __name__ == "__main__":
    main()
