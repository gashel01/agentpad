import math
import threading
import time
from typing import Callable, List, Tuple

RGBW = Tuple[int, int, int, int]


def solid(color: RGBW) -> RGBW:
    return color


def breathe(color: RGBW, t: float, period: float = 2.4) -> RGBW:
    phase = (math.sin(2 * math.pi * t / period) + 1) / 2  # 0..1
    level = 0.15 + 0.85 * phase
    return tuple(int(c * level) for c in color)


def blink(color: RGBW, t: float, period: float = 0.5) -> RGBW:
    on = int(t / (period / 2)) % 2 == 0
    return color if on else (0, 0, 0, 0)


# Palette by agent state (RGBW order, 0-255). Free to taste/adjust.
STATE_COLORS = {
    "idle": (0, 0, 0, 0),
    "thinking": (0, 60, 255, 0),
    "running_tool": (255, 150, 0, 0),
    "waiting_input": (255, 255, 255, 40),
    "needs_attention": (255, 190, 0, 0),   # yellow: a choice or permission is waiting for your response
    "done": (0, 200, 40, 0),
    "error": (255, 0, 0, 0),
}

STATE_STYLE = {
    "idle": "solid",
    "thinking": "breathe",
    "running_tool": "breathe",
    "waiting_input": "solid",
    "needs_attention": "breathe",
    "done": "breathe",   # "Finished": a session completed while you were looking elsewhere
    "error": "blink",
}


STATE_ANIMS = ("solid", "breathe", "blink")


STATE_SPEED = {"needs_attention": 1.8, "done": 0.7}   # asking you: fast; finished: slow   # breathes faster: it should stand out


def state_defaults() -> dict:
    """Factory setting for each state: {state: {color, anim, speed}}. The app
    customizes it (config "<profile>.states") and can always revert to it."""
    return {s: {"color": list(c), "anim": STATE_STYLE.get(s, "solid"), "speed": STATE_SPEED.get(s, 1.0)}
            for s, c in STATE_COLORS.items()}


# Halo in agent mode: it summarizes all sessions (the most important state wins),
# with its own settings -- softer by default than the keys, it's an ambiance.
HALO_STATE_COLORS = {
    "idle": (0, 0, 0, 0),
    "thinking": (0, 40, 180, 0),
    "running_tool": (180, 100, 0, 0),
    "waiting_input": (0, 0, 0, 30),
    "needs_attention": (255, 170, 0, 0),
    "done": (0, 150, 30, 0),
    "error": (255, 0, 0, 0),
}
# from most important to least important
# halo: whatever demands your attention most comes first. "Finished" comes before work in
# progress; a session simply at rest (already seen) comes last.
STATE_PRIORITY = ("error", "needs_attention", "done", "running_tool", "thinking", "waiting_input", "idle")


# Ambiances: a complete set of states (keys + halo) in one click. "defaut" = factory setting.
# Each state: (RGBW color, animation, speed). The halo is a softer version.
def _amb(keys: dict, halo_scale: float = 0.7) -> dict:
    k = {s: {"color": list(c), "anim": a, "speed": v} for s, (c, a, v) in keys.items()}
    h = {s: {"color": [int(x * halo_scale) for x in c], "anim": a, "speed": v} for s, (c, a, v) in keys.items()}
    return {"keys": k, "halo": h}


AMBIANCES = {
    "calme": _amb({
        "idle": ((0, 0, 0, 0), "solid", 1.0),
        "thinking": ((0, 40, 160, 0), "breathe", 0.6),
        "running_tool": ((140, 80, 0, 0), "breathe", 0.6),
        "waiting_input": ((0, 0, 0, 25), "solid", 1.0),
        "needs_attention": ((230, 150, 0, 0), "breathe", 1.0),
        "done": ((0, 140, 30, 0), "breathe", 0.5),
        "error": ((200, 0, 0, 0), "breathe", 1.0),
    }),
    "vif": _amb({
        "idle": ((0, 0, 0, 0), "solid", 1.0),
        "thinking": ((0, 120, 255, 0), "breathe", 1.4),
        "running_tool": ((255, 140, 0, 0), "breathe", 1.4),
        "waiting_input": ((0, 0, 0, 110), "solid", 1.0),
        "needs_attention": ((255, 200, 0, 0), "blink", 0.8),
        "done": ((0, 255, 60, 0), "breathe", 1.0),
        "error": ((255, 0, 0, 0), "blink", 1.5),
    }, halo_scale=0.85),
    "nuit": _amb({
        "idle": ((0, 0, 0, 0), "solid", 1.0),
        "thinking": ((0, 8, 40, 0), "solid", 1.0),
        "running_tool": ((30, 15, 0, 0), "solid", 1.0),
        "waiting_input": ((0, 0, 0, 0), "solid", 1.0),
        "needs_attention": ((140, 90, 0, 0), "breathe", 1.0),
        "done": ((0, 70, 15, 0), "breathe", 0.5),
        "error": ((120, 0, 0, 0), "breathe", 1.0),
    }, halo_scale=0.5),
}


def halo_state_defaults() -> dict:
    return {s: {"color": list(c), "anim": STATE_STYLE.get(s, "solid"), "speed": STATE_SPEED.get(s, 1.0)}
            for s, c in HALO_STATE_COLORS.items()}


