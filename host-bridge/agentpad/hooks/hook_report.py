"""Called by Claude Code hooks (settings.json) to report a
state change to the agentpad bridge. Usage: python hook_report.py <state>

States expected by agentpad/led_styles.py:
  idle | thinking | running_tool | waiting_input | needs_attention | done | error
"""
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_STATUS_FILE = "~/.agentpad/status.json"


def main():
    if len(sys.argv) < 2:
        print("usage: hook_report.py <state>", file=sys.stderr)
        sys.exit(1)
    state = sys.argv[1]
    path = Path(os.path.expanduser(os.environ.get("AGENTPAD_STATUS_FILE", DEFAULT_STATUS_FILE)))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "ts": time.time()}), encoding="utf-8")
    tmp.replace(path)


if __name__ == "__main__":
    main()
