"""Local web interface for agentpad (cross-platform: runs in the
browser, no native GUI toolkit). Reuses SerialLink/
ActionDispatcher/JoystickDecoder/KeyInjector directly from the CLI bridge -- same
logic, just driven by a web page instead of the terminal.

Runs a local server (FastAPI + WebSocket) that:
- lists/opens the pad's serial port
- relays each event (key/encoder/joystick) live to the
  browser via WebSocket, AND triggers the actual configured keyboard/macro/
  agent action (it's a functional bridge, not just an editor)
- exposes settings (keyboard/macro/agent bindings + colors + LED animation
  preset per profile) for reading/writing, with saving to
  config.yaml
- runs ONE continuous LED loop (Animator, ~12 fps) once
  connected: it recomputes the frame on every tick from the configured
  colors, the active animation preset, one-off previews (a slider
  being dragged) and the status of tracked agent sessions -- no
  need to "push" a frame by hand on every change, the next
  tick (< 100ms) picks it up automatically. Continuous serial writing is
  safe on the write-timeout side (see serial_link.py/led_styles.py).
- tracks agent sessions (AgentSessionManager): launch/list/kill/
  bring to front, with an optional small floating widget
  (webui/widget.py, drawn with PIL: see radial_native.py).
"""
from __future__ import annotations

import copy
import json
import re
import math
import os
import sys
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import uvicorn
import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from serial.tools import list_ports

from agentpad.agent_sessions import AgentSessionManager
from agentpad.injector import KeyInjector
from agentpad.joystick import JoystickDecoder
from agentpad import osfront
from agentpad.osfront import IS_MAC, HAS_SCREEN
from agentpad.led_styles import (AGENT_STATUS_TO_STATE, STATE_ANIMS, Animator, apply_animation, dominant_state,
                                 frame_for_state, halo_state_defaults, limit_power, render_state, state_defaults)
from agentpad.modes.common import ActionDispatcher
from agentpad.serial_link import SerialLink, find_port

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_CONFIG_PATH = Path.cwd() / "config.yaml"

PROFILES = ("claude_code", "macropad")   # historical profiles; macropad profiles are now a free-form list
AGENTS_PROFILE = "claude_code"
DEFAULT_MACRO_PROFILES = {"macropad": {"name": "General", "apps": []}}
DEFAULT_COLOR = [20, 20, 20, 0]
DEFAULT_ANIMATION = {"name": "none", "params": {}, "zone": "all"}

# keyId (switch matrix, what the firmware sends on a press and what
# the "keys" bindings use) -> ledId (SK6812 chain, what the "colors"
# config and LED frame use). The two are wired separately and
# don't line up (except 0 and 11) -- confirmed by two direct tests on
# the real pad. Must stay in sync with KEY_TO_LED in static/app.js.
KEY_TO_LED = {0: 0, 3: 1, 6: 2, 9: 3, 1: 7, 4: 6, 7: 5, 10: 4, 2: 8, 5: 9, 8: 10, 11: 11}

# The top 8 keys (back row + middle row), in physical left->right
# order, reserved for agent mode: not configurable via normal
# bindings, they directly drive the windows of detected Claude Code
# sessions. The front row (8,9,10,11) stays
# available for normal bindings.
AGENT_KEY_SLOTS = [0, 3, 6, 9, 1, 4, 7, 10]
# Physical layout, calibrated from the software (the "calibration" config):
#  - key positions "r{row}c{col}", row 0 = back (knob/joystick side), col 0 = left;
#  - halo positions "h0".."h11" clockwise as seen from above: back left->right (h0-h2),
#    right side back->front (h3-h5), front right->left (h6-h8), left side front->back (h9-h11).
# Defaults = the current wiring, confirmed by direct tests (cf. KEY_TO_LED above).
DEFAULT_KEY_LAYOUT = {"r0c0": 0, "r0c1": 3, "r0c2": 6, "r0c3": 9, "r1c0": 1, "r1c1": 4, "r1c2": 7, "r1c3": 10,
                      "r2c0": 2, "r2c1": 5, "r2c2": 8, "r2c3": 11}
DEFAULT_LED_LAYOUT = {0: "r0c0", 1: "r0c1", 2: "r0c2", 3: "r0c3", 7: "r1c0", 6: "r1c1", 5: "r1c2", 4: "r1c3",
                      8: "r2c0", 9: "r2c1", 10: "r2c2", 11: "r2c3",
                      12: "h3", 13: "h4", 14: "h5", 15: "h6", 16: "h7", 17: "h8",
                      18: "h9", 19: "h10", 20: "h11", 21: "h0", 22: "h1", 23: "h2"}
# Bottom row in agent mode: bolt, confirm, cancel, mic. Default colors (by
# position, so correct regardless of calibration); a manually set color takes priority, and
# "restore" removes it.
NUM_LEDS_DEFAULT = 24   # firmware: 12 keys + 12 halo LEDs (config.h, NUM_LEDS)
# joystick up/down in Agents mode: scrolls in the foreground session (a terminal
# shortcut, not a key sent to Claude). Windows Terminal: Ctrl+Shift+arrow; Mac: Cmd+arrow.
SCROLL_KEYS = ("cmd+up", "cmd+down") if IS_MAC else ("ctrl+shift+up", "ctrl+shift+down")
RADIAL_ON, RADIAL_DIM = [40, 110, 255, 0], [3, 6, 18, 0]   # halo compass, radial menu open
FRONT_KEY_DEFAULTS = {"r2c0": [255, 160, 0, 0], "r2c1": [0, 200, 40, 0], "r2c2": [255, 0, 0, 0], "r2c3": [0, 90, 255, 0]}
# Default actions for the bottom row in agent mode (by position): a binding set in
# the config takes priority; removing it reverts to this default. "mic" = hold for Claude Code dictation.
FRONT_KEY_ACTIONS = {
    "r2c0": {"action": "combo", "value": "shift+tab"},   # bolt: switches mode (auto-accept / plan)
    "r2c1": {"action": "combo", "value": "enter"},       # confirm
    "r2c2": {"action": "combo", "value": "esc"},         # cancel
    "r2c3": {"action": "mic", "value": ""},              # mic: hold to dictate (Claude Code dictation)
}
# Knob in agent mode: rotation = volume, double-click = show/hide session
# windows, free single click (nothing by default). "sessions" is an action specific to the software.
# "auto": Claude Code session in foreground -> arrows (browse multiple choices, Confirm
# = Enter); otherwise -> volume.
ENCODER_DEFAULTS = {
    "cw": {"action": "auto", "value": ""},
    "ccw": {"action": "auto", "value": ""},
    "press": {"action": "none", "value": ""},
    "double": {"action": "sessions", "value": ""},
}
FOCUS_DIM = 0.12       # a session in the foreground: its agent key stays full, the others drop to 12%
MIC_PENDING_S = 90.0   # after a dictation: Confirm sends, Cancel clears, during this window
# Claude Code dictation (/voice, hold mode): it is triggered by HOLDING Space and detects
# that hold from the key's fast repetition. A simulated press does not auto-repeat on its own:
# so Space is resent at the rate of a keyboard auto-repeat as long as the mic is held.
MIC_KEY = "space"
MIC_REPEAT_S = 0.022   # ~30 actual presses/s (the Windows timer rounds up)
KNOB_DOUBLE_S = 0.35   # double-click window; a single click only waits if a double-click is configured
AGENT_ENCODER_BTN_ID = 100
AGENT_JOYSTICK_BTN_ID = 110  # reserved for the floating widget (see webui/widget.py)


