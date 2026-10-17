# Setup

Three steps: flash the ESP32, start the software, calibrate the pad. Allow about half an hour.

## 1. Firmware (ESP32)

1. Install [PlatformIO](https://platformio.org/) (VS Code extension or CLI).
2. Wire the pad following [`hardware/PINOUT.md`](../hardware/PINOUT.md), or adapt
   `firmware/include/config.h` to your wiring (it is the only file to change).
3. Plug the ESP32 in over USB, then from `firmware/`:
   ```sh
   pio run -t upload
   pio device monitor
   ```
   At boot you should see `{"t":"hello",...}`, then `{"t":"key",...}` on every key press.
   The protocol is described in [`PROTOCOL.md`](PROTOCOL.md).

## 2. Software (Windows, macOS)

Python 3.10 or later. From `host-bridge/`:

```sh
pip install -e .
agentpad-gui
```

The page opens in your browser (`http://127.0.0.1:8420`). The pad's port is found
automatically; if not, pick it in the "Device" card. Every setting changed in the page
is saved automatically to `config.yaml` (Ctrl+Z undoes the last one).

- **Windows**: nothing else to do. The software reads the screen of the Claude Code sessions
  open in Windows Terminal to know what they are doing.
- **macOS** (experimental): also install the Claude Code hooks, see [`MAC.md`](MAC.md).

### Start with the computer

In the page, **Advanced › Start with the computer** (or `agentpad-autostart on` from
`host-bridge/`). agentpad then starts in the background when you log in: no console, no
browser tab, the pad lights up on its own. Running `agentpad-gui` again just opens the page.
Its output goes to `~/.agentpad/agentpad.log`. Turn it off the same way (`agentpad-autostart off`).
Windows uses a per-user Run entry, macOS a LaunchAgent, Linux `~/.config/autostart`.

## 3. Calibrate

Click **Calibrate** at the top of the page, then **Guided calibration**: press the keys
in the requested order, confirm the LEDs that light up, then move the joystick. Everything
is saved at the end. Each part can also be redone on its own.

## Next

- **Profiles**: "Agents" (the keys follow your Claude Code sessions) and as many
  macro profiles as you like, which can switch on automatically based on the foreground
  application.
- **Default project**: an agent key with no session opens a new session there.
- **Radial menu**: click the joystick. Point with the joystick (or turn the knob), Confirm to
  select, Cancel to go back.
