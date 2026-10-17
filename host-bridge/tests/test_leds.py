"""LED states, power budget, and the wiring tables that must agree with each other."""
import re
from pathlib import Path

from agentpad.led_styles import dominant_state, frame_for_state, limit_power
from agentpad.webui import server

STATIC = Path(server.__file__).parent / "static"


def test_power_budget_keeps_hue():
    frame = [(255, 128, 0, 0)] * 24
    out = limit_power(frame, budget_ma=300)
    total_ma = sum(sum(px) for px in out) / 255 * 20
    assert total_ma <= 300
    r, g, _, _ = out[0]
    assert abs(r / g - 255 / 128) < 0.05


def test_power_budget_leaves_dim_frames_alone():
    frame = [(10, 10, 10, 0)] * 24
    assert limit_power(frame, budget_ma=700) == frame


def test_most_important_state_wins_the_halo():
    assert dominant_state(["thinking", "needs_attention", "done"]) == "needs_attention"
    assert dominant_state([]) == "idle"


def test_error_blinks_and_custom_specs_apply():
    on, off = frame_for_state("error", 0.0), frame_for_state("error", 0.3)
    assert on != off and off == (0, 0, 0, 0)
    assert frame_for_state("idle", 0, {"idle": {"color": [1, 2, 3, 4]}}) == (1, 2, 3, 4)


def test_key_and_led_tables_agree():
    # key id -> physical position -> LED id must match KEY_TO_LED
    led_at = {pos: led for led, pos in server.DEFAULT_LED_LAYOUT.items()}
    for pos, key in server.DEFAULT_KEY_LAYOUT.items():
        assert server.KEY_TO_LED[key] == led_at[pos], pos
    assert sorted(server.DEFAULT_LED_LAYOUT) == list(range(server.NUM_LEDS_DEFAULT))
    # the 8 agent keys are the back and middle rows
    assert server.AGENT_KEY_SLOTS == [server.DEFAULT_KEY_LAYOUT[f"r{r}c{c}"] for r in (0, 1) for c in range(4)]


def test_bottom_row_default_colors():
    bolt, confirm, cancel, mic = (server.FRONT_KEY_DEFAULTS[f"r2c{c}"] for c in range(4))
    assert bolt[0] > 200 and bolt[1] > 100 and bolt[2] == 0     # yellow
    assert confirm[1] > confirm[0]                              # green
    assert cancel[0] > 200 and cancel[1] == 0                   # red
    assert mic[2] > 200 and mic[0] == 0                         # blue


def test_web_page_uses_the_same_key_to_led_table():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    pairs = {int(k): int(l) for k, l in re.findall(r"keyId:\s*(\d+),\s*ledId:\s*(\d+)", js)}
    assert pairs == server.KEY_TO_LED
