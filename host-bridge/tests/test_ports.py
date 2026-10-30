"""Which serial port is taken for the pad (nothing is opened here)."""
from types import SimpleNamespace

from agentpad import serial_link


def port(device, description, hwid="", vid=None):
    return SimpleNamespace(device=device, description=description, hwid=hwid, vid=vid)


BT = port("COM5", "Standard Serial over Bluetooth link (COM5)", "BTHENUM\{00001101}")
PAD = port("COM6", "Silicon Labs CP210x USB to UART Bridge (COM6)", "USB VID:PID=10C4:EA60", 0x10C4)


def test_the_pad_wins_over_bluetooth_ports(monkeypatch):
    monkeypatch.setattr(serial_link.list_ports, "comports", lambda: [BT, PAD])
    assert serial_link.find_port() == "COM6"


def test_never_falls_back_to_a_bluetooth_port(monkeypatch):
    # at login the pad's USB port may not exist yet: wait for it rather than open a Bluetooth link
    monkeypatch.setattr(serial_link.list_ports, "comports", lambda: [BT])
    assert serial_link.find_port() is None


def test_an_unknown_usb_serial_chip_is_still_found(monkeypatch):
    other = port("COM9", "USB Serial Device (COM9)", "USB VID:PID=303A:1001", 0x303A)
    monkeypatch.setattr(serial_link.list_ports, "comports", lambda: [BT, other])
    assert serial_link.find_port() == "COM9"
