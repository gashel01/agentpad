import json
import threading
import time
from typing import Callable, Optional

import serial
from serial.tools import list_ports


def is_bluetooth(p) -> bool:
    """Bluetooth serial links (paired phones, headsets...): never the pad, and opening one can hang."""
    return "bluetooth" in (p.description or "").lower() or "BTHENUM" in (p.hwid or "").upper() \
        or "bluetooth" in (p.device or "").lower()


def find_port() -> Optional[str]:
    """The pad's USB serial port, or None (the caller retries later). Never falls back to just any
    port: at login the USB port can show up after the Bluetooth ones, and picking one of those left
    the software "connected" to something that is not the pad."""
    known, usb = [], []
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        dev = p.device or ""
        if dev.startswith("/dev/tty.") or is_bluetooth(p):
            continue                      # macOS: the same port also exists as /dev/cu.*, which is the one to open
        if any(k in desc for k in ("cp210", "ch340", "ch9102", "usb-serial", "usb serial", "silicon labs", "uart")) \
                or any(k in dev for k in ("usbserial", "usbmodem", "wchusbserial", "SLAB_USBtoUART", "ttyUSB", "ttyACM")):
            known.append(p.device)
        elif p.vid is not None:           # some other USB serial chip
            usb.append(p.device)
    return (known + usb or [None])[0]


class SerialLink:
    def __init__(self, port: Optional[str], baud: int = 115200, on_message: Optional[Callable[[dict], None]] = None,
                 on_disconnect: Optional[Callable[[], None]] = None):
        self.port = port or find_port()
        if not self.port:
            raise RuntimeError("No serial port detected. Plug in the pad or specify --port COMx.")
        self.baud = baud
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self._ser: Optional[serial.Serial] = None
        self._stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()

    def open(self):
        # EXPLICIT write_timeout: on Windows, omitting this parameter makes
        # writes also inherit the short timeout from the `timeout` parameter
        # (reads) -- at 24fps (LED animator), the OS buffer can briefly fill
        # up and that raised SerialTimeoutException, which silently killed
        # the animation thread (no more LEDs afterward).
        self._ser = serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=2.0)
        time.sleep(2.0)  # gives the ESP32's auto-reset time to happen on plug-in
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self.send({"t": "ping"})        # the firmware answers with "hello": proof it is really the pad

    def close(self):
        self._stop.set()
        if self._ser:
            self._ser.close()

    def send(self, msg: dict):
        line = json.dumps(msg) + "\n"
        with self._write_lock:
            if self._ser and self._ser.is_open:
                self._ser.write(line.encode("utf-8"))

    def _read_loop(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except (serial.SerialException, OSError):
                # cable unplugged / port disappeared -- not just a normal close()
                # (which itself calls _stop.set() before closing the port).
                # We close the dead handle again: otherwise Windows keeps the COM
                # port busy and automatic reconnection keeps failing in a loop.
                lost = not self._stop.is_set()
                self._stop.set()
                try:
                    self._ser.close()
                except Exception:
                    pass
                if lost and self.on_disconnect:
                    self.on_disconnect()
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", errors="ignore"))
                except json.JSONDecodeError:
                    continue
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        # a PROCESSING error (not a serial read one) must
                        # never kill this thread -- already seen in practice: a
                        # Windows exception in SetForegroundWindow (agents
                        # mode) bubbled up to here and silently stopped the
                        # whole pad, even though /api/state kept saying
                        # "connected" (the link itself was fine).
                        pass
