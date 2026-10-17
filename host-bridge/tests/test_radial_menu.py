"""Radial menu layers (WidgetController), with a fake window and no server."""
import pytest

from agentpad.webui import widget


class FakeWindow:
    def __init__(self):
        self.title, self.items, self.hover, self.hint, self.shown = "", [], 0, "", False

    def set_list(self, title, items, hover, hint):
        self.title, self.items, self.hover, self.hint = title, items, hover, hint

    def set_hover(self, hover):
        self.hover = hover

    def set_cursor(self, nx, ny):
        pass

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False


@pytest.fixture
def api(monkeypatch):
    """Answers /state and /live from a dict, records every POST."""
    data = {"/state": {"profile": "claude_code", "default_project": "C:/code/app"}, "/live": [], "/agents": []}
    posts = []
    monkeypatch.setattr(widget, "_api_get", lambda path: data.get(path))
    monkeypatch.setattr(widget, "_api_post", lambda path, body, timeout=2: posts.append((path, body)))
    monkeypatch.setattr(widget, "_get_cursor_pos", lambda: (0, 0))
    return data, posts


def open_menu(api, live):
    data, _ = api
    data["/live"] = live
    w = widget.WidgetController(FakeWindow())
    w.show()
    return w


def labels(w):
    return [it["label"] for it in w.window.items]


def test_root_in_agents_mode(api):
    w = open_menu(api, [])
    assert labels(w) == ["Sessions", "Mode", "Launch", "Lighting", "Windows", "Undo"]
    assert w.window.hint == "Confirm: select · Cancel: close"


def test_active_session_adds_close_and_resume_when_empty(api):
    w = open_menu(api, [{"pid": 7, "name": "app", "focused": True, "empty": True, "status": "waiting"}])
    assert labels(w)[:3] == ["Resume", "Close", "Sessions"]
    w.on_radial("ok")                               # Resume is hovered first
    assert api[1][-1] == ("/widget/visible", {"visible": False})
    assert ("/live/resume", {"pid": 7}) in api[1]


def test_busy_session_has_no_resume(api):
    w = open_menu(api, [{"pid": 7, "name": "app", "focused": True, "empty": False, "status": "running"}])
    assert "Resume" not in labels(w)
    assert labels(w)[0] == "Sessions"


def test_close_asks_first_and_cancel_goes_back(api):
    w = open_menu(api, [{"pid": 7, "name": "app", "focused": True, "empty": False}])
    w.hover = labels(w).index("Close")
    w.on_radial("ok")
    assert labels(w) == ["Yes, close", "No"] and w.window.title == "Menu › Close?"
    w.on_radial("back")
    assert labels(w)[1] == "Close"
    assert not any(p[0] == "/live/close" for p in api[1])


def test_a_waiting_question_opens_straight_on_the_answers(api):
    menu = {"question": "Allow this command?", "items": [{"n": 1, "label": "Yes"}, {"n": 2, "label": "No"}]}
    w = open_menu(api, [{"pid": 7, "name": "app", "menu": menu}])
    assert labels(w) == ["1. Yes", "2. No"]
    assert w.window.hint == "Allow this command?"
    w.on_radial("next")
    w.on_radial("ok")
    assert ("/live/answer", {"pid": 7, "n": 2}) in api[1]


def test_knob_turns_wrap_around(api):
    w = open_menu(api, [])
    w.on_radial("prev")
    assert w.hover == len(w.items) - 1
    w.on_radial("next")
    assert w.hover == 0


def test_cancel_at_the_root_closes_the_menu(api):
    w = open_menu(api, [])
    w.on_radial("back")
    assert not w.visible and not w.window.shown
