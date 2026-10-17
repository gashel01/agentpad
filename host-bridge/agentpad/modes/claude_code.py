import json
import os
import threading
import time
from pathlib import Path

from agentpad.injector import KeyInjector
from agentpad.joystick import JoystickDecoder
from agentpad.led_styles import frame_for_state
from agentpad.modes.common import ActionDispatcher

DEFAULT_STATUS_FILE = "~/.agentpad/status.json"
ENCODER_BUTTON_IDS = (100, 101)


class ClaudeCodeMode:
    name = "claude_code"

    def __init__(self, config: dict, num_leds: int):
        cc_cfg = config.get("claude_code", {})
        self.dispatcher = ActionDispatcher(cc_cfg, KeyInjector(config.get("target_apps")))
        self.joystick = JoystickDecoder(
            self.dispatcher,
            deadzone=cc_cfg.get("joystick", {}).get("deadzone", 650),
            invert_x=cc_cfg.get("joystick", {}).get("invert_x", False),
            invert_y=cc_cfg.get("joystick", {}).get("invert_y", False),
        )
        self.num_leds = num_leds
        self.status_path = Path(os.path.expanduser(cc_cfg.get("status_file", DEFAULT_STATUS_FILE)))
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = "idle"
        self._mtime = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def _watch_loop(self):
        while not self._stop.is_set():
            try:
                mtime = self.status_path.stat().st_mtime
                if mtime != self._mtime:
                    self._mtime = mtime
                    data = json.loads(self.status_path.read_text(encoding="utf-8"))
                    self.state = data.get("state", "idle")
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
            time.sleep(0.15)

    def stop(self):
        self._stop.set()

    def on_key(self, key_id: int, down: bool):
        if key_id in ENCODER_BUTTON_IDS:
            self.dispatcher.on_enc_button(key_id, down)
        else:
            self.dispatcher.on_key(key_id, down)

    def on_enc(self, enc_id: int, delta: int):
        self.dispatcher.on_enc(enc_id, delta)

    def on_joy(self, x: int, y: int):
        self.joystick.on_raw(x, y)

    def get_frame(self):
        color = frame_for_state(self.state, time.time())
        return [color] * self.num_leds
