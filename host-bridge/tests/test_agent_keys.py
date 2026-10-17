"""Which agent key each Claude session gets (Hub._assign_keys), without a device or a config file."""
import time

from agentpad.webui.server import AGENT_KEY_SLOTS, Hub


def make_hub():
    hub = Hub.__new__(Hub)          # skip __init__: no serial port, no config file
    hub.agent_key_slots = list(AGENT_KEY_SLOTS)
    hub._pid_key = {}
    hub._pending_key = None
    hub._key_sessions = []
    return hub


def sessions(*pids):
    return [{"pid": p, "name": f"s{p}"} for p in pids]


def test_sessions_take_the_first_free_keys_in_order():
    hub = make_hub()
    live = sessions(10, 20, 30)
    hub._assign_keys(live)
    assert [s["agent_key"] for s in live] == AGENT_KEY_SLOTS[:3]
    assert [s and s["pid"] for s in hub._key_sessions[:4]] == [10, 20, 30, None]


def test_a_session_keeps_its_key_when_another_closes():
    hub = make_hub()
    hub._assign_keys(sessions(10, 20, 30))
    live = sessions(10, 30)
    hub._assign_keys(live)
    assert [s["agent_key"] for s in live] == [AGENT_KEY_SLOTS[0], AGENT_KEY_SLOTS[2]]
    live = sessions(10, 30, 40)     # the freed key goes to the newcomer
    hub._assign_keys(live)
    assert live[2]["agent_key"] == AGENT_KEY_SLOTS[1]


def test_the_key_that_launched_a_session_is_reserved_for_it():
    hub = make_hub()
    hub._assign_keys(sessions(10))
    pressed = AGENT_KEY_SLOTS[5]
    hub._pending_key = (pressed, time.time() + 45)
    live = sessions(10, 99)
    hub._assign_keys(live)
    assert live[1]["agent_key"] == pressed
    assert hub._pending_key is None


def test_an_expired_reservation_is_dropped():
    hub = make_hub()
    hub._pending_key = (AGENT_KEY_SLOTS[5], time.time() - 1)
    live = sessions(10)
    hub._assign_keys(live)
    assert live[0]["agent_key"] == AGENT_KEY_SLOTS[0]


def test_more_sessions_than_keys():
    hub = make_hub()
    live = sessions(*range(1, 11))
    hub._assign_keys(live)
    keys = [s["agent_key"] for s in live]
    assert keys[:8] == AGENT_KEY_SLOTS and keys[8:] == [None, None]
