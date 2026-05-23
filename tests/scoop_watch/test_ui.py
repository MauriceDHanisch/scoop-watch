"""Tests for terminal output helpers."""

from scoop_watch import ui


def test_paint_wraps_when_enabled():
    assert ui.paint("32", "ok", enabled=True) == "\033[32mok\033[0m"


def test_paint_is_plain_when_disabled():
    assert ui.paint("32", "ok", enabled=False) == "ok"


def test_color_disabled_by_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.color_enabled() is False