def load_config(path: Path) -> dict:
    if not path.exists():
        example = path.parent / "config.example.yaml"
        if example.exists():
            path = example
        else:
            return {"num_leds": NUM_LEDS_DEFAULT, "power_budget_ma": 420, "agents": {}, "claude_code": {}, "macropad": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Hub:
    """Shared server state: serial connection, active config, subscribed
    WebSocket clients, tracked agent sessions. A single instance for
    the whole process."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.state_path = config_path.parent / ".agentpad_state.json"
        self.profile = self._load_last_profile()
        self.link: Optional[SerialLink] = None
        self.device_info: dict = {}
        self.dispatcher: Optional[ActionDispatcher] = None
        self.joystick: Optional[JoystickDecoder] = None
        self.injector = KeyInjector(self.config.get("target_apps"))
        self.ws_clients: list[WebSocket] = []
        self.loop = None  # asyncio event loop, set when the server starts
        self.agents = AgentSessionManager()
        self.agents.terminal_app = self.config.get("terminal_app", "Terminal")
        self._auto_sessions: list[dict] = []  # latest scan_system(), sorted by pid
        # agent key of each session (pid -> key id), kept as long as the session lives:
        # closing a session no longer shifts the others; a session launched from an empty key
        # takes that key (_pending_key), a session opened by hand takes the 1st free key
        self._pid_key: dict[int, int] = {}
        self._pending_key: Optional[tuple[int, float]] = None
        self._key_sessions: list = []          # aligned with agent_key_slots: session or None
        self._session_hwnds: list = []         # window of each session, same order (for focus)
        self._attention_pids: set = set()       # sessions with a menu / permission prompt on screen
        # latest Claude Code hook events, by pid: {"status", "menu", "t"} (see on_hook)
        self._hooks: dict = {}
        # "Finished": a session goes from Working to idle while you're looking elsewhere;
        # it stays lit until you bring that session to the foreground
        self._finished_pids: set = set()
        self._run_seen: dict = {}               # pid -> time of the last "running" not yet seen finished
        self._mic: Optional[dict] = None          # dictation in progress: {key, hwnd, since}
        self._scroll = 0.0                        # joystick: -1..1 down / up (0 = nothing)
        self._scroll_thread: Optional[threading.Thread] = None
        self._mic_pending: Optional[dict] = None  # dictation finished, waiting for Confirm/Cancel
        self._knob_timer: Optional[threading.Timer] = None
        self._knob_last = 0.0
        self._launch_cooldown = 0.0   # empty agent key: no two sessions from a double press
        self.widget_pointer = None   # radial menu: direction of the pointed item (degrees, 0 = up, clockwise)
        self.widget_visible = False  # the floating widget reports its state (see /api/widget/visible) --
        # while it's open, the encoder button is used to switch its mode, not to tile windows
        self._overrides: dict[int, tuple[list, float]] = {}
        self._state_preview: dict[str, tuple[dict, float]] = {}   # group ("keys"/"halo") -> state preview
        self._animator: Optional[Animator] = None
        self._agents_stop = threading.Event()
        # calibration view open: presses trigger no action. Renewed by the page
        # every ~8 s: if it's closed mid-calibration, the pad comes back to life on its own after 20 s.
        self.calibrating_until = 0.0
        self.injector.policy = self._may_inject
        self._apply_layout()
        self._load_agent_slots()
        self._rebuild_dispatcher()

    # ---- config / profiles -------------------------------------------------
    def _load_last_profile(self) -> str:
        """The active profile must survive a server restart: without
        this, a restart silently falls back to "claude_code" by
        default, with its own keyboard bindings -- has happened before, and
        dangerous if the user still thinks they're on another profile.
        Stored separately (not in config.yaml) so switching profiles never
        persists unsaved settings."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            profile = data.get("last_profile")
            if profile in self.profile_ids():
                return profile
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return "claude_code"

    def _save_last_profile(self):
        try:
            self.state_path.write_text(json.dumps({"last_profile": self.profile}), encoding="utf-8")
        except OSError:
            pass

    def _load_agent_slots(self):
        for slot_id, cfg in (self.config.get("agents") or {}).items():
            cfg = cfg or {}
            self.agents.configure(str(slot_id), cfg.get("name", ""), cfg.get("cwd", ""), cfg.get("command", "claude"))

    @property
    def calibrating(self) -> bool:
        return time.time() < self.calibrating_until

    # ---- physical layout (calibration) ---------------------------------
    def get_layout(self) -> dict:
        cal = self.config.get("calibration") or {}
        keys = dict(DEFAULT_KEY_LAYOUT)
        keys.update({str(k): int(v) for k, v in (cal.get("keys") or {}).items()})
        leds_cfg = cal.get("leds")
        leds = {int(k): str(v) for k, v in leds_cfg.items()} if leds_cfg else dict(DEFAULT_LED_LAYOUT)
        return {"keys": keys, "leds": {str(k): v for k, v in sorted(leds.items())},
                "joystick": dict(cal.get("joystick") or {})}

    def _apply_layout(self):
        lay = self.get_layout()
        pos_to_led = {pos: int(led) for led, pos in lay["leds"].items()}
        self.pos_to_led = pos_to_led
        self.pos_to_key = {pos: int(kid) for pos, kid in lay["keys"].items()}
        self.halo_order = [pos_to_led[f"h{i}"] for i in range(12) if f"h{i}" in pos_to_led]   # clockwise
        self.key_to_led = {kid: pos_to_led[pos] for pos, kid in lay["keys"].items() if pos in pos_to_led}
        # the 8 agent keys = back row then middle row, left to right
        self.agent_key_slots = [lay["keys"][f"r{r}c{c}"] for r in (0, 1) for c in range(4) if f"r{r}c{c}" in lay["keys"]]
        self.halo_leds = sorted(int(led) for led, pos in lay["leds"].items() if pos.startswith("h"))

    def set_calibration(self, keys=None, leds=None, joystick=None) -> dict:
        cal = self.config.setdefault("calibration", {})
        if keys is not None:
            cal["keys"] = {str(k): int(v) for k, v in keys.items()}
        if leds is not None:
            cal["leds"] = {int(k): str(v) for k, v in leds.items()}
        if joystick is not None:
            cal["joystick"] = joystick
        self._apply_layout()
        self._rebuild_dispatcher()
        return self.get_layout()

    def solo_led(self, idx: int, color: list[int], ttl: float = 1.2):
        """LED calibration: a single LED lit, all others off."""
        num_leds = self.config.get("num_leds", NUM_LEDS_DEFAULT)
        expire = time.time() + ttl
        for i in range(num_leds):
            self._overrides[i] = (list(color) if i == idx else [0, 0, 0, 0], expire)

    def _rebuild_dispatcher(self):
        cfg = dict(self.config.get(self.profile, {}) or {})
        cfg["keys"] = {**self.key_action_defaults(), **{str(k): v for k, v in (cfg.get("keys") or {}).items()}}
        encs = {str(k): dict(v or {}) for k, v in (cfg.get("encoders") or {}).items()}
        encs["0"] = {**self.encoder_defaults(), **encs.get("0", {})}
        cfg["encoders"] = encs
        self.dispatcher = ActionDispatcher(cfg, self.injector, agent_cb=self.agents.launch)
        jcfg = dict(cfg.get("joystick", {}) or {})
        jcfg.update((self.config.get("calibration") or {}).get("joystick") or {})   # calibration takes priority
        self.joystick = JoystickDecoder(
            self.dispatcher,
            deadzone=jcfg.get("deadzone", 650),
            invert_x=jcfg.get("invert_x", False),
            invert_y=jcfg.get("invert_y", False),
            center=jcfg.get("center"),
        )

    # ---- profiles: Agents + a free-form list of macropad profiles -----------------------
    # Each profile keeps its own config (config[<id>]: keys, colors, animation...);
    # config["profiles"] gives the name and the applications that activate it automatically.
    def macro_profiles(self) -> dict:
        return self.config.get("profiles") or {k: dict(v) for k, v in DEFAULT_MACRO_PROFILES.items()}

    def profile_ids(self) -> list:
        return [AGENTS_PROFILE] + list(self.macro_profiles().keys())

    def get_profiles(self) -> list:
        out = [{"id": AGENTS_PROFILE, "name": "Agents", "kind": "agents", "apps": []}]
        for pid, meta in self.macro_profiles().items():
            out.append({"id": pid, "name": meta.get("name") or pid, "kind": "macropad", "apps": list(meta.get("apps") or [])})
        return out

    def create_profile(self, name: str) -> str:
        import re as _re
        profs = self.config.setdefault("profiles", self.macro_profiles())
        base = "p-" + (_re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profil")
        pid, n = base, 2
        while pid in profs or pid == AGENTS_PROFILE:
            pid, n = f"{base}-{n}", n + 1
        profs[pid] = {"name": name.strip() or "Profile", "apps": []}
        # each profile has its own halo color: you can see at a glance which profile you're in
        palette = [[180, 60, 255, 0], [255, 120, 0, 0], [0, 200, 180, 0], [255, 40, 120, 0], [120, 220, 0, 0], [60, 120, 255, 0]]
        ring = palette[(len(profs) - 1) % len(palette)]
        self.config[pid] = {"colors": {"default": [20, 20, 20, 0], "ring": ring}, "keys": {}, "encoders": {}}
        return pid

    def update_profile(self, pid: str, name: Optional[str] = None, apps: Optional[list] = None):
        profs = self.config.setdefault("profiles", self.macro_profiles())
        if pid not in profs:
            raise ValueError(f"unknown profile: {pid}")
        if name is not None and name.strip():
            profs[pid]["name"] = name.strip()
        if apps is not None:
            profs[pid]["apps"] = sorted({a.strip() for a in apps if a and a.strip()}, key=str.lower)

    def delete_profile(self, pid: str):
        profs = self.config.setdefault("profiles", self.macro_profiles())
        if pid not in profs or len(profs) <= 1:
            raise ValueError("keep at least one macro profile")
        del profs[pid]
        self.config.pop(pid, None)
        if self.profile == pid:
            self.set_profile(AGENTS_PROFILE)

    def get_auto_profile(self) -> bool:
        return bool(self.config.get("auto_profile", True))

    def set_auto_profile(self, on: bool):
        self.config["auto_profile"] = bool(on)

    def set_profile(self, profile: str):
        if profile not in self.profile_ids():
            raise ValueError(f"unknown profile: {profile}")
        changed = profile != self.profile
        self.profile = profile
        self._save_last_profile()
        self._rebuild_dispatcher()
        if changed:
            self._broadcast({"t": "profile", "profile": profile})

    # automatic switching: on every foreground window change, a Claude Code
    # session -> Agents, a linked application -> its profile; elsewhere nothing is touched
    def start_auto_profile(self):
        threading.Thread(target=self._auto_profile_loop, daemon=True).start()

    def _auto_profile_loop(self):
        last = None
        while not self._agents_stop.is_set():
            time.sleep(0.4)
            try:
                fg = osfront.foreground()
                if fg == last or not self.get_auto_profile():
                    last = fg
                    continue
                last = fg
                target = None
                if fg in self._session_hwnds:
                    target = AGENTS_PROFILE
                else:
                    exe = osfront.foreground_process_name() or ""
                    for prof_id, meta in self.macro_profiles().items():
                        if exe in [a.lower() for a in meta.get("apps") or []]:
                            target = prof_id
                            break
                if target and target != self.profile:
                    self.set_profile(target)
            except Exception:
                pass

    def running_apps(self) -> list:
        """Applications with a visible window (to link a profile to an application)."""
        if IS_MAC:
            from agentpad import macos
            return macos.visible_apps()
        try:
            import psutil
            import win32gui
            import win32process
        except ImportError:
            return []
        seen = {}

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                try:
                    _, pid = win32process.GetWindowThreadProcessId(h)
                    exe = psutil.Process(pid).name()
                    seen.setdefault(exe, win32gui.GetWindowText(h)[:60])
                except Exception:
                    pass
            return True
        win32gui.EnumWindows(cb, None)
        skip = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe"}
        return [{"exe": e, "title": t} for e, t in sorted(seen.items(), key=lambda x: x[0].lower()) if e.lower() not in skip]

    def _may_inject(self) -> bool:
        """Automatic safeguard (no more "allowed applications" list to fill in)."""
        if self.profile != AGENTS_PROFILE:
            return True
        try:
            return osfront.foreground() in self._session_hwnds
        except Exception:
            return False

    def get_bindings(self) -> dict:
        cfg = self.config.get(self.profile, {}) or {}
        return {
            "keys": cfg.get("keys", {}),
            "encoders": cfg.get("encoders", {}),
            "colors": cfg.get("colors", {}),
        }

    def get_animation(self) -> dict:
        cfg = self.config.get(self.profile, {}) or {}
        anim = cfg.get("animation") or DEFAULT_ANIMATION
        return {**DEFAULT_ANIMATION, **anim}

    def set_animation(self, name: str, params: dict, zone: str):
        cfg = self.config.setdefault(self.profile, {})
        cfg["animation"] = {"name": name, "params": params or {}, "zone": zone or "all"}

    # ---- agent states: color + animation per state, with a factory default ----
    STATE_GROUPS = {"keys": ("states", state_defaults), "halo": ("halo_states", halo_state_defaults)}

    def get_states(self, group: str = "keys") -> dict:
        cfg_key, defaults = self.STATE_GROUPS[group]
        custom = (self.config.get(self.profile, {}) or {}).get(cfg_key) or {}
        out = defaults()
        for name, spec in custom.items():
            if name in out and isinstance(spec, dict):
                out[name] = {**out[name], **spec}
        return out

    def set_state(self, name: str, color: list[int], anim: str, speed: float, group: str = "keys"):
        cfg_key, defaults = self.STATE_GROUPS[group]
        if name not in defaults():
            raise ValueError(f"unknown state: {name}")
        if anim not in STATE_ANIMS:
            raise ValueError(f"unknown animation: {anim}")
        cfg = self.config.setdefault(self.profile, {})
        cfg.setdefault(cfg_key, {})[name] = {"color": [int(c) for c in color][:4], "anim": anim, "speed": float(speed)}

    def apply_ambiance(self, name: str):
        """Sets all states at once (keys + halo); "defaut" = factory default."""
        from agentpad.led_styles import AMBIANCES
        cfg = self.config.setdefault(self.profile, {})
        if name == "defaut":
            cfg.pop("states", None)
            cfg.pop("halo_states", None)
            return
        amb = AMBIANCES.get(name)
        if not amb:
            raise ValueError(f"unknown ambiance: {name}")
        cfg["states"] = {k: dict(v) for k, v in amb["keys"].items()}
        cfg["halo_states"] = {k: dict(v) for k, v in amb["halo"].items()}

    def current_ambiance(self) -> str:
        """Name of the ambiance that matches the current states exactly, otherwise "perso"."""
        from agentpad.led_styles import AMBIANCES
        ks, hs = self.get_states("keys"), self.get_states("halo")
        if ks == state_defaults() and hs == halo_state_defaults():
            return "defaut"
        for name, amb in AMBIANCES.items():
            if all(ks.get(s) == v for s, v in amb["keys"].items()) and all(hs.get(s) == v for s, v in amb["halo"].items()):
                return name
        return "perso"

    def reset_states(self, name: Optional[str] = None, group: str = "keys"):
        """Reverts to the factory default: one state, or all if `name` is None."""
        cfg_key, _ = self.STATE_GROUPS[group]
        cfg = self.config.get(self.profile) or {}
        states = cfg.get(cfg_key)
        if not states:
            return
        if name is None:
            states.clear()
        else:
            states.pop(name, None)
        if not states:
            cfg.pop(cfg_key, None)

    # Per agent key: only "waiting for input" and "idle" can be set key by
    # key (recognize each agent by its color); the other states stay global.
    PER_KEY_STATES = ("waiting_input", "idle")

    def get_key_states(self) -> dict:
        """{keyId: {state: spec}} -- only the keys that differ from the global setting."""
        raw = (self.config.get(self.profile, {}) or {}).get("key_states") or {}
        return {str(k): {s: dict(v) for s, v in (d or {}).items() if s in self.PER_KEY_STATES} for k, d in raw.items()}

    def set_key_state(self, key_id: str, name: str, color: list[int], anim: str, speed: float):
        if name not in self.PER_KEY_STATES:
            raise ValueError(f"state {name} is global, not per key")
        if anim not in STATE_ANIMS:
            raise ValueError(f"unknown animation: {anim}")
        cfg = self.config.setdefault(self.profile, {})
        cfg.setdefault("key_states", {}).setdefault(str(key_id), {})[name] = {
            "color": [int(c) for c in color][:4], "anim": anim, "speed": float(speed)}

    def reset_key_state(self, key_id: str, name: Optional[str] = None):
        """The key reverts to the global setting (one state, or both)."""
        cfg = self.config.get(self.profile) or {}
        ks = cfg.get("key_states") or {}
        d = ks.get(str(key_id))
        if d is None:
            return
        if name is None:
            d.clear()
        else:
            d.pop(name, None)
        if not d:
            ks.pop(str(key_id), None)
        if not ks:
            cfg.pop("key_states", None)

    def preview_state(self, spec: dict, ttl: float = 3.0, group: str = "keys"):
        self._state_preview[group] = (dict(spec), time.time() + ttl)

    def _active_preview(self, group: str, now: float):
        p = self._state_preview.get(group)
        return p[0] if p and p[1] > now else None

    # halo in agent mode: "agents" = summarizes session state, "fixed" = ring color
    def get_halo_mode(self) -> str:
        return (self.config.get(self.profile, {}) or {}).get("halo_mode", "agents")

    def set_halo_mode(self, mode: str):
        if mode not in ("agents", "fixed"):
            raise ValueError(f"unknown halo mode: {mode}")
        self.config.setdefault(self.profile, {})["halo_mode"] = mode

    # global pad brightness (all profiles), in %
    def get_brightness(self) -> int:
        return int(self.config.get("brightness", 100))

    def set_brightness(self, value: int):
        self.config["brightness"] = max(5, min(100, int(value)))

    # per-zone brightness, agent mode: agent keys (one), halo (one), bottom row (per
    # key). In %, on top of the overall brightness. Absent = 100.
    def get_zone_brightness(self) -> dict:
        z = (self.config.get(self.profile, {}) or {}).get("zone_brightness") or {}
        return {"agents": int(z.get("agents", 100)), "halo": int(z.get("halo", 100)),
                "keys": {str(k): int(v) for k, v in (z.get("keys") or {}).items()}}

    def set_zone_brightness(self, zone: str, value: int, key: Optional[str] = None):
        v = max(5, min(100, int(value)))
        z = self.config.setdefault(self.profile, {}).setdefault("zone_brightness", {})
        if zone in ("agents", "halo"):
            z[zone] = v
        elif zone == "key" and key is not None:
            z.setdefault("keys", {})[str(key)] = v
        else:
            raise ValueError(f"unknown zone: {zone}")

    def set_target_apps(self, apps: list[str]):
        """Safeguard: combo/text/keyboard-macro injects ONLY into
        one of these apps (process name, e.g. WindowsTerminal.exe) -- empty
        = everything blocked, never "allow everything" by default (see injector.py,
        the reasoning for this choice is documented there)."""
        apps = [a.strip() for a in apps if a.strip()]
        self.config["target_apps"] = apps
        self.injector.allowed_processes = [a.lower() for a in apps]

    def set_binding(self, target: str, key_id: str, action: Optional[str], value,
                     cw=None, ccw=None, press=None, double=None):
        cfg = self.config.setdefault(self.profile, {})
        if target == "key":
            keys = cfg.setdefault("keys", {})
            if action:
                keys[key_id] = {"action": action, "value": value if value is not None else ""}
            else:
                keys.pop(key_id, None)
        elif target == "encoder":
            encs = cfg.setdefault("encoders", {})
            entry = encs.setdefault(key_id, {})
            for name, val in (("cw", cw), ("ccw", ccw), ("press", press), ("double", double)):
                if val is not None:
                    if val.get("action"):
                        entry[name] = val
                    else:
                        entry.pop(name, None)
        self._rebuild_dispatcher()

    def encoder_defaults(self) -> dict:
        return {k: dict(v) for k, v in ENCODER_DEFAULTS.items()} if self.profile == "claude_code" else {}

    def key_action_defaults(self) -> dict:
        """{keyId: action} default for the profile (bottom row in agent mode)."""
        if self.profile != "claude_code":
            return {}
        return {str(self.pos_to_key[p]): dict(a) for p, a in FRONT_KEY_ACTIONS.items() if p in self.pos_to_key}

    def key_color_defaults(self) -> dict:
        """{ledId: color} default for the profile (bottom row in agent mode)."""
        if self.profile != "claude_code":
            return {}
        return {str(self.pos_to_led[p]): list(c) for p, c in FRONT_KEY_DEFAULTS.items() if p in self.pos_to_led}

    def reset_color(self, key_id: str):
        colors = (self.config.get(self.profile, {}) or {}).get("colors") or {}
        colors.pop(str(key_id), None)
        try:
            colors.pop(int(key_id), None)
        except ValueError:
            pass

    def set_color(self, key_id: str, color: list[int]):
        cfg = self.config.setdefault(self.profile, {})
        colors = cfg.setdefault("colors", {})
        colors[key_id] = color

    def save(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.config, f, allow_unicode=True, sort_keys=False)

    # ---- autosave + undo ---------------------------------
    # No more "Save" button: every setting change is written on its own to
    # config.yaml (after a short delay, to coalesce a burst), and the previous config is
    # kept so it can be undone. Learned the hard way: lost calibration three times for not clicking it.
    UNDO_MAX, COALESCE_S, SAVE_DELAY_S = 50, 1.5, 0.8

    def record_change(self, before: dict, path: str):
        now = time.time()
        last = getattr(self, "_last_change", None)
        if not (last and last[0] == path and now - last[1] < self.COALESCE_S):
            hist = self.__dict__.setdefault("_undo", [])
            hist.append(before)
            del hist[:-self.UNDO_MAX]
        self._last_change = (path, now)
        self._schedule_save()

    def _schedule_save(self):
        t = getattr(self, "_save_timer", None)
        if t:
            t.cancel()
        self._save_timer = threading.Timer(self.SAVE_DELAY_S, self._autosave_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _autosave_now(self):
        backup = self.config_path.with_name(self.config_path.name + ".avant_autosave")
        if not backup.exists() and self.config_path.exists():
            backup.write_bytes(self.config_path.read_bytes())   # copy of the hand-written file, once
        try:
            self.save()
        except Exception:
            pass

    def undo_depth(self) -> int:
        return len(getattr(self, "_undo", []))

    def undo(self) -> bool:
        hist = getattr(self, "_undo", [])
        if not hist:
            return False
        self.config = hist.pop()
        self._last_change = None
        self.injector.allowed_processes = [a.lower() for a in self.config.get("target_apps", [])]
        if self.profile not in self.profile_ids():     # the creation of the active profile was undone
            self.profile = AGENTS_PROFILE
            self._save_last_profile()
        self._apply_layout()
        self._rebuild_dispatcher()
        self._schedule_save()
        return True

    # ---- LEDs: a frame recomputed on every tick -------------------------
    def current_frame(self) -> list[list[int]]:
        now = time.time()
        num_leds = self.config.get("num_leds", NUM_LEDS_DEFAULT)
        colors_cfg = {str(k): v for k, v in (self.get_bindings().get("colors") or {}).items()}
        default = list(colors_cfg.get("default", DEFAULT_COLOR))
        ring = list(colors_cfg.get("ring", DEFAULT_COLOR))       # the halo never takes the keys' color
        frame = [list(default) for _ in range(num_leds)]
        for i in self.halo_leds:
            if i < num_leds:
                frame[i] = list(ring)
        for k, v in self.key_color_defaults().items():
            if int(k) < num_leds:
                frame[int(k)] = list(v)
        for k, v in colors_cfg.items():
            if k in ("default", "ring"):
                continue
            try:
                idx = int(k)
            except ValueError:
                continue
            if 0 <= idx < num_leds:
                frame[idx] = list(v)

        frame = apply_animation(frame, self.get_animation(), now, num_leds)

        # halo in agent mode: the most important state across all sessions, with its settings
        if self.profile == "claude_code" and self.get_halo_mode() == "agents":
            hp = self._active_preview("halo", now)
            if hp is None:
                dom = dominant_state(AGENT_STATUS_TO_STATE.get(s.get("status", "offline"), "idle") for s in self._auto_sessions)
                hp = self.get_states("halo")[dom]
            px = list(render_state(hp, now))
            for i in self.halo_leds:
                if i < num_leds:
                    frame[i] = px

        # agent status (Agents profile only): takes priority over everything else, on the 8
        # agent keys -- one per detected session, in order; slots without a session
        # stay off. In Macropad, these keys keep their colors like the others.
        agent_slots = self.agent_key_slots if self.profile == "claude_code" else []
        specs = self.get_states()
        key_specs = self.get_key_states()
        preview = self._active_preview("keys", now)
        for i, key_id in enumerate(agent_slots):
            led_idx = self.key_to_led.get(key_id)
            if led_idx is None or not (0 <= led_idx < num_leds):
                continue
            sess = self._key_sessions[i] if i < len(self._key_sessions) else None
            status = sess.get("status", "offline") if sess else "offline"
            kp = self._active_preview(f"key:{key_id}", now)
            if kp or preview:   # preview of a state being adjusted (one key, or all 8)
                frame[led_idx] = list(render_state(kp or preview, now))
                continue
            state_name = AGENT_STATUS_TO_STATE.get(status, "idle")
            own = key_specs.get(str(key_id), {}).get(state_name)
            frame[led_idx] = list(render_state(own, now) if own else frame_for_state(state_name, now, specs))

        if self.profile == "claude_code":
            self._focus_frame(frame, num_leds)
            self._mic_frame(frame, now, num_leds)
        if self.widget_visible:
            self._radial_frame(frame, num_leds)

        # one-off previews (slider being dragged / LED test):
        # take priority over everything, and clear themselves on expiry
        for idx, (color, expire) in list(self._overrides.items()):
            if expire < now:
                del self._overrides[idx]
                continue
            if 0 <= idx < num_leds:
                frame[idx] = list(color)
        scale = [self.get_brightness() / 100.0] * num_leds
        if self.profile == "claude_code":
            zb = self.get_zone_brightness()
            for i in self.halo_leds:
                if i < num_leds:
                    scale[i] *= zb["halo"] / 100.0
            for kid in self.agent_key_slots:
                i = self.key_to_led.get(kid)
                if i is not None and i < num_leds:
                    scale[i] *= zb["agents"] / 100.0
            for kid, v in zb["keys"].items():
                i = self.key_to_led.get(int(kid))
                if i is not None and i < num_leds and int(kid) not in self.agent_key_slots:
                    scale[i] *= v / 100.0
        if any(s < 1 for s in scale):
            frame = [[int(c * s) for c in px] for px, s in zip(frame, scale)]
        return frame

    def preview_led(self, key_id: int, color: list[int], ttl: float = 0.4):
        self._overrides[key_id] = (list(color), time.time() + ttl)

    def preview_led_all(self, color: list[int], ttl: float = 2.5, zone: str = "all"):
        """zone "keys" = keys only, "ring" = halo only: a preview never spills into the other zone."""
        num_leds = self.config.get("num_leds", NUM_LEDS_DEFAULT)
        expire = time.time() + ttl
        halo = set(self.halo_leds)
        for i in range(num_leds):
            if zone == "keys" and i in halo or zone == "ring" and i not in halo:
                continue
            self._overrides[i] = (list(color), expire)

    # ---- connexion serie ------------------------------------------------
    def connect(self, port: Optional[str]):
        if self.link:
            self.disconnect()
        link = SerialLink(port, 115200, on_message=self._on_device_message, on_disconnect=self._on_link_lost)
        link.open()
        self.link = link
        self._animator = Animator(
            send_fn=link.send, get_frame=self.current_frame, fps=12,
            power_budget_ma=self.config.get("power_budget_ma", 420),
        )
        self._animator.start()
        self._broadcast({"t": "conn", "connected": True, "port": self.link.port})

    def disconnect(self):
        if self._animator:
            self._animator.stop()
            self._animator = None
        if self.link:
            self.link.close()
            self.link = None
        self.device_info = {}
        self._broadcast({"t": "conn", "connected": False, "port": None})

    def _on_link_lost(self):
        """Cable unplugged / port disappeared mid-operation (not a
        requested disconnect()) -- we clean up the state so the
        automatic reconnection loop (_reconnect_loop) can retry as soon as
        the port reappears."""
        if self._animator:
            self._animator.stop()
            self._animator = None
        if self.link:
            try:
                self.link.close()        # frees the COM port for reconnection
            except Exception:
                pass
        self.link = None
        self.device_info = {}
        self._broadcast({"t": "conn", "connected": False, "port": None})

    def _on_device_message(self, msg: dict):
        t = msg.get("t")
        if self.calibrating and t in ("key", "enc", "joy"):
            # calibration view: show the event to the software, without triggering an action
            self._broadcast(msg)
            return
        if t == "hello":
            self.device_info = msg
        elif t == "key":
            key_id = msg.get("id", -1)
            down = msg.get("ev") == "down"
            radial = self._radial_command(key_id) if self.widget_visible else None
            if radial:
                # radial menu open: Confirm / Cancel drive it (select, go back a layer) and
                # don't type anything elsewhere; the widget listens for this message on the same websocket stream
                if down:
                    self._broadcast({"t": "radial", "cmd": radial})
            elif self.profile == "claude_code" and key_id in self.agent_key_slots:
                if down:
                    self._on_agent_key(key_id)
                # Agents profile: these 8 keys follow the sessions; in Macropad they are
                # normal keys, configurable like the others
            elif key_id == AGENT_ENCODER_BTN_ID:
                if down and not self.widget_visible:
                    if self.profile == "claude_code":
                        self._on_knob_click()
                    else:
                        self.agents.toggle_all()
                # floating widget open: this click is used to switch
                # mode (agents <-> actions), not to tile windows --
                # the widget listens to the same websocket stream and handles
                # this itself, see widget.py:on_encoder_button_down()
            elif key_id == AGENT_JOYSTICK_BTN_ID:
                pass  # reserved for the floating widget, which listens to the same
                # websocket stream client-side -- no normal keyboard dispatch here
            elif self.profile == "claude_code" and self._mic_key_event(key_id, down):
                pass  # mic held, or Confirm/Cancel right after a dictation
            elif self.profile == "claude_code" and self._session_key(key_id, down):
                pass  # bottom row, session in the foreground: action sent into the session
            else:
                self.dispatcher.on_key(key_id, down)
        elif t == "enc" and self.widget_visible and msg.get("d"):
            self._broadcast({"t": "radial", "cmd": "next" if msg["d"] > 0 else "prev"})
        elif t == "enc":
            if not (self.profile == "claude_code" and self._knob_rotate(msg.get("id", -1), msg.get("d", 0))):
                self.dispatcher.on_enc(msg.get("id", -1), msg.get("d", 0))
        elif t == "joy":
            # Agents profile: the joystick is used for the radial menu (widget.py), it types nothing. Otherwise
            # old direction settings (e.g. "/review", "/debug") used to go into the session.
            if self.profile != AGENTS_PROFILE:
                self.joystick.on_raw(msg.get("x", 0), msg.get("y", 0))
            elif not self.widget_visible:
                self._joy_scroll(msg.get("x", 0), msg.get("y", 0))
            else:
                self._scroll = 0.0
        self._broadcast(msg)

    # ---- agent mode mic: hold to dictate into the foreground session ----
    @staticmethod
    def _is_mic(a: dict) -> bool:
        v = str(a.get("value", "")).replace(" ", "").lower()
        return a.get("action") == "mic" or (a.get("action") == "combo" and v in ("cmd+h", "win+h"))   # cmd+h: old value

    def _radial_command(self, key_id: int) -> Optional[str]:
        """Confirm key (r2c1) -> "ok", Cancel (r2c2) -> "back"; by position, so in
        all profiles regardless of calibration."""
        if key_id == self.pos_to_key.get("r2c1"):
            return "ok"
        if key_id == self.pos_to_key.get("r2c2"):
            return "back"
        return None

    def _key_role(self, key_id: int) -> dict:
        return self.dispatcher.keys_cfg.get(str(key_id)) or {}

    def _mic_key_event(self, key_id: int, down: bool) -> bool:
        """True if the event is captured by the mic flow (so not by the dispatcher)."""
        a = self._key_role(key_id)
        if self._is_mic(a):
            if down:
                self._mic_start(key_id)
            else:
                self._mic_stop()
            return True
        p = self._mic_pending
        if down and p and time.time() < p["until"]:
            v = (a.get("action"), str(a.get("value", "")).lower())
            if v == ("combo", "enter"):
                self._mic_finish("enter")
                return True
            if v == ("combo", "esc"):
                # Claude Code: Ctrl+C cancels the dictation if it's still finalizing, otherwise clears the
                # input; a single press never exits
                self._mic_finish("ctrl+c")
                return True
        return False

    def _mic_start(self, key_id: int):
        hwnd = self.agents.focused_session_window()
        if not hwnd:
            # no session in the foreground: the agent keys blink in error
            self.preview_state(self.get_states()["error"], 1.6, "keys")
            return
        self._mic_pending = None
        mic = {"key": key_id, "hwnd": hwnd, "since": time.time(), "stop": threading.Event()}
        self._mic = mic
        threading.Thread(target=self._mic_hold, args=(mic,), daemon=True).start()

    def _mic_hold(self, mic: dict):
        """Holds Space in the session as long as the mic is held; stops immediately if the
        session leaves the foreground (never types spaces into another window)."""
        try:
            while not mic["stop"].is_set():
                if osfront.foreground() != mic["hwnd"]:
                    self.preview_state(self.get_states()["error"], 1.6, "keys")
                    if self._mic is mic:
                        self._mic = None
                    break
                self.injector.key_down(MIC_KEY)
                mic["stop"].wait(MIC_REPEAT_S)
        finally:
            self.injector.key_up(MIC_KEY)          # release: Claude Code finalizes the transcription

    def _mic_stop(self):
        mic, self._mic = self._mic, None
        if not mic:
            return
        mic["stop"].set()
        self._mic_pending = {"hwnd": mic["hwnd"], "until": time.time() + MIC_PENDING_S}

    def _mic_finish(self, combo: str):
        p, self._mic_pending = self._mic_pending, None
        if p and self.agents.focused_session_window() == p["hwnd"]:
            self.injector.send_combo(combo, verified=True)
        else:   # the session is no longer in the foreground: nothing is sent elsewhere
            self.preview_state(self.get_states()["error"], 1.6, "keys")

    def _joy_scroll(self, x: int, y: int):
        """Joystick up/down, Agents mode: tracks the vertical deflection; a thread scrolls as long
        as it lasts, faster the further the stick is pushed. Left/right: nothing."""
        j = self.joystick
        if j.center is None:
            j.center = (x, y)                    # no calibration: the 1st position is rest
            return
        dx, dy = x - j.center[0], y - j.center[1]
        if j.invert_x:
            dx = -dx
        if j.invert_y:
            dy = -dy
        if abs(dy) < j.deadzone or abs(dx) > abs(dy):
            self._scroll = 0.0
            return
        mag = min(1.0, (abs(dy) - j.deadzone) / max(1.0, 2048 - j.deadzone))
        self._scroll = mag if dy > 0 else -mag
        if self._scroll_thread is None or not self._scroll_thread.is_alive():
            self._scroll_thread = threading.Thread(target=self._scroll_loop, daemon=True)
            self._scroll_thread.start()

    def _scroll_loop(self):
        while self._scroll and self.profile == AGENTS_PROFILE and not self.widget_visible:
            if osfront.foreground() in self._session_hwnds:     # a session in the foreground, verified
                # direction checked on the pad: the raw stick value rises when pulled toward you
                self.injector.send_combo(SCROLL_KEYS[1] if self._scroll > 0 else SCROLL_KEYS[0], verified=True)
            time.sleep(0.16 - 0.13 * abs(self._scroll))         # from ~6 to ~30 lines per second
        self._scroll = 0.0

    def _radial_frame(self, frame: list, num_leds: int):
        """Radial menu open: the halo acts as a compass (it lights up in the direction of the
        pointed item, like the on-screen menu); Confirm and Cancel light up green and red."""
        n = len(self.halo_order)
        for k, i in enumerate(self.halo_order):
            if i >= num_leds:
                continue
            f = 0.0
            if self.widget_pointer is not None and n:
                a = (k - 1) * 360 / n                  # h1 = middle of the back = top of the menu; clockwise
                d = abs((a - self.widget_pointer + 180) % 360 - 180)
                f = max(0.0, 1 - d / 45)
            frame[i] = [round(RADIAL_DIM[c] + (RADIAL_ON[c] - RADIAL_DIM[c]) * f) for c in range(4)]
        for pos, col in (("r2c1", [0, 200, 40, 0]), ("r2c2", [255, 0, 0, 0])):
            led = self.pos_to_led.get(pos)
            if led is not None and led < num_leds:
                frame[led] = list(col)

    def _focus_frame(self, frame: list, num_leds: int):
        """Spotlight: the agent key of the foreground session keeps its full
        intensity, the others dim -- you can see at a glance which one has focus."""
        try:
            fg = osfront.foreground()
        except Exception:
            return
        hw = self._session_hwnds
        if not fg or fg not in hw:
            return
        focused_pid = self._auto_sessions[hw.index(fg)].get("pid") if hw.index(fg) < len(self._auto_sessions) else None
        for i, key_id in enumerate(self.agent_key_slots):
            led = self.key_to_led.get(key_id)
            sess = self._key_sessions[i] if i < len(self._key_sessions) else None
            urgent = bool(sess) and sess.get("status") in ("attention", "error", "finished")
            mine = bool(sess) and sess.get("pid") == focused_pid
            if not mine and not urgent and led is not None and led < num_leds:   # a pending request stays clearly visible
                frame[led] = [int(c * FOCUS_DIM) for c in frame[led]]

    def _mic_frame(self, frame: list, now: float, num_leds: int):
        """Dictation in progress: the mic key pulses and a glow rotates in the halo."""
        mic = self._mic
        if not mic:
            return
        led = self.key_to_led.get(mic["key"])
        col = list(frame[led]) if led is not None and led < num_leds else [0, 0, 0, 0]
        if not any(col):
            col = [0, 0, 0, 200]
        t = now - mic["since"]
        if led is not None and led < num_leds:
            k = 0.3 + 0.7 * (math.sin(2 * math.pi * t / 0.9) + 1) / 2
            frame[led] = [int(c * k) for c in col]
        n = len(self.halo_order)
        for j, i in enumerate(self.halo_order):
            if i < num_leds:
                d = (j / n - t * 0.7) % 1.0               # comet head, clockwise
                k = max(0.0, 1.0 - d * 3.0) ** 2 + 0.08
                frame[i] = [int(c * min(1.0, k)) for c in col]

    # ---- bottom row when a session is in the foreground ------------------------
    _MENU_ITEM = re.compile(r"^\s*([>❯›])?\s*(\d+)\.\s+(.*)$")

    def _focused_session_pid(self) -> Optional[int]:
        try:
            fg = osfront.foreground()
        except Exception:
            return None
        hw = self._session_hwnds
        if fg and fg in hw:
            i = hw.index(fg)
            if i < len(self._auto_sessions):
                return self._auto_sessions[i].get("pid")
        return None

    def _session_key(self, key_id: int, down: bool) -> bool:
        """Verified session window: the action goes straight into it, without going through the
        list of allowed applications (otherwise Confirm was blocked). Cancel becomes contextual."""
        if not down:
            return False
        pid = self._focused_session_pid()
        if pid is None:
            return False                               # elsewhere: normal path, with safeguard
        a = self._key_role(key_id)
        act, val = a.get("action"), str(a.get("value", ""))
        if act == "combo" and val.lower() == "esc":
            threading.Thread(target=self._smart_cancel, args=(pid,), daemon=True).start()
            return True
        if act == "combo":
            self.injector.send_combo(val, verified=True)
            return True
        if act == "text":
            self.injector.send_text(val, verified=True)
            return True
        return act == "none"

    def _smart_cancel(self, pid: int):
        """Cancel depending on what's on the session's screen:
        - choice menu: text typed into "Type something" -> clear it; otherwise go to
          "Chat about this" and confirm; no "Chat about this" -> Escape;
        - prompt: something written -> clear it (Ctrl+C); empty -> Escape."""
        lines = self.agents.session_screen(pid)
        send = lambda c: self.injector.send_combo(c, verified=True)
        if any("to navigate" in l and "Enter to select" in l for l in lines):
            items, sel = {}, None
            for l in lines:
                m = self._MENU_ITEM.match(l)
                if m:
                    n = int(m.group(2))
                    items[n] = m.group(3).strip()
                    if m.group(1):
                        sel = n
            chat = next((n for n, t in items.items() if t.lower().startswith("chat about this")), None)
            if sel is not None and chat is not None and sel == chat - 1 and items[sel] and not items[sel].lower().startswith("type something"):
                for _ in range(len(items[sel]) + 2):   # free text typed into the menu: clear it
                    send("backspace")
                return
            if chat is not None and sel is not None:
                for _ in range(abs(chat - sel)):
                    send("down" if chat > sel else "up")
                send("enter")
                return
            send("esc")
            return
        prompt = ""
        for i, l in enumerate(lines):
            if l.startswith(">") and i > 0 and lines[i - 1].lstrip().startswith("─"):
                prompt = l[1:].strip()
        if prompt.startswith('Try "'):
            prompt = ""      # grey placeholder of an empty prompt, not typed text
        send("ctrl+c" if prompt else "esc")

    # ---- knob "auto" rotation (agent mode) -------------------------------------
    def _knob_rotate(self, enc_id: int, d: int) -> bool:
        cfg = self.dispatcher.enc_cfg.get(str(enc_id)) or {}
        a = cfg.get("cw" if d > 0 else "ccw") or {}
        if a.get("action") != "auto" or not d:
            return False
        try:
            in_session = osfront.foreground() in self._session_hwnds
        except Exception:
            in_session = False
        for _ in range(max(1, min(abs(d), 5))):
            if in_session:   # window already verified: arrows go through even without an allowed application
                self.injector.send_combo("down" if d > 0 else "up", verified=True)
            else:
                self.injector.send_combo("volume_up" if d > 0 else "volume_down")
        return True

    # ---- knob click / double-click (agent mode) --------------------------------
    def _knob_action(self, name: str) -> dict:
        return (self.dispatcher.enc_cfg.get("0") or {}).get(name) or {}

    def _run_knob(self, name: str):
        a = self._knob_action(name)
        if a.get("action") == "sessions":
            self.agents.toggle_all()
        else:
            self.dispatcher._run(a)

    def _knob_single(self):
        self._knob_timer = None
        self._run_knob("press")

    def _on_knob_click(self):
        now = time.time()
        if self._knob_timer is not None and now - self._knob_last < KNOB_DOUBLE_S:
            self._knob_timer.cancel()
            self._knob_timer = None
            self._run_knob("double")
            return
        self._knob_last = now
        if self._knob_action("double").get("action") in (None, "", "none"):
            self._run_knob("press")            # no double-click configured: the click fires immediately
            return
        self._knob_timer = threading.Timer(KNOB_DOUBLE_S, self._knob_single)
        self._knob_timer.daemon = True
        self._knob_timer.start()

    def _on_agent_key(self, key_id: int):
        try:
            i = self.agent_key_slots.index(key_id)
        except ValueError:
            return
        sess = self._key_sessions[i] if i < len(self._key_sessions) else None
        if sess:
            self.agents.toggle_focus(sess["slot"])
        else:
            self.launch_default(key_id)

    # ---- default project: an agent key without a session opens Claude there ---------------
    def get_default_project(self) -> str:
        return self.config.get("default_project") or ""

    def set_default_project(self, path: str):
        path = (path or "").strip().strip('"')
        if path and not os.path.isdir(path):
            raise ValueError(f"folder not found: {path}")
        if path:
            self.config["default_project"] = path
        else:
            self.config.pop("default_project", None)

    def launch_default(self, key_id: Optional[int] = None) -> dict:
        """Opens a Claude session in the default project. The pressed key lights up blue
        (launching) or red (no default project, or folder missing)."""
        led = self.key_to_led.get(key_id) if key_id is not None else None
        now = time.time()
        path = self.get_default_project()
        if not path or not os.path.isdir(path):
            if led is not None:
                self._overrides[led] = ([255, 0, 0, 0], now + 1.0)
            return {"ok": False, "error": "no default project" if not path else f"folder not found: {path}"}
        if now < self._launch_cooldown:
            return {"ok": False, "error": "a session is already opening"}
        self._launch_cooldown = now + 8.0
        if key_id is not None:
            self._pending_key = (key_id, now + 45.0)   # the time for Claude to start and be detected
        self.agents.open_session(path)
        if led is not None:
            self._overrides[led] = ([0, 80, 255, 0], now + 2.5)
        return {"ok": True, "cwd": path}

    def _broadcast(self, msg: dict):
        if not self.loop:
            return
        import asyncio
        dead = []
        for ws in list(self.ws_clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(msg), self.loop)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.ws_clients:
                self.ws_clients.remove(ws)

    # ---- agents : poll + broadcast independant du lien serie ---------------
    FINISH_IDLE_S = 6.0   # idle for at least 6 s: the CPU sometimes drops mid-thinking

    def _track_finished(self, auto: list):
        try:
            fg = osfront.foreground()
        except Exception:
            fg = None
        now, alive = time.time(), set()
        hw = dict(zip([s.get("pid") for s in self._auto_sessions], self._session_hwnds))
        for s in auto:
            pid, st = s.get("pid"), s.get("status")
            if not pid:
                continue
            alive.add(pid)
            seen = fg is not None and hw.get(pid) == fg
            if st in ("running", "attention"):
                self._run_seen[pid] = now
                self._finished_pids.discard(pid)
            elif st == "waiting" and pid in self._run_seen:
                if seen:
                    self._run_seen.pop(pid, None)            # finished before your eyes: nothing to report
                elif now - self._run_seen[pid] >= self.FINISH_IDLE_S:
                    self._finished_pids.add(pid)
                    self._run_seen.pop(pid, None)
            if seen:
                self._finished_pids.discard(pid)             # you looked at it: it goes back to "idle"
            if pid in self._finished_pids and st == "waiting":
                s["status"] = "finished"
        self._finished_pids &= alive
        for pid in [p for p in self._run_seen if p not in alive]:
            self._run_seen.pop(pid, None)

    def _assign_keys(self, auto: list):
        """Gives an agent key to each session: its own if it already has one, the key
        reserved by a launch in progress, otherwise the first free one (in order of appearance)."""
        slots = self.agent_key_slots
        by_pid = {s["pid"]: s for s in auto if s.get("pid")}
        self._pid_key = {pid: k for pid, k in self._pid_key.items() if pid in by_pid and k in slots}
        used = set(self._pid_key.values())
        pend = self._pending_key
        if pend and (time.time() > pend[1] or pend[0] in used or pend[0] not in slots):
            pend = self._pending_key = None
        for s in auto:                                    # sorted by pid = order of appearance
            pid = s.get("pid")
            if not pid or pid in self._pid_key:
                continue
            if pend:
                k, pend, self._pending_key = pend[0], None, None
            else:
                k = next((x for x in slots if x not in used and not (self._pending_key and x == self._pending_key[0])), None)
            if k is None:
                continue                                  # no more free keys
            self._pid_key[pid] = k
            used.add(k)
        key_pid = {k: pid for pid, k in self._pid_key.items()}
        self._key_sessions = [by_pid.get(key_pid.get(k)) for k in slots]
        for s in auto:
            s["agent_key"] = self._pid_key.get(s.get("pid"))

    PERMISSION_MENU = ["Yes", "Yes, and don't ask again", "No, and tell Claude what to do differently"]

    def on_hook(self, ev: dict):
        """A session event sent by the Claude Code hook (hooks/agentpad_hook.py)."""
        pid, event = ev.get("pid"), ev.get("event") or ""
        if not pid:
            return
        if event == "SessionEnd":
            self._hooks.pop(pid, None)
            return
        h = {"status": "running", "menu": None, "t": time.time()}
        if event == "Stop":
            h["status"] = "waiting"
        elif event == "Notification":
            msg = (ev.get("message") or "").lower()
            if "permission" in msg:
                h.update(status="attention", menu={"question": ev.get("message") or "",
                                                  "items": [{"n": i + 1, "label": l, "selected": i == 0, "desc": ""}
                                                            for i, l in enumerate(self.PERMISSION_MENU)]})
            else:
                h["status"] = "waiting"                  # "Claude is waiting for your input"
        elif event == "PreToolUse" and ev.get("tool_name") == "AskUserQuestion":
            q = ((ev.get("tool_input") or {}).get("questions") or [{}])[0]
            opts = [o.get("label") if isinstance(o, dict) else str(o) for o in q.get("options") or []]
            h.update(status="attention", menu={"question": q.get("question") or "",
                                              "items": [{"n": i + 1, "label": l, "selected": i == 0, "desc": ""}
                                                        for i, l in enumerate(opts)]})
        self._hooks[pid] = h

    def hook_menu(self, pid):
        h = self._hooks.get(pid)
        return h.get("menu") if h else None

    def start_agents_poll(self):
        threading.Thread(target=self._agents_poll_loop, daemon=True).start()

    def _agents_poll_loop(self):
        while not self._agents_stop.is_set():
            try:
                self.agents.scan_system()
                sessions = self.agents.list()
                auto = sorted((s for s in sessions if s.get("auto")), key=lambda s: s.get("pid") or 0)
                att, screens = set(), {}
                for s in auto:   # a menu or a permission prompt on screen: the session is asking you something
                    if not s.get("pid"):
                        continue
                    lines = self.agents.session_screen(s["pid"])     # read once: attention + "Now" view
                    screens[s["pid"]] = lines
                    # the screen tells better than the CPU whether Claude is working: its bar shows
                    # "esc to interrupt" while a response is in progress
                    if s.get("status") == "waiting" and any("esc to interrupt" in l for l in lines[-6:]):
                        s["status"] = "running"
                    if s.get("status") in ("running", "waiting") and self.agents.needs_attention(s["pid"], lines):
                        s["status"] = "attention"
                        att.add(s["pid"])
                    # without screen reading (Mac): state comes from Claude Code hooks
                    h = self._hooks.get(s["pid"]) if not HAS_SCREEN else None
                    if h:
                        s["status"] = h["status"]
                        if h["status"] == "attention":
                            att.add(s["pid"])
                self._screens = screens
                alive = {s.get("pid") for s in auto}
                for pid in [p for p in self._hooks if p not in alive]:
                    self._hooks.pop(pid, None)
                self._attention_pids = att
                self._track_finished(auto)
                self._assign_keys(auto)
                self._auto_sessions = auto
                # window of each session, read here (slow the 1st time) so the frame loop only
                # has to compare it to the foreground window
                self._session_hwnds = [self.agents._session_window(s["pid"]) if s.get("pid") else None for s in auto]
                self._broadcast({"t": "agents", "sessions": sessions})
            except Exception:
                pass
            time.sleep(2.0)

    # ---- automatic reconnection --------------------------------------
    def start_reconnect_watchdog(self):
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        """As long as nothing is connected, retries every ~3s as soon as a
        plausible serial port exists -- covers both server startup
        (nobody has clicked "Connect" yet) and a cable being
        unplugged/replugged along the way (see
        on_disconnect in serial_link.py)."""
        while not self._agents_stop.is_set():
            if self.link is None:
                port = find_port()
                if port:
                    try:
                        self.connect(port)
                    except Exception:
                        pass  # port busy/transient -- retried on the next tick
            time.sleep(3.0)

    # ---- LED preview, UI side: same frame as the one sent to the hardware,
    # broadcast continuously whether the pad is plugged in or not (current_frame()
    # doesn't touch self.link) -- this is what lets the browser
    # "see the LEDs light up" live instead of only showing the
    # static colors from the config. ---------------------------------------
    def start_frame_broadcast(self, fps: float = 10.0):
        threading.Thread(target=self._frame_broadcast_loop, args=(1.0 / fps,), daemon=True).start()

    def _frame_broadcast_loop(self, interval: float):
        while not self._agents_stop.is_set():
            try:
                frame = limit_power(self.current_frame(), self.config.get("power_budget_ma", 420))
                self._broadcast({"t": "frame", "frame": frame})
            except Exception:
                pass
            time.sleep(interval)


hub: Optional[Hub] = None
app = FastAPI(title="agentpad")
# identifier of this startup: an already-open tab sees it changed and reloads
BOOT_ID = str(time.time())
_page_seen = threading.Event()   # an agentpad tab has (re)connected since startup


# Every request that changes the config is recorded on its own (see Hub.record_change).
# These paths don't touch the config, or are ephemeral previews.
_AUTOSAVE_SKIP = {"/api/save", "/api/undo", "/api/connect", "/api/disconnect", "/api/profile",
                  "/api/preview_led", "/api/preview_led_all", "/api/states/preview",
                  "/api/calibration/mode", "/api/calibration/solo", "/api/widget/open",
                  "/api/widget/visible", "/api/agents/launch", "/api/agents/focus", "/api/agents/kill",
                  "/api/live/focus", "/api/live/answer", "/api/live/interrupt", "/api/live/close", "/api/live/resume", "/api/widget/pointer",
                  "/api/agents/arrange", "/api/agents/new", "/api/hook", "/api/hooks"}


@app.middleware("http")
async def _autosave(request, call_next):
    path = request.url.path
    if request.method != "POST" or not path.startswith("/api/") or path in _AUTOSAVE_SKIP or hub is None:
        return await call_next(request)
    before = copy.deepcopy(hub.config)
    response = await call_next(request)
    if hub.config != before:
        hub.record_change(before, path)
    return response


# ---- "Now" view: live sessions, and answering their menus -----------
@app.get("/api/live")
def api_live():
    try:
        fg = osfront.foreground()
    except Exception:
        fg = None
    screens = getattr(hub, "_screens", {})
    out = []
    for i, s in enumerate(hub._auto_sessions):
        pid = s.get("pid")
        hw = hub._session_hwnds[i] if i < len(hub._session_hwnds) else None
        d = AgentSessionManager.screen_digest(screens.get(pid, []))
        if not d.get("menu") and not HAS_SCREEN:
            d["menu"] = hub.hook_menu(pid)
        # empty session = it just started: the "Claude Code v..." welcome screen is still showing and
        # no response ("●") -- a conversation can be resumed there (/resume)
        lines = screens.get(pid, [])
        d["empty"] = (any("Claude Code v" in l for l in lines)
                      and not any(l.lstrip().startswith(("●", "⏺")) for l in lines)) if HAS_SCREEN else None
        out.append({"pid": pid, "slot": s.get("slot"), "name": s.get("name"), "cwd": s.get("cwd"),
                    "status": s.get("status"), "focused": bool(fg and hw == fg),
                    "agent_key": hub._pid_key.get(pid), **d})
    return out


class LiveAnswerBody(BaseModel):
    pid: int
    n: int


class LivePidBody(BaseModel):
    pid: int


@app.post("/api/live/focus")
def api_live_focus(body: LivePidBody):
    return {"ok": hub.agents.focus_pid(body.pid)}


@app.post("/api/live/interrupt")
def api_live_interrupt(body: LivePidBody):
    """Escape in a session (interrupts what it's doing), after bringing it to front and verifying
    it actually has focus -- never into another window."""
    hwnd = hub.agents._session_window(body.pid)
    if not hwnd:
        return {"ok": False, "error": "session window not found"}
    hub.agents.focus_pid(body.pid)
    for _ in range(10):
        if osfront.foreground() == hwnd:
            break
        time.sleep(0.05)
    else:
        return {"ok": False, "error": "could not bring the session to the front"}
    hub.injector.send_combo("esc", verified=True)
    return {"ok": True}


@app.post("/api/live/resume")
def api_live_resume(body: LivePidBody):
    """Types /resume into a session (verified to be in the foreground): the list of conversations
    opens, choose with the knob / arrows, Confirm to resume."""
    hwnd = hub.agents._session_window(body.pid)
    if not hwnd:
        return {"ok": False, "error": "session window not found"}
    hub.agents.focus_pid(body.pid)
    for _ in range(10):
        if osfront.foreground() == hwnd:
            break
        time.sleep(0.05)
    else:
        return {"ok": False, "error": "could not bring the session to the front"}
    hub.injector.send_text("/resume", verified=True)
    time.sleep(0.15)
    hub.injector.send_combo("enter", verified=True)
    return {"ok": True}


def _close_session(pid: int):
    """Closes the session's terminal window (like the window's close button): Claude and its
    terminal stop with it, without typing anything anywhere. If Claude is still running 3 s
    later, its process is terminated. The conversation stays resumable (claude --resume)."""
    if IS_MAC:
        from agentpad import macos
        macos.close_session(pid, hub.agents._session_window(pid))
        return
    import psutil
    import win32con
    import win32gui
    hwnd = hub.agents._session_window(pid)
    if hwnd and win32gui.IsWindow(hwnd):
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    for _ in range(30):
        if not psutil.pid_exists(pid):
            return
        time.sleep(0.1)
    try:
        psutil.Process(pid).terminate()
    except psutil.Error:
        pass


@app.post("/api/live/close")
def api_live_close(body: LivePidBody):
    if body.pid not in [s.get("pid") for s in hub._auto_sessions]:
        return {"ok": False, "error": "unknown session"}
    threading.Thread(target=_close_session, args=(body.pid,), daemon=True).start()
    return {"ok": True}


@app.post("/api/live/answer")
def api_live_answer(body: LiveAnswerBody):
    """Selects option n of the menu open in a session: brings it to front, verifies it
    actually has focus, then arrows + Enter (as on the keyboard)."""
    hwnd = hub.agents._session_window(body.pid)
    if not hwnd:
        return {"ok": False, "error": "session window not found"}
    hub.agents.focus_pid(body.pid)
    for _ in range(10):
        if osfront.foreground() == hwnd:
            break
        time.sleep(0.05)
    else:
        return {"ok": False, "error": "could not bring the session to the front"}
    menu = AgentSessionManager.screen_digest(hub.agents.session_screen(body.pid)).get("menu")
    if not menu and not HAS_SCREEN:
        menu = hub.hook_menu(body.pid)
    if not menu:
        return {"ok": False, "error": "no menu open anymore"}
    sel = next((it["n"] for it in menu["items"] if it["selected"]), None)
    if sel is None or body.n not in [it["n"] for it in menu["items"]]:
        return {"ok": False, "error": "option not found"}
    for _ in range(abs(body.n - sel)):
        hub.injector.send_combo("down" if body.n > sel else "up", verified=True)
        time.sleep(0.03)
    hub.injector.send_combo("enter", verified=True)
    if body.pid in hub._hooks:
        hub._hooks[body.pid].update(status="running", menu=None)   # answered: the menu disappears
    return {"ok": True}


class ProfileCreateBody(BaseModel):
    name: str


class ProfileUpdateBody(BaseModel):
    id: str
    name: Optional[str] = None
    apps: Optional[list[str]] = None


class ProfileIdBody(BaseModel):
    id: str


class AutoProfileBody(BaseModel):
    on: bool


@app.post("/api/profiles/create")
def api_profiles_create(body: ProfileCreateBody):
    pid = hub.create_profile(body.name)
    hub.set_profile(pid)
    return {"ok": True, "id": pid}


@app.post("/api/profiles/update")
def api_profiles_update(body: ProfileUpdateBody):
    hub.update_profile(body.id, body.name, body.apps)
    return {"ok": True}


@app.post("/api/profiles/delete")
def api_profiles_delete(body: ProfileIdBody):
    hub.delete_profile(body.id)
    return {"ok": True}


@app.post("/api/auto_profile")
def api_auto_profile(body: AutoProfileBody):
    hub.set_auto_profile(body.on)
    return {"ok": True}


@app.get("/api/apps")
def api_apps():
    return hub.running_apps()


@app.post("/api/undo")
def api_undo():
    return {"ok": hub.undo(), "undo_depth": hub.undo_depth()}


class ConnectBody(BaseModel):
    port: Optional[str] = None


class ProfileBody(BaseModel):
    profile: str


class KeyBindingBody(BaseModel):
    id: str
    action: Optional[str] = None  # "combo" | "text" | "macro" | "agent" | None (= removes the binding)
    value: Optional[object] = None  # str for combo/text/agent, list[dict] for macro


class EncBindingBody(BaseModel):
    id: str
    cw: Optional[dict] = None
    ccw: Optional[dict] = None
    press: Optional[dict] = None
    double: Optional[dict] = None


class ColorBody(BaseModel):
    id: str
    color: list[int]


class PreviewBody(BaseModel):
    id: int
    color: list[int]


class PreviewAllBody(BaseModel):
    color: list[int]
    zone: str = "all"


class GlobalColorBody(BaseModel):
    which: str  # "default" | "ring"
    color: list[int]


class KeyStateBody(BaseModel):
    key: str
    state: str
    color: list[int]
    anim: str = "solid"
    speed: float = 1.0


class KeyStateResetBody(BaseModel):
    key: str
    state: Optional[str] = None


class StateBody(BaseModel):
    group: str = "keys"
    state: str
    color: list[int]
    anim: str = "solid"
    speed: float = 1.0


class StateResetBody(BaseModel):
    group: str = "keys"
    state: Optional[str] = None


class HaloModeBody(BaseModel):
    mode: str


class BrightnessBody(BaseModel):
    value: int


class ZoneBrightnessBody(BaseModel):
    zone: str
    value: int
    key: Optional[str] = None


class StatePreviewBody(BaseModel):
    group: str = "keys"
    color: list[int]
    anim: str = "solid"
    speed: float = 1.0
    ttl: float = 3.0


class AnimationBody(BaseModel):
    name: str = "none"
    params: dict = {}
    zone: str = "all"


class TargetAppsBody(BaseModel):
    apps: list[str] = []


class AgentConfigBody(BaseModel):
    slot: str
    name: str = ""
    cwd: str = ""
    command: str = "claude"


class AgentActionBody(BaseModel):
    slot: str


class CalibrationBody(BaseModel):
    keys: Optional[dict] = None
    leds: Optional[dict] = None
    joystick: Optional[dict] = None


class CalibrationModeBody(BaseModel):
    on: bool


class SoloBody(BaseModel):
    id: int
    color: list[int] = [0, 0, 0, 255]
    ttl: float = 1.2


@app.get("/api/ports")
def api_ports():
    out = []
    for p in list_ports.comports():
        out.append({"device": p.device, "description": p.description or ""})
    return out


@app.get("/api/state")
def api_state():
    return {
        "connected": hub.link is not None,
        "port": hub.link.port if hub.link else None,
        "profile": hub.profile,
        "device_info": hub.device_info,
        "bindings": hub.get_bindings(),
        "animation": hub.get_animation(),
        "target_apps": hub.config.get("target_apps", []),
        "num_leds": hub.config.get("num_leds", NUM_LEDS_DEFAULT),
        "layout": hub.get_layout(),
        "calibrating": hub.calibrating,
        "boot": BOOT_ID,
        "undo_depth": hub.undo_depth(),
        "profiles": hub.get_profiles(),
        "auto_profile": hub.get_auto_profile(),
        "states": hub.get_states(),
        "state_defaults": state_defaults(),
        "halo_states": hub.get_states("halo"),
        "halo_state_defaults": halo_state_defaults(),
        "halo_mode": hub.get_halo_mode(),
        "ambiance": hub.current_ambiance(),
        "key_states": hub.get_key_states(),
        "key_color_defaults": hub.key_color_defaults(),
        "key_action_defaults": hub.key_action_defaults(),
        "encoder_defaults": hub.encoder_defaults(),
        "brightness": hub.get_brightness(),
        "default_project": hub.get_default_project(),
        "zone_brightness": hub.get_zone_brightness(),
    }


@app.post("/api/states")
def api_states(body: StateBody):
    hub.set_state(body.state, body.color, body.anim, body.speed, body.group)
    return {"ok": True}


@app.post("/api/states/reset")
def api_states_reset(body: StateResetBody):
    hub.reset_states(body.state, body.group)
    return {"ok": True}


@app.post("/api/key_state")
def api_key_state(body: KeyStateBody):
    hub.set_key_state(body.key, body.state, body.color, body.anim, body.speed)
    return {"ok": True}


@app.post("/api/key_state/reset")
def api_key_state_reset(body: KeyStateResetBody):
    hub.reset_key_state(body.key, body.state)
    return {"ok": True}


class AmbianceBody(BaseModel):
    name: str


@app.post("/api/ambiance")
def api_ambiance(body: AmbianceBody):
    hub.apply_ambiance(body.name)
    return {"ok": True, "ambiance": hub.current_ambiance()}


@app.post("/api/halo_mode")
def api_halo_mode(body: HaloModeBody):
    hub.set_halo_mode(body.mode)
    return {"ok": True}


@app.post("/api/zone_brightness")
def api_zone_brightness(body: ZoneBrightnessBody):
    hub.set_zone_brightness(body.zone, body.value, body.key)
    return {"ok": True}


@app.post("/api/brightness")
def api_brightness(body: BrightnessBody):
    hub.set_brightness(body.value)
    return {"ok": True, "brightness": hub.get_brightness()}


@app.post("/api/states/preview")
def api_states_preview(body: StatePreviewBody):
    hub.preview_state({"color": body.color, "anim": body.anim, "speed": body.speed}, min(body.ttl, 10.0), body.group)
    return {"ok": True}


@app.post("/api/calibration")
def api_calibration(body: CalibrationBody):
    return hub.set_calibration(body.keys, body.leds, body.joystick)


@app.post("/api/calibration/mode")
def api_calibration_mode(body: CalibrationModeBody):
    hub.calibrating_until = time.time() + 20 if body.on else 0.0
    if not body.on:
        hub._overrides.clear()  # turns everything back on normally when leaving LED calibration
    return {"ok": True, "calibrating": hub.calibrating}


@app.post("/api/calibration/solo")
def api_calibration_solo(body: SoloBody):
    hub.solo_led(body.id, body.color, body.ttl)
    return {"ok": True}


@app.post("/api/connect")
def api_connect(body: ConnectBody):
    hub.connect(body.port)
    return {"ok": True, "port": hub.link.port if hub.link else None}


@app.post("/api/disconnect")
def api_disconnect():
    hub.disconnect()
    return {"ok": True}


@app.post("/api/profile")
def api_profile(body: ProfileBody):
    hub.set_profile(body.profile)
    return {"ok": True, "bindings": hub.get_bindings(), "animation": hub.get_animation()}


@app.post("/api/binding/key")
def api_binding_key(body: KeyBindingBody):
    hub.set_binding("key", body.id, body.action, body.value)
    return {"ok": True}


@app.post("/api/binding/encoder")
def api_binding_enc(body: EncBindingBody):
    hub.set_binding("encoder", body.id, None, None, cw=body.cw, ccw=body.ccw, press=body.press, double=body.double)
    return {"ok": True}


@app.post("/api/color")
def api_color(body: ColorBody):
    hub.set_color(body.id, body.color)
    return {"ok": True}


class ColorResetBody(BaseModel):
    id: str


@app.post("/api/color/reset")
def api_color_reset(body: ColorResetBody):
    hub.reset_color(body.id)
    return {"ok": True}


@app.post("/api/preview_led")
def api_preview_led(body: PreviewBody):
    hub.preview_led(body.id, body.color)
    return {"ok": True}


@app.post("/api/preview_led_all")
def api_preview_led_all(body: PreviewAllBody):
    hub.preview_led_all(body.color, zone=body.zone)
    return {"ok": True}


@app.post("/api/color_global")
def api_color_global(body: GlobalColorBody):
    if body.which not in ("default", "ring"):
        raise ValueError("which must be 'default' or 'ring'")
    hub.set_color(body.which, body.color)
    return {"ok": True}


@app.post("/api/animation")
def api_animation(body: AnimationBody):
    hub.set_animation(body.name, body.params, body.zone)
    return {"ok": True}


@app.post("/api/target_apps")
def api_target_apps(body: TargetAppsBody):
    hub.set_target_apps(body.apps)
    return {"ok": True}


@app.get("/api/agents")
def api_agents_list():
    out = hub.agents.list()
    for s in out:
        s["agent_key"] = hub._pid_key.get(s.get("pid")) if s.get("auto") else None   # same status as the server loop (detection of a menu on screen)
        if s.get("pid") in hub._attention_pids and s.get("status") in ("running", "waiting"):
            s["status"] = "attention"
        elif s.get("pid") in hub._finished_pids and s.get("status") == "waiting":
            s["status"] = "finished"
    return out


@app.post("/api/agents/configure")
def api_agents_configure(body: AgentConfigBody):
    hub.agents.configure(body.slot, body.name, body.cwd, body.command)
    agents_cfg = hub.config.setdefault("agents", {})
    agents_cfg[body.slot] = {"name": body.name, "cwd": body.cwd, "command": body.command}
    return {"ok": True}


@app.post("/api/agents/remove")
def api_agents_remove(body: AgentActionBody):
    hub.agents.remove(body.slot)
    agents_cfg = hub.config.setdefault("agents", {})
    agents_cfg.pop(body.slot, None)
    return {"ok": True}


@app.post("/api/agents/launch")
def api_agents_launch(body: AgentActionBody):
    info = hub.agents.launch(body.slot)
    return {"ok": True, "session": info}


@app.post("/api/agents/kill")
def api_agents_kill(body: AgentActionBody):
    hub.agents.kill(body.slot)
    return {"ok": True}


@app.post("/api/hook")
async def api_hook(request: Request):
    """A session event, sent by the Claude Code hook (hooks/agentpad_hook.py)."""
    try:
        hub.on_hook(await request.json())
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/hooks")
def api_hooks_status():
    from agentpad.hooks import install
    return {"installed": install.installed(), "needed": not HAS_SCREEN}


class HooksBody(BaseModel):
    install: bool


@app.post("/api/hooks")
def api_hooks_set(body: HooksBody):
    from agentpad.hooks import install
    msg = install.install() if body.install else install.uninstall()
    return {"ok": True, "installed": install.installed(), "message": msg}


class DefaultProjectBody(BaseModel):
    path: Optional[str] = None


@app.post("/api/default_project")
def api_default_project(body: DefaultProjectBody):
    try:
        hub.set_default_project(body.path or "")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "default_project": hub.get_default_project()}


@app.post("/api/agents/new")
def api_agents_new():
    """New Claude session in the default project (radial menu, "Launch")."""
    return hub.launch_default()


@app.post("/api/agents/arrange")
def api_agents_arrange():
    """Arranges the session windows in a grid, or hides them all (toggle)."""
    return {"ok": True, "result": hub.agents.toggle_all()}


@app.post("/api/agents/focus")
def api_agents_focus(body: AgentActionBody):
    ok = hub.agents.focus(body.slot)
    return {"ok": ok}


@app.post("/api/widget/open")
def api_widget_open():
    _try_start_widget()   # closes the running instance: otherwise two radial menus stack up
    return {"ok": True}


class WidgetVisibleBody(BaseModel):
    visible: bool


class WidgetPointerBody(BaseModel):
    angle: Optional[float] = None


@app.post("/api/widget/pointer")
def api_widget_pointer(body: WidgetPointerBody):
    hub.widget_pointer = body.angle
    return {"ok": True}


@app.post("/api/widget/visible")
def api_widget_visible(body: WidgetVisibleBody):
    """Reports server-side whether the floating widget is open, so the
    encoder button knows whether it should tile windows (widget closed) or
    change mode in the radial (widget open) -- see widget.py."""
    hub.widget_visible = body.visible
    if not body.visible:
        hub.widget_pointer = None
    return {"ok": True}


@app.post("/api/save")
def api_save():
    hub.save()
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if ws.query_params.get("c") == "page" or ws.headers.get("origin"):   # a tab (the widget doesn't send an Origin)
        _page_seen.set()
    hub.ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # nothing expected from the client, just keeping the connection open
    except WebSocketDisconnect:
        pass
    finally:
        if ws in hub.ws_clients:
            hub.ws_clients.remove(ws)


class NoCacheStaticFiles(StaticFiles):
    """Static files (app.js/index.html/style.css) change often
    during dev -- without this, Chrome serves them from its disk cache without
    revalidation on a plain F5 and the old version keeps showing
    despite a server-side redeploy (already happened: etag/last-modified
    up to date server-side, but the browser never requested)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def _kill_stray_widgets():
    """A server restart (frequent in dev) used to relaunch the widget
    without ever closing a previous instance -- several floating
    windows would end up stacking on screen. Closes any already-running
    instance before launching a new one."""
    try:
        import psutil
    except ImportError:
        return
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if "agentpad.webui.widget" in " ".join(p.info.get("cmdline") or []):
                p.kill()
        except Exception:
            pass


def _try_start_widget():
    """Launches the floating widget in the background, hidden by default --
    no manual action needed, it opens on its own on the first
    press of the joystick button (see webui/widget.py). Silent if
    PIL/numpy are missing (optional feature)."""
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return
    _kill_stray_widgets()
    subprocess.Popen(
        [sys.executable, "-m", "agentpad.webui.widget"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run():
    global hub
    import asyncio

    config_path = Path(os.environ.get("AGENTPAD_CONFIG", str(DEFAULT_CONFIG_PATH)))
    hub = Hub(config_path)

    async def _main():
        hub.loop = asyncio.get_event_loop()
        hub.start_agents_poll()
        hub.start_auto_profile()
        hub.start_frame_broadcast()
        hub.start_reconnect_watchdog()
        config = uvicorn.Config(app, host="127.0.0.1", port=8420, log_level="warning")
        server = uvicorn.Server(config)
        # an already-open tab reconnects on its own (every 1.5 s) and reloads: a new one is
        # opened only if none has shown up
        threading.Timer(4.0, lambda: None if _page_seen.is_set() else webbrowser.open("http://127.0.0.1:8420")).start()
        threading.Timer(1.5, _try_start_widget).start()
        await server.serve()

    asyncio.run(_main())


if __name__ == "__main__":
    run()
