"""Key bindings and joystick decoding (no device, no OS input)."""
import time

from agentpad.joystick import JoystickDecoder
from agentpad.modes.common import ActionDispatcher


def test_combo_and_text_fire_on_press_only(injector):
    d = ActionDispatcher({"keys": {"2": {"action": "combo", "value": "ctrl+s"},
                                   5: {"action": "text", "value": "git status\n"}}}, injector)
    d.on_key(2, True)
    d.on_key(2, False)
    d.on_key(5, True)
    assert injector.sent == [("combo", "ctrl+s"), ("text", "git status\n")]


def test_unbound_key_does_nothing(injector):
    ActionDispatcher({}, injector).on_key(7, True)
    assert injector.sent == []


def test_encoder_rotation_and_press(injector):
    d = ActionDispatcher({"encoders": {"0": {"cw": {"action": "combo", "value": "page_down"},
                                             "ccw": {"action": "combo", "value": "page_up"},
                                             "press": {"action": "combo", "value": "enter"}}}}, injector)
    d.on_enc(0, 1)
    d.on_enc(0, -1)
    d.on_enc_button(100, True)     # knob click = key id 100
    d.on_enc_button(100, False)
    assert injector.sent == [("combo", "page_down"), ("combo", "page_up"), ("combo", "enter")]


def test_macro_runs_steps_in_order(injector):
    d = ActionDispatcher({"keys": {"8": {"action": "macro", "value": [
        {"type": "key", "value": "ctrl+s"}, {"type": "wait", "value": 10}, {"type": "text", "value": "ok"}]}}},
        injector)
    d.on_key(8, True)
    deadline = time.time() + 2
    while len(injector.sent) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert injector.sent == [("combo", "ctrl+s"), ("text", "ok")]


def test_agent_action_calls_back(injector):
    seen = []
    d = ActionDispatcher({"keys": {"0": {"action": "agent", "value": 3}}}, injector, agent_cb=seen.append)
    d.on_key(0, True)
    assert seen == ["3"]


class Recorder:
    def __init__(self):
        self.events = []

    def on_key(self, key_id, down):
        self.events.append((key_id, down))


def test_joystick_first_sample_is_the_center():
    r = Recorder()
    j = JoystickDecoder(r, deadzone=500)
    j.on_raw(2000, 2100)
    assert j.center == (2000, 2100) and r.events == []


def test_joystick_deadzone_and_directions():
    r = Recorder()
    j = JoystickDecoder(r, deadzone=500, center=(2048, 2048))
    j.on_raw(2048 + 300, 2048)          # inside the dead zone
    assert r.events == []
    j.on_raw(4095, 2048)                # east
    j.on_raw(2048, 4095)                # north (y grows upward)
    j.on_raw(2048, 2048)                # back to rest
    assert r.events == [(202, True), (202, False), (200, True), (200, False)]


def test_joystick_invert():
    r = Recorder()
    j = JoystickDecoder(r, deadzone=500, invert_y=True, center=(2048, 2048))
    j.on_raw(2048, 4095)
    assert r.events == [(204, True)]    # south once inverted
