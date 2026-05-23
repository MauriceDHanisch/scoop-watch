"""Tests for systemd unit construction (no systemd calls).

macOS launchd backend tests live in ``test_scheduler_macos.py``; the
front-end dispatcher in ``scheduler.py`` is a one-liner per call so its
behavior is covered through each backend's tests.
"""

from scoop_watch import config, scheduler_linux as scheduler


def test_unit_stem_namespaced():
    assert scheduler._unit_stem("demo") == "scoop-watch-demo"


def test_on_calendar_all_days_is_plain():
    assert scheduler.on_calendar(list(config.WEEKDAYS), "07:00") == "*-*-* 07:00:00"


def test_on_calendar_empty_is_plain():
    assert scheduler.on_calendar([], "08:00") == "*-*-* 08:00:00"


def test_on_calendar_subset_keeps_weekday_order():
    assert (
        scheduler.on_calendar(["Fri", "Mon", "Wed"], "06:30")
        == "Mon,Wed,Fri *-*-* 06:30:00"
    )


def test_service_template_runs_the_project():
    text = scheduler._SERVICE_TEMPLATE.format(project="demo", shim="/bin/scoop-watch")
    assert "ExecStart=/bin/scoop-watch run demo" in text
    assert "Type=oneshot" in text


def test_parse_list_timers_splits_columns_around_double_spaces():
    """A row from `systemctl list-timers --no-legend` parses into named fields,
    even though the NEXT/LAST values contain their own single spaces."""
    row = (
        "Mon 2026-05-25 04:00:00 PDT   2 days left    n/a  n/a    "
        "scoop-watch-demo.timer    scoop-watch-demo.service"
    )
    parsed = scheduler._parse_list_timers(row)
    assert parsed["next"] == "Mon 2026-05-25 04:00:00 PDT"
    assert parsed["left"] == "2 days left"
    assert parsed["last"] == "n/a"
    assert parsed["passed"] == "n/a"
    assert parsed["unit"] == "scoop-watch-demo.timer"
    assert parsed["activates"] == "scoop-watch-demo.service"


def test_timer_template_is_reboot_ephemeral():
    text = scheduler._TIMER_TEMPLATE.format(
        project="demo", on_calendar="Mon,Wed *-*-* 07:30:00"
    )
    assert "OnCalendar=Mon,Wed *-*-* 07:30:00" in text
    assert "Persistent=false" in text
