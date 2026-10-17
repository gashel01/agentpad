"""Infinite-loop LED test to track down cold solder joints in the SK6812 chain.

Cycle: chase (one LED at a time, in chain order) -> solid colors
R, G, B, W (a badly soldered channel gives a wrong hue on one LED) -> soft solid
white (tap / gently flex the wires: an intermittent joint will flicker).
Brightness limited to stay under ~500 mA on a USB port. Ctrl+C to stop.

Usage: python tools/led_test_cycle.py [COM6]
"""
import json
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
LEVEL = 50          # 0-255 per channel: ~24 LEDs x 3 channels stays under the USB budget
STEP_S = 0.35       # duration of one LED in the chase
HOLD_S = 1.5        # duration of a solid color
SOAK_S = 6.0        # duration of the solid white (the moment to tap the wires)


def send(ser, msg):
    ser.write((json.dumps(msg) + "\n").encode())


def hello(ser):
    """Asks the firmware for the number of LEDs (replies {"t":"hello",...,"leds":N})."""
    send(ser, {"t": "ping"})
    end = time.time() + 3
    while time.time() < end:
        line = ser.readline().decode(errors="ignore").strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("t") == "hello":
                return d
    return {}


def frame(ser, px):
    send(ser, {"t": "led_frame", "px": px})


def connect():
    """Opens the port, waits for it to reappear if the pad disappeared from the USB bus."""
    waited = False
    while True:
        try:
            ser = serial.Serial(PORT, 115200, timeout=0.2, write_timeout=2.0)
            break
        except serial.SerialException:
            if not waited:
                print("%s  waiting for %s (pad unplugged or powered off)..." % (time.strftime("%H:%M:%S"), PORT), flush=True)
                waited = True
            time.sleep(1.0)
    time.sleep(2.0)                       # the ESP32 resets when the port is opened
    info = hello(ser)
    if not info:                          # silent (e.g. still in download mode after a flash): pulse on EN
        ser.dtr = False; ser.rts = True; time.sleep(0.15); ser.rts = False
        time.sleep(2.5)
        info = hello(ser)
    if not info:
        raise serial.SerialException("no hello from the firmware")
    n = int(info["leds"])
    print("%s  firmware %s %s: %d LEDs" % (time.strftime("%H:%M:%S"), info.get("fw", "?"), info.get("ver", "?"), n), flush=True)
    return ser, n


def run_cycles(ser, n, state):
    off = [[0, 0, 0, 0]] * n
    while True:
        state["cycle"] += 1
        print("--- cycle %d" % state["cycle"], flush=True)
        for i in range(n):                                    # 1. chase
            px = [list(p) for p in off]; px[i] = [0, 0, 0, LEVEL * 2]
            state["step"] = "LED %d alone" % i
            frame(ser, px)
            print("  LED %2d lit alone" % i, flush=True)
            time.sleep(STEP_S)
        for name, c in (("red", [LEVEL, 0, 0, 0]), ("green", [0, LEVEL, 0, 0]),
                        ("blue", [0, 0, LEVEL, 0]), ("white", [0, 0, 0, LEVEL])):   # 2. solid colors
            state["step"] = "all in " + name
            frame(ser, [c] * n)
            print("  all in %s" % name, flush=True)
            time.sleep(HOLD_S)
        state["step"] = "solid white"
        frame(ser, [[0, 0, 0, LEVEL]] * n)                    # 3. solid white: tap the wires
        print("  solid white %.0f s: tap / gently flex the wires" % SOAK_S, flush=True)
        time.sleep(SOAK_S)


def main():
    state = {"cycle": 0, "step": "", "drops": 0}
    ser = None
    try:
        while True:
            try:
                ser, n = connect()
                run_cycles(ser, n, state)
            except serial.SerialException:
                # the pad disappeared from the USB bus: often a power glitch (5V/GND) -> log when
                state["drops"] += 1
                print("%s  !!! PAD LOST (drop #%d) during: %s, cycle %d" % (
                    time.strftime("%H:%M:%S"), state["drops"], state["step"], state["cycle"]), flush=True)
                try: ser.close()
                except Exception: pass
                ser = None
    except KeyboardInterrupt:
        pass
    finally:
        if ser:
            try:
                frame(ser, [[0, 0, 0, 0]] * n); ser.close()
            except serial.SerialException:
                pass


if __name__ == "__main__":
    main()
