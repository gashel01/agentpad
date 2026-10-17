import subprocess
import threading
import time
from typing import Callable, Optional

from agentpad.injector import KeyInjector


class ActionDispatcher:
    """Translates device events (keys/encoders) into OS keyboard actions
    from a config dict shared by both modes.

    Config format (see config.example.yaml):
      keys:
        "<id>": {action: "combo"|"text"|"macro"|"agent", value: ...}
      encoders:
        "<id>":
          cw:  {action: ..., value: ...}
          ccw: {action: ..., value: ...}
          press: {action: ..., value: ...}

    "macro": value is a list of steps [{type, value, delay_after_ms}],
    type in "key"|"text"|"wait"|"exec" -- run in a separate thread
    so it never blocks the serial read loop (a macro can contain
    multi-second "wait" steps).
    "agent": value is the slot id passed to `agent_cb` (launching/relaunching
    a session tracked by AgentSessionManager).
    """

    def __init__(self, config: dict, injector: KeyInjector, agent_cb: Optional[Callable[[str], None]] = None):
        self.keys_cfg = {str(k): v for k, v in (config.get("keys") or {}).items()}
        self.enc_cfg = {str(k): v for k, v in (config.get("encoders") or {}).items()}
        self.injector = injector
        self.agent_cb = agent_cb

    def _run(self, action_cfg: dict | None):
        if not action_cfg:
            return
        kind = action_cfg.get("action")
        value = action_cfg.get("value", "")
        if kind == "combo":
            self.injector.send_combo(value)
        elif kind == "text":
            self.injector.send_text(value)
        elif kind == "macro":
            steps = value if isinstance(value, list) else []
            threading.Thread(target=self._run_macro, args=(steps,), daemon=True).start()
        elif kind == "agent" and self.agent_cb:
            try:
                self.agent_cb(str(value))
            except Exception:
                pass  # a session that refuses to (re)launch must not crash the dispatch

    def _run_macro(self, steps: list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            stype = step.get("type")
            value = step.get("value", "")
            try:
                if stype == "key":
                    self.injector.send_combo(value)
                elif stype == "text":
                    self.injector.send_text(value)
                elif stype == "wait":
                    time.sleep(max(0.0, float(value)) / 1000)
                elif stype == "exec":
                    subprocess.Popen(value, shell=True)
            except Exception:
                pass  # a failed step must not interrupt the rest of the macro
            delay = step.get("delay_after_ms")
            if delay:
                try:
                    time.sleep(max(0.0, float(delay)) / 1000)
                except (TypeError, ValueError):
                    pass

    def on_key(self, key_id: int, down: bool):
        if not down:
            return  # fires on press, not on release
        self._run(self.keys_cfg.get(str(key_id)))

    def on_enc(self, enc_id: int, delta: int):
        cfg = self.enc_cfg.get(str(enc_id))
        if not cfg:
            return
        self._run(cfg.get("cw") if delta > 0 else cfg.get("ccw"))

    def on_enc_button(self, enc_key_id: int, down: bool):
        if not down:
            return
        enc_id = str(enc_key_id - 100)
        cfg = self.enc_cfg.get(enc_id)
        if cfg:
            self._run(cfg.get("press"))
