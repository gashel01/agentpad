import pytest


class FakeInjector:
    """Records what would have been typed instead of sending it to the OS."""

    def __init__(self):
        self.sent = []

    def send_combo(self, combo, verified=False):
        self.sent.append(("combo", combo))

    def send_text(self, text, verified=False):
        self.sent.append(("text", text))


@pytest.fixture
def injector():
    return FakeInjector()
