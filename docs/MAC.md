# agentpad on Mac

> **Experimental.** macOS support is written but has not yet been tried on a real
> Mac. Feedback (and fixes) are welcome.

The pad, the settings page and the firmware are the same as on Windows; only the way
Claude Code sessions are tracked and the radial menu window differ.

## Installation

```sh
cd host-bridge
python3 -m pip install -e .        # also installs pyobjc-framework-Cocoa (radial menu)
agentpad-hooks install             # required on Mac, see below
agentpad-gui                       # starts the software, the page opens in the browser
```

The pad's port is found automatically (`/dev/cu.usbserial-*` or `/dev/cu.wchusbserial-*`). If
nothing shows up with a CH340 chip, install the WCH driver.

## Permissions to grant (once)

macOS asks for them on first use, for the application that launches agentpad (Terminal, iTerm2
or Python):

- **Accessibility** (System Settings › Privacy & Security): without it, the pad's keys
  type nothing (Confirm, Cancel, Mic, macros).
- **Automation › Terminal / iTerm2**: bring a session to the front, hide it, tuck it away,
  close it.

## The Claude Code hooks

On Windows, the software reads the sessions' screen to know whether Claude is working, waiting or
asking you a question. On Mac this is not possible: each session notifies the pad
itself, through Claude Code hooks.

- Install: `agentpad-hooks install`, or the button in the page ("Advanced › Claude Code
  hooks"). A copy of `~/.claude/settings.json` is made before each change; your
  other hooks are left untouched.
- Remove: `agentpad-hooks uninstall` (or the same button).
- Sessions already open before installation only pick them up after a restart.

## What works, what differs

| | Windows | Mac |
|---|---|---|
| Colors per state, "Needs you", "Done" | sessions' screen | hooks |
| Answer a menu from the pad | yes | yes (multiple-choice questions and permissions) |
| Agent keys: show / hide, focus | Windows Terminal | Terminal.app, iTerm2 |
| New session (empty key, "Launch") | Windows Terminal | Terminal.app (`terminal_app: iTerm2` in `config.yaml` for iTerm2) |
| Close a session | closes its window | stops Claude, then closes its tab if it is alone |
| Radial menu | transparent Win32 window | transparent Cocoa window |
| Automatic profile per app | yes | yes |
| "Now" view: session text | yes | no (no screen reading) |
| Session name = tab title | yes | no (folder name) |