def dominant_state(states) -> str:
    present = set(states)
    return next((s for s in STATE_PRIORITY if s in present), "idle")


def render_state(spec: dict, t: float) -> RGBW:
    color = tuple(spec.get("color") or (0, 0, 0, 0))
    anim = spec.get("anim", "solid")
    speed = max(0.2, float(spec.get("speed", 1.0) or 1.0))
    if anim == "breathe":
        return breathe(color, t, period=2.4 / speed)
    if anim == "blink":
        return blink(color, t, period=0.5 / speed)
    return solid(color)


def frame_for_state(state: str, t: float, specs: dict | None = None) -> RGBW:
    """`specs` = reglages personnalises par etat ; un etat absent garde son defaut."""
    spec = (specs or {}).get(state) or state_defaults().get(state) or {"color": [0, 0, 0, 0]}
    return render_state(spec, t)


# AgentSessionManager status -> visual state reusing the palette above
# (already designed for this: "agent status in RGB", see pyproject.toml).
AGENT_STATUS_TO_STATE = {
    "offline": "idle",
    "running": "thinking",
    "waiting": "waiting_input",
    "error": "error",
    "done": "done",
    "finished": "done",   # finished its task while you were looking elsewhere, not seen yet
    "attention": "needs_attention",   # menu or permission on the session's screen (read by the server)
}


def rainbow_frame(n: int, t: float, speed: float = 1.0) -> List[RGBW]:
    import colorsys
    out = []
    for i in range(max(n, 1)):
        hue = (i / max(n, 1) + t * speed * 0.15) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        out.append((int(r * 255), int(g * 255), int(b * 255), 0))
    return out


def wave_frame(n: int, t: float, color: RGBW, speed: float = 1.0, width: float = 3.0) -> List[RGBW]:
    out = []
    for i in range(max(n, 1)):
        phase = math.sin((i - t * speed * 4) / max(width, 0.1))
        level = (phase + 1) / 2
        out.append(tuple(int(c * level) for c in color))
    return out


def apply_animation(frame: List[list], anim: dict, t: float, num_leds: int) -> List[list]:
    """Lays an animation preset over `frame` (already built from
    config colors), on the chosen zone. Doesn't touch ids outside the
    zone -- allows e.g. a breathing effect on the ring alone while the
    keys keep their fixed colors."""
    name = (anim or {}).get("name", "none")
    if not name or name == "none":
        return frame
    params = anim.get("params") or {}
    zone = anim.get("zone", "all")
    if zone == "keys":
        indices = list(range(min(12, num_leds)))
    elif zone == "ring":
        indices = list(range(12, num_leds))
    else:
        indices = list(range(num_leds))
    color = tuple(params.get("color", [91, 140, 255, 0]))
    speed = float(params.get("speed", 1.0) or 1.0)
    if name == "breathe":
        val = breathe(color, t, period=max(0.3, 2.4 / speed))
        for i in indices:
            frame[i] = list(val)
    elif name == "pulse":
        val = blink(color, t, period=max(0.1, 1.0 / speed))
        for i in indices:
            frame[i] = list(val)
    elif name == "rainbow":
        colors = rainbow_frame(len(indices), t, speed)
        for c, i in zip(colors, indices):
            frame[i] = list(c)
    elif name == "wave":
        colors = wave_frame(len(indices), t, color, speed)
        for c, i in zip(colors, indices):
            frame[i] = list(c)
    return frame


MA_PER_CHANNEL_FULL = 20  # SK6812RGBW, approx. mA per channel (R/G/B/W) at 255


def limit_power(frame: List[RGBW], budget_ma: float, ma_per_channel: float = MA_PER_CHANNEL_FULL) -> List[RGBW]:
    """Proportionally reduces all LEDs if the image would exceed
    `budget_ma` (hue preserved, just dimmer) -- to stay within what
    a plain USB port can supply without external power."""
    total_units = sum(sum(px) for px in frame)  # cumulative 0-255 units, all channels
    if total_units <= 0:
        return frame
    estimated_ma = (total_units / 255.0) * ma_per_channel
    if estimated_ma <= budget_ma:
        return frame
    scale = budget_ma / estimated_ma
    return [tuple(int(c * scale) for c in px) for px in frame]


class Animator:
    """Pushes a led_frame at a regular rate from a `get_frame() ->
    List[RGBW]` function supplied by the active mode. Applies a global
    current budget (`power_budget_ma`) before sending, to stay usable
    over a single USB cable (no external 5V power required)."""

    def __init__(self, send_fn: Callable[[dict], None], get_frame: Callable[[], List[RGBW]],
                 fps: float = 24.0, power_budget_ma: float = 420.0):
        self._send = send_fn
        self._get_frame = get_frame
        self._interval = 1.0 / fps
        self._budget_ma = power_budget_ma
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                frame = limit_power(self._get_frame(), self._budget_ma)
            except Exception:
                # an invalid setting (e.g. non-numeric speed) or a transient error
                # computing the image: skip this frame instead of killing the thread
                # (otherwise the LEDs stay frozen for the rest of the session, silently)
                time.sleep(self._interval)
                continue
            try:
                self._send({"t": "led_frame", "px": [list(px) for px in frame]})
            except Exception:
                # a failed write (timeout, transient USB disconnection) must
                # never kill the whole thread -- otherwise no LED moves at
                # all for the rest of the session, silently.
                pass
            time.sleep(self._interval)
