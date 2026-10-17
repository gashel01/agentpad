"""Floating "agents" widget -- a native window always on top,
driven entirely by the joystick, not the mouse:

  - joystick button (id 110) -> opens / closes the menu (hidden by default)
  - joystick movement -> RADIAL menu: the continuously pointed
    direction (real angle, not a step-by-step scroll) highlights
    the item closest to that direction
  - Confirm key -> picks the pointed item: enters its submenu
    (layer) or runs the action
  - Cancel key -> goes back up one layer; at the root, closes the menu
    (the server never types them anywhere while the menu is open)
  - knob click (id 100) -> like Confirm; rotation -> next / previous item
  - on opening, if a session is asking a question (menu on screen), the
    menu opens directly on its choices
  - the computer's mouse moves -> closes the widget again (the joystick and
    the mouse must never step on each other)

All the state logic lives HERE: a thread connects to the already-running
server's /ws and drives the menu window (radial_native.RadialWindow), which
only draws what it's given. No more pywebview: WebView2 can't do a
transparent window on Windows (the menu used to be a square).
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
import urllib.request

JOY_CENTER = 2048
JOY_RANGE = 2048
JOY_DEADZONE = 500  # wider than the visual preview -- requires a deliberate deflection

# ---- menu tree ----------------------------------------------------
# A single root; each item opens a submenu ("open": (title, function that builds its
# list)) or runs an action ("do"). An action closes the menu, except settings ("stay") which
# stay open to show the new value. Confirm enters/runs, Cancel goes back.
_SHOWN_KEYS = ("label", "sub", "status", "active", "more", "detail")
AMBIANCES = [("defaut", "Default"), ("calme", "Calm"), ("vif", "Vivid"), ("nuit", "Night")]
BRIGHTNESS_LEVELS = [(10, "Low"), (30, "Medium"), (60, "High"), (100, "Max")]
STATUS_TEXT = {"running": "working", "waiting": "idle", "attention": "needs you", "finished": "done",
               "error": "error", "offline": "offline"}


def _get_cursor_pos():
    from agentpad.osfront import cursor_pos
    return cursor_pos()


def _api_get(path: str):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8420/api{path}", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _api_post(path: str, body: dict, timeout: float = 2):
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:8420/api{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


class WidgetController:
    def __init__(self, window):
        self.window = window
        self.visible = False
        self.st: dict = {}                # /api/state, read again on open and after every setting change
        self.live: list[dict] = []        # /api/live: sessions + menu open on screen
        self.focused_pid = None           # foreground session when the menu opens (it doesn't take focus)
        self.agents_cfg: list[dict] = []  # /api/agents: configured sessions (to launch)
        self.stack: list[dict] = []       # open layers: {"title", "build"}
        self.items: list[dict] = []       # items of the displayed layer (cached, rebuilt on every change)
        self.hover = 0
        self.joy = (JOY_CENTER, JOY_CENTER)
        self._last_mouse = None

    # ---- data ------------------------------------------------------------
    def _fetch(self, state=True, live=True, agents=False):
        if state:
            self.st = _api_get("/state") or self.st
        if live:
            self.live = _api_get("/live") or []
        if agents:
            self.agents_cfg = _api_get("/agents") or []

    # ---- layers -------------------------------------------------------------
    def _root(self) -> list[dict]:
        agents_mode = self.st.get("profile") == "claude_code"
        asking = sum(1 for s in self.live if s.get("menu"))
        summary = " · ".join(f"{s.get('name')} {STATUS_TEXT.get(self._status(s), '')}" for s in self.live)
        sessions = {"label": "Sessions", "more": True,
                    "sub": (f"{asking} need{'s' if asking == 1 else ''} you" if asking else f"{len(self.live)} open"),
                    "status": "attention" if asking else None, "open": ("Sessions", self._sessions),
                    "detail": summary or "No Claude session open"}
        mode = {"label": "Mode", "more": True, "sub": self._profile_name(), "open": ("Mode", self._modes),
                "detail": f"Profile {self._profile_name()} · auto per app: {'on' if self.st.get('auto_profile') else 'off'}"}
        dp = self.st.get("default_project") or ""
        launch = {"label": "Launch", "more": True, "open": ("Launch", self._launchers),
                  "detail": f"New session in {self._base(dp)}" if dp else "No default project"}
        light = {"label": "Lighting", "more": True, "open": ("Lighting", self._light),
                 "detail": f"Ambiance {dict(AMBIANCES).get(self.st.get('ambiance'), 'custom')} · {self.st.get('brightness', 0)} %"}
        windows = {"label": "Windows", "more": True, "open": ("Windows", self._windows),
                   "detail": "Tile the sessions in a grid, or hide them all"}
        depth = self.st.get("undo_depth", 0)
        undo = {"label": "Undo", "sub": "nothing to undo" if not depth else f"{depth} saved",
                "do": (lambda: _api_post("/undo", {})) if depth else None,
                "detail": "Go back to the previous setting" if depth else "No setting to undo"}
        # in Agents mode, sessions come first; in macros mode, mode and lighting come first
        order = [sessions, mode, launch, light, windows, undo] if agents_mode else [mode, light, sessions, launch, windows, undo]
        # menu opened from a session (it had focus): close it in two gestures
        active = self._session_by_pid(self.focused_pid) if self.focused_pid else None
        if active and active.get("empty"):
            order.insert(0, {"label": "Resume", "sub": active.get("name") or "", "status": "attention",
                             "detail": "Pick a past conversation to continue in this session",
                             "do": lambda pid=self.focused_pid: _api_post("/live/resume", {"pid": pid})})
        if active:
            name = active.get("name") or "the session"
            order.insert(1 if agents_mode else 0, {
                "label": "Close", "sub": name, "status": "error", "more": True,
                "open": ("Close?", lambda pid=self.focused_pid: self._confirm_close(pid)),
                "detail": f"Close the active session: {name}"})
        return order

    @staticmethod
    def _status(s: dict) -> str:
        return "attention" if s.get("menu") else s.get("status", "offline")

    @staticmethod
    def _base(path: str) -> str:
        import os
        return os.path.basename((path or "").rstrip("/\\")) or path

    def _sessions(self) -> list[dict]:
        out = []
        for s in self.live:
            st = self._status(s)
            last = next((l.strip().lstrip("●⎿ ").strip() for l in reversed(s.get("text") or []) if l.strip()), "")
            out.append({"label": s.get("name") or str(s.get("pid")), "sub": STATUS_TEXT.get(st, st), "status": st,
                        "more": True, "open": (s.get("name") or "Session", lambda pid=s.get("pid"): self._session(pid)),
                        "detail": (s.get("menu") or {}).get("question") or last or self._base(s.get("cwd"))})
        return out

    def _session_by_pid(self, pid):
        return next((s for s in self.live if s.get("pid") == pid), None)

    def _session(self, pid) -> list[dict]:
        s = self._session_by_pid(pid) or {}
        items = [{"label": "Show", "sub": "bring to front", "do": lambda: _api_post("/live/focus", {"pid": pid}),
                  "detail": "Bring its window to the front"}]
        if s.get("menu"):
            items.insert(0, {"label": "Answer", "more": True, "status": "attention",
                             "sub": f"{len(s['menu']['items'])} choices", "open": ("Answer", lambda: self._answers(pid)),
                             "detail": s["menu"].get("question") or "It is waiting for your choice"})
        if s.get("empty") is not False:            # empty, or unknown (Mac: no screen reading)
            items.insert(0, {"label": "Resume", "sub": "past conversation",
                             "detail": "Opens /resume: pick with the knob, Confirm to continue",
                             "do": lambda: _api_post("/live/resume", {"pid": pid})})
        items.append({"label": "Interrupt", "sub": "Esc in the session",
                      "do": lambda: _api_post("/live/interrupt", {"pid": pid}),
                      "detail": "Sends Esc: it stops what it is doing"})
        items.append({"label": "Close", "more": True, "sub": "quit Claude",
                      "open": ("Close?", lambda: self._confirm_close(pid)),
                      "detail": "Closes its terminal window (asks first)"})
        cwd = s.get("cwd") or ""
        is_default = bool(cwd) and cwd == self.st.get("default_project")
        items.append({"label": "Default project", "sub": "this one" if is_default else self._base(cwd),
                      "active": is_default, "stay": True, "detail": cwd or "Unknown folder",
                      "do": (lambda: _api_post("/default_project", {"path": cwd})) if cwd and not is_default else None})
        return items

    def _confirm_close(self, pid) -> list[dict]:
        name = (self._session_by_pid(pid) or {}).get("name") or "this session"
        return [{"label": "Yes, close", "status": "error", "detail": f"Close {name} (the conversation can be resumed)",
                 "do": lambda: _api_post("/live/close", {"pid": pid})},
                {"label": "No", "back": True, "detail": "Go back without closing anything"}]

    def _answers(self, pid) -> list[dict]:
        self._answer_pid = pid
        s = self._session_by_pid(pid) or {}
        menu = s.get("menu") or {}
        return [{"label": f"{it['n']}. {it['label']}", "sub": it.get("desc", ""),
                 "detail": it["label"] + (f" ({it['desc']})" if it.get("desc") else ""),
                 "do": lambda n=it["n"]: _api_post("/live/answer", {"pid": pid, "n": n}, timeout=5)}
                for it in menu.get("items", [])]

    def _question(self) -> str:
        pid = getattr(self, "_answer_pid", None)
        s = self._session_by_pid(pid) or {}
        return (s.get("menu") or {}).get("question") or ""

    def _profile_name(self) -> str:
        return next((p["name"] for p in self.st.get("profiles", []) if p["id"] == self.st.get("profile")), "")

    def _modes(self) -> list[dict]:
        items = [{"label": p["name"], "active": p["id"] == self.st.get("profile"),
                  "sub": "Claude sessions" if p.get("kind") == "agents" else "macros",
                  "detail": "The keys follow your Claude sessions" if p.get("kind") == "agents" else "The keys send your macros",
                  "do": lambda pid=p["id"]: _api_post("/profile", {"profile": pid})} for p in self.st.get("profiles", [])]
        auto = bool(self.st.get("auto_profile"))
        items.append({"label": "Auto per app", "sub": "on" if auto else "off", "active": auto, "stay": True,
                      "detail": "Switches profile by itself based on the app in front",
                      "do": lambda: _api_post("/auto_profile", {"on": not auto})})
        return items

    def _launchers(self) -> list[dict]:
        import os
        dp = self.st.get("default_project") or ""
        out = [{"label": "New session", "sub": self._base(dp) if dp else "no default project",
                "detail": dp or "Set a default project in the app, or from a session",
                "do": (lambda: _api_post("/agents/new", {})) if dp else None}]
        for a in self.agents_cfg:
            if a.get("auto"):
                continue
            ok = os.path.isdir(a.get("cwd") or "")
            out.append({"label": a.get("name") or a["slot"],
                        "sub": ("already open" if a.get("alive") else os.path.basename((a.get("cwd") or "").rstrip("/\\")))
                        if ok else "folder not found",
                        "status": "running" if a.get("alive") else None, "detail": a.get("cwd") or "",
                        "do": (lambda slot=a["slot"]: _api_post("/agents/launch", {"slot": slot})) if ok else None})
        return out

    def _light(self) -> list[dict]:
        amb = dict(AMBIANCES).get(self.st.get("ambiance"), "Custom")
        bright = self.st.get("brightness", 0)
        halo = self.st.get("halo_mode", "agents")
        return [
            {"label": "Ambiance", "more": True, "sub": amb, "open": ("Ambiance", self._ambiances),
             "detail": "Colors and animations for every state"},
            {"label": "Brightness", "more": True, "sub": f"{bright} %", "open": ("Brightness", self._brightness),
             "detail": "Overall pad brightness"},
            {"label": "Halo", "sub": "follows sessions" if halo == "agents" else "solid color", "stay": True,
             "detail": "Shows the most urgent session state" if halo == "agents" else "Solid profile color",
             "do": lambda: _api_post("/halo_mode", {"mode": "fixed" if halo == "agents" else "agents"})},
            {"label": "Test LEDs", "sub": "white 2 s", "detail": "Everything white for 2 seconds",
             "do": lambda: _api_post("/preview_led_all", {"color": [255, 255, 255, 120]})},
        ]

    def _ambiances(self) -> list[dict]:
        cur = self.st.get("ambiance")
        return [{"label": name, "active": key == cur, "stay": True,
                 "do": lambda k=key: _api_post("/ambiance", {"name": k})} for key, name in AMBIANCES]

    def _brightness(self) -> list[dict]:
        cur = self.st.get("brightness", 0)
        return [{"label": name, "sub": f"{v} %", "active": v == cur, "stay": True,
                 "do": lambda v=v: _api_post("/brightness", {"value": v})} for v, name in BRIGHTNESS_LEVELS]

    def _windows(self) -> list[dict]:
        return [{"label": "Tile / hide", "sub": "all sessions", "do": lambda: _api_post("/agents/arrange", {}),
                 "detail": "1st press: grid · 2nd press: all hidden"}]

    def _rebuild(self, keep_hover=False):
        layer = self.stack[-1]["build"] if self.stack else self._root
        self.items = layer()
        if not keep_hover or self.hover >= len(self.items):
            self.hover = 0
        self._push_list()

    def _title(self) -> str:
        # last two levels only: the full path overflows the center
        return " › ".join((["Menu"] + [layer["title"] for layer in self.stack])[-2:])

    def _items(self) -> list[dict]:
        return self.items

    # ---- joystick state: real angle, not a scroll -----------------
    def _is_centered(self, x, y) -> bool:
        return abs(x - JOY_CENTER) < JOY_DEADZONE and abs(y - JOY_CENTER) < JOY_DEADZONE

    def on_joy(self, x, y):
        self.joy = (x, y)
        if not self.visible:
            return
        items = self.items
        # normalized stick position (-1..1), for the cursor that follows the joystick continuously --
        # y inverted, same convention as the angle below and as the main app's preview
        nx = max(-1.0, min(1.0, (x - JOY_CENTER) / JOY_RANGE))
        ny = max(-1.0, min(1.0, -(y - JOY_CENTER) / JOY_RANGE))
        self._push_cursor(nx, ny)
        if not self._is_centered(x, y) and items:
            # real joystick angle -> item closest to that direction
            angle = math.atan2(-(y - JOY_CENTER), x - JOY_CENTER)
            n = len(items)
            # item 0 at the top (angle = pi/2), clockwise
            idx = round(((math.pi / 2 - angle) % (2 * math.pi)) / (2 * math.pi / n)) % n
            if idx != self.hover:
                self.hover = idx
                self._push_hover()

    def on_joy_button_down(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def on_radial(self, cmd: str):
        """Confirm / Cancel keys, relayed by the server only while the menu is open."""
        if not self.visible:
            return
        if cmd in ("next", "prev") and self.items:
            self.hover = (self.hover + (1 if cmd == "next" else -1)) % len(self.items)
            self._push_hover()
        elif cmd == "ok":
            self._select_hover()
        elif cmd == "back":
            if self.stack:
                self.stack.pop()
                self._rebuild()
            else:
                self.hide()

    def on_encoder_button_down(self):
        """Knob click, menu open = Confirm (the server already ignores this click meanwhile)."""
        if self.visible:
            self._select_hover()

    def on_agents(self, sessions: list[dict]):
        """Sessions update (~2 s): rereads the sessions' screen and refreshes the displayed
        layer, keeping the pointed item."""
        if self.visible:
            self._fetch(state=False, live=True)
            self._rebuild(keep_hover=True)

    # ---- actions ------------------------------------------------------------
    def _select_hover(self):
        if not (0 <= self.hover < len(self.items)):
            return
        item = self.items[self.hover]
        if item.get("back"):
            self.on_radial("back")
            return
        if "open" in item:
            title, build = item["open"]
            if build in (self._launchers,):
                self._fetch(state=False, live=False, agents=True)
            self.stack.append({"title": title, "build": build})
            self._rebuild()
            return
        if not item.get("do"):
            return                                  # informational item (e.g. folder not found)
        item["do"]()
        if item.get("stay"):
            self._fetch(live=False)
            self._rebuild(keep_hover=True)
        else:
            self.hide()

    def show(self):
        self._fetch()
        self.focused_pid = next((s.get("pid") for s in self.live if s.get("focused")), None)
        self.visible = True
        self.stack = []
        self.hover = 0
        # a session is asking a question: open directly on its choices
        asking = next((s for s in self.live if s.get("menu")), None)
        if asking:
            pid = asking.get("pid")
            self.stack = [{"title": "Sessions", "build": self._sessions},
                          {"title": asking.get("name") or "Session", "build": lambda: self._session(pid)},
                          {"title": "Answer", "build": lambda: self._answers(pid)}]
        self._last_mouse = _get_cursor_pos()
        self._rebuild()
        self.window.show()
        self._push_cursor(0.0, 0.0)
        _api_post("/widget/visible", {"visible": True})

    def hide(self):
        self.visible = False
        self.stack = []
        self.window.hide()
        _api_post("/widget/visible", {"visible": False})

    # ---- push state to the window (and the halo compass) -----------
    def _push_list(self):
        items = [{k: it[k] for k in _SHOWN_KEYS if it.get(k) is not None} for it in self.items]
        hint = "Confirm: select · Cancel: back" if self.stack else "Confirm: select · Cancel: close"
        if self.stack and self.stack[-1]["title"] == "Answer":
            hint = self._question()                  # Answer layer: the question in the center
        self.window.set_list(self._title(), items, self.hover, hint)
        self._push_pointer()

    def _push_hover(self):
        self.window.set_hover(self.hover)
        self._push_pointer()

    def _push_pointer(self):
        n = len(self.items)
        _api_post("/widget/pointer", {"angle": (self.hover * 360 / n) if n else None})

    def _push_cursor(self, nx: float, ny: float):
        self.window.set_cursor(nx, ny)

    # ---- background loops --------------------------------------------------
    def run_ws_loop(self):
        from websockets.sync.client import connect

        while True:
            try:
                with connect("ws://127.0.0.1:8420/ws", open_timeout=5) as ws:
                    for raw in ws:
                        msg = json.loads(raw)
                        t = msg.get("t")
                        if t == "joy":
                            self.on_joy(msg.get("x", JOY_CENTER), msg.get("y", JOY_CENTER))
                        elif t == "key" and msg.get("ev") == "down":
                            if msg.get("id") == 110:
                                self.on_joy_button_down()
                            elif msg.get("id") == 100:
                                self.on_encoder_button_down()
                        elif t == "radial":
                            self.on_radial(msg.get("cmd", ""))
                        elif t == "agents":
                            self.on_agents(msg.get("sessions", []))
            except Exception:
                time.sleep(2.0)  # server not ready yet / disconnected -- retry

    def run_mouse_watch_loop(self):
        while True:
            if self.visible:
                pos = _get_cursor_pos()
                if self._last_mouse is not None:
                    dx = abs(pos[0] - self._last_mouse[0])
                    dy = abs(pos[1] - self._last_mouse[1])
                    if dx > 6 or dy > 6:
                        self.hide()
                self._last_mouse = pos
            time.sleep(0.12)


def run():
    if sys.platform == "darwin":
        from agentpad.webui.radial_mac import RadialWindowMac as RadialWindow
    else:
        from agentpad.webui.radial_native import RadialWindow

    window = RadialWindow()            # hidden until the joystick button opens it
    controller = WidgetController(window)
    threading.Thread(target=controller.run_ws_loop, daemon=True).start()
    threading.Thread(target=controller.run_mouse_watch_loop, daemon=True).start()
    window.run()                       # window loop, on the main thread


if __name__ == "__main__":
    run()
