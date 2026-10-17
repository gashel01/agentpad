"""The login command (nothing is written to the registry here)."""
from pathlib import Path

from agentpad import autostart


def test_login_command_runs_in_the_background_with_an_absolute_config(tmp_path):
    args = autostart.command(tmp_path / "config.yaml")
    assert args[1:4] == ["-m", "agentpad.webui.server", "--background"]
    assert args[-2] == "--config" and Path(args[-1]).is_absolute()


def test_paths_with_spaces_are_quoted():
    assert autostart._quote(["C:/Program Files/py.exe", "-m", "x"]) == '"C:/Program Files/py.exe" -m x'
