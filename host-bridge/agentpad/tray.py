"""Notification-area icon: click to open the page, right-click to quit.

Its own small process (started by the server, like the radial menu), talking to the
server over its local API. It closes itself when the server goes away.
"""
import json
import threading
import time
import urllib.request
import webbrowser

import pystray
from PIL import Image, ImageDraw

URL = "http://127.0.0.1:8420"
HALO_ON, HALO_OFF = (255, 176, 32), (120, 120, 120)


def _api(path: str, body: dict | None = None, timeout: float = 2):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + "/api" + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def draw_icon(connected: bool) -> Image.Image:
    """The pad seen from the top: light case, 4 x 3 keys, the halo lit when the pad is connected."""
    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    halo = HALO_ON if connected else HALO_OFF
    d.rounded_rectangle((1, 1, s - 2, s - 2), radius=14, fill=halo)
    d.rounded_rectangle((6, 6, s - 7, s - 7), radius=10, fill=(222, 222, 218))
    for r in range(3):
        for c in range(4):
            x, y = 12 + c * 11, 17 + r * 11
            d.rounded_rectangle((x, y, x + 8, y + 8), radius=2, fill=(70, 70, 74))
    return img


class Tray:
    def __init__(self):
        self.connected = None
        self.icon = pystray.Icon("agentpad", draw_icon(False), "agentpad", menu=pystray.Menu(
            pystray.MenuItem("Open agentpad", self.open_page, default=True),
            pystray.MenuItem(lambda _: "Pad connected" if self.connected else "Pad not connected", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit agentpad", self.quit),
        ))

    def open_page(self, *_):
        webbrowser.open(URL)

    def quit(self, *_):
        try:
            _api("/quit", {})
        except Exception:
            pass
        self.icon.stop()

    def _watch(self):
        misses = 0
        while True:
            try:
                connected = bool((_api("/state") or {}).get("connected"))
                misses = 0
            except Exception:
                misses += 1
                if misses >= 3:          # the server is gone: so is its icon
                    self.icon.stop()
                    return
                time.sleep(3)
                continue
            if connected != self.connected:
                self.connected = connected
                self.icon.icon = draw_icon(connected)
                self.icon.title = "agentpad · " + ("pad connected" if connected else "pad not connected")
                self.icon.update_menu()
            time.sleep(3)

    def run(self):
        threading.Thread(target=self._watch, daemon=True).start()
        self.icon.run()


def run():
    Tray().run()


if __name__ == "__main__":
    run()
