# agentpad serial protocol

USB serial link (CDC through the ESP32 board's USB-UART chip), 115200 baud,
JSON lines terminated by `\n`. Both sides ignore any line they do not
recognize (forward compatibility).

## Device -> Host

| `t`       | Fields                          | Meaning |
|-----------|----------------------------------|------|
| `hello`   | `fw`, `ver`, `keys`, `leds`      | Sent at boot and in reply to `{"t":"ping"}`. Lets the bridge check it is talking to the right firmware before sending commands. |
| `key`     | `id` (int), `ev` (`"down"`/`"up"`) | Matrix key (id 0-11) or encoder button (id 100 = encoder 0, 101 = encoder 1). |
| `enc`     | `id` (int), `d` (int, +1/-1 per detent) | Rotation of a rotary encoder. |
| `joy`     | `x`, `y` (int, 0-4095 raw 12-bit ADC) | Raw position of the planar joystick. Not calibrated, not filtered: center and dead zone are handled on the Python bridge side (`agentpad/joystick.py`), not in the firmware. |

Reserved `key` ids (beyond 0-11 for the matrix):

| id | Source |
|----|--------|
| 100 | Encoder #0 button |
| 101 | Encoder #1 button (optional) |
| 110 | Joystick button |
| 200-207 | Joystick directions (N, NE, E, SE, S, SW, W, NW) — **never sent by the firmware**, synthesized on the bridge side from `joy` by `JoystickDecoder`. |

Examples:
```
{"t":"hello","fw":"agentpad-fw","ver":"0.1.0","keys":12,"leds":22}
{"t":"key","id":3,"ev":"down"}
{"t":"key","id":3,"ev":"up"}
{"t":"enc","id":0,"d":1}
{"t":"key","id":100,"ev":"down"}
{"t":"joy","x":2048,"y":1980}
{"t":"key","id":110,"ev":"down"}
```

## Host -> Device

| `t`         | Fields                                   | Effect |
|-------------|-------------------------------------------|-------|
| `ping`      | -                                          | The device replies `{"t":"hello",...}`. |
| `led`       | `id`, `r`,`g`,`b`,`w` (0-255)              | Sets LED `id` (same indexing as keys 0-11, then the ring 12+). |
| `led_all`   | `r`,`g`,`b`,`w`                            | Sets all LEDs (keys + ring) to the same color. |
| `led_frame` | `px`: list of `[r,g,b,w]`, length = NUM_LEDS | Updates all LEDs in a single call (used by host-side animations, ~24 fps). |

### LED indexing

A single SK6812RGBW chain: ids `0-11` are the 12 keys (same
`row*3+col` order as the matrix), then ids `12` to `12+RING_LEDS-1` form
the separate status ring around the case (12 pixels by default, around the
edge/walls, not under the keys). See
`hardware/PINOUT.md` for the physical wiring order of the ring.

The firmware does NO local animation (no built-in breathing/pulse):
all color logic lives on the Python bridge side, which pushes `led_frame` at a
regular rate. This keeps the firmware trivial and all the
"agent state -> color" logic editable without reflashing.

## Why not direct HID?

A classic ESP32 (WROOM/WROVER) has no native USB-OTG: it cannot
enumerate as a real USB keyboard. So we go through this serial protocol +
a Python bridge that simulates keystrokes at the OS level (via
`pynput`) when a key/encoder triggers an action. This is also what
most DIY stream-deck/macropads on a classic ESP32 do.
