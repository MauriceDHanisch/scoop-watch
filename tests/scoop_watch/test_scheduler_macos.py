"""Tests for the launchd backend's pure-Python helpers.

These cover plist construction, schedule round-tripping and next-firing
arithmetic. ``arm`` / ``disarm`` / ``is_armed`` shell out to ``launchctl`` and
are exercised manually on a Mac host; the dispatcher in ``scheduler.py``
chooses the backend at call time, so monkeypatching ``platform.system`` is
enough to route to either side from any host.
"""

from __future__ import annotations

import datetime as dt

from scoop_watch import config, scheduler_macos


def test_calendar_entries_one_dict_per_weekday():
    text = scheduler_macos._calendar_entries(["Mon", "Wed", "Fri"], "07:30")
    assert text.count("<dict>") == 3
    # Mon=1, Wed=3, Fri=5 in launchd's Sunday-zero indexing.
    assert "<key>Weekday</key><integer>1</integer>" in text
    assert "<key>Weekday</key><integer>3</integer>" in text
    assert "<key>Weekday</key><integer>5</integer>" in text
    assert text.count("<key>Hour</key><integer>7</integer>") == 3
    assert text.count("<key>Minute</key><integer>30</integer>") == 3


def test_calendar_entries_empty_means_every_day():
    """An empty weekday list mirrors scheduler_linux: all seven days."""
    text = scheduler_macos._calendar_entries([], "09:00")
    assert text.count("<dict>") == len(config.WEEKDAYS)


def test_next_firing_today_in_future():
    """If the target time is later today on a scheduled weekday, it's today."""
    now = dt.datetime(2026, 5, 25, 6, 0)  # Monday 06:00
    nxt = scheduler_macos._next_firing(["Mon", "Wed"], "07:30", now=now)
    assert nxt == dt.datetime(2026, 5, 25, 7, 30)


def test_next_firing_today_already_passed_skips_to_next_scheduled_day():
    now = dt.datetime(2026, 5, 25, 8, 0)  # Monday 08:00, after 07:30
    nxt = scheduler_macos._next_firing(["Mon", "Wed"], "07:30", now=now)
    assert nxt == dt.datetime(2026, 5, 27, 7, 30)  # next Wed


def test_next_firing_skips_unscheduled_weekdays():
    now = dt.datetime(2026, 5, 26, 6, 0)  # Tuesday
    nxt = scheduler_macos._next_firing(["Mon", "Fri"], "04:00", now=now)
    assert nxt == dt.datetime(2026, 5, 29, 4, 0)  # next Fri


def test_plist_dir_is_not_library_launchagents(monkeypatch, tmp_path):
    """Regression: plists must NOT be written into ~/Library/LaunchAgents/,
    because launchd auto-loads everything in that directory at login. Storing
    them under the data root makes the on-disk schedule reboot-ephemeral —
    only ``scoop-watch arm`` bootstraps it into the running session."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    target = scheduler_macos.paths.launchd_plist_dir()
    assert "Library/LaunchAgents" not in str(target)
    assert target.is_relative_to(tmp_path)


def test_plist_round_trips_through_disk(tmp_path, monkeypatch):
    """A plist written by ``arm`` parses back into the same schedule via
    ``_read_calendar_from_plist`` (used by ``timer_line``)."""
    monkeypatch.setattr(scheduler_macos.paths, "launchd_plist_dir", lambda: tmp_path)
    monkeypatch.setattr(scheduler_macos.paths, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(
        scheduler_macos.paths, "shim_path", lambda: "/usr/local/bin/scoop-watch"
    )
    # Stub launchctl: we are testing plist content, not the bootstrap call.
    monkeypatch.setattr(
        scheduler_macos,
        "_launchctl",
        lambda *args: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    scheduler_macos.arm("demo", ["Mon", "Thu"], "06:45")

    schedule = scheduler_macos._read_calendar_from_plist("demo")
    assert schedule == (["Mon", "Thu"], "06:45")
