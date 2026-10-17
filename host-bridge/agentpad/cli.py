import argparse
import sys
import time

import yaml

from agentpad.led_styles import Animator
from agentpad.modes.claude_code import ClaudeCodeMode
from agentpad.modes.macropad import MacropadMode
from agentpad.serial_link import SerialLink

MODES = {
    "claude_code": ClaudeCodeMode,
    "macropad": MacropadMode,
}


def load_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"Config '{path}' not found, starting with an empty config.", file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser(prog="agentpad")
    parser.add_argument("--mode", choices=list(MODES.keys()), default="claude_code")
    parser.add_argument("--port", default=None, help="e.g. COM5 (auto-detected if omitted)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    config = load_config(args.config)
    num_leds = config.get("num_leds", 24)   # firmware: 12 keys + 12 halo LEDs
    power_budget_ma = config.get("power_budget_ma", 420.0)

    mode = MODES[args.mode](config, num_leds)
    print(f"[agentpad] active mode: {mode.name}")

    def on_message(msg: dict):
        t = msg.get("t")
        if t == "key":
            mode.on_key(msg.get("id", -1), msg.get("ev") == "down")
        elif t == "enc":
            mode.on_enc(msg.get("id", -1), msg.get("d", 0))
        elif t == "joy":
            if hasattr(mode, "on_joy"):
                mode.on_joy(msg.get("x", 0), msg.get("y", 0))
        elif t == "hello":
            print(f"[agentpad] device connected: {msg.get('fw')} v{msg.get('ver')} "
                  f"({msg.get('keys')} keys, {msg.get('leds')} leds)")

    link = SerialLink(args.port, args.baud, on_message=on_message)
    link.open()
    print(f"[agentpad] connected on {link.port}")

    animator = Animator(link.send, mode.get_frame, power_budget_ma=power_budget_ma)
    animator.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        animator.stop()
        if hasattr(mode, "stop"):
            mode.stop()
        link.close()


if __name__ == "__main__":
    main()
