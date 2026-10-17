from agentpad.injector import KeyInjector
from agentpad.joystick import JoystickDecoder
from agentpad.modes.common import ActionDispatcher

DEFAULT_COLOR = (20, 20, 20, 0)
ENCODER_BUTTON_IDS = (100, 101)


class MacropadMode:
    name = "macropad"

    def __init__(self, config: dict, num_leds: int):
        macropad_cfg = config.get("macropad", {})
        self.dispatcher = ActionDispatcher(macropad_cfg, KeyInjector(config.get("target_apps")))
        self.joystick = JoystickDecoder(
            self.dispatcher,
            deadzone=macropad_cfg.get("joystick", {}).get("deadzone", 650),
            invert_x=macropad_cfg.get("joystick", {}).get("invert_x", False),
            invert_y=macropad_cfg.get("joystick", {}).get("invert_y", False),
        )
        colors_cfg = {str(k): v for k, v in (macropad_cfg.get("colors") or {}).items()}
        default = tuple(colors_cfg.get("default", DEFAULT_COLOR))
        ring_color = tuple(colors_cfg.get("ring", default))
        self.frame = [default] * num_leds
        for k, v in colors_cfg.items():
            if k in ("default", "ring"):
                continue
            try:
                idx = int(k)
            except ValueError:
                continue
            if 0 <= idx < num_leds:
                self.frame[idx] = tuple(v)
        for i in range(12, num_leds):
            self.frame[i] = ring_color

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
        return self.frame
