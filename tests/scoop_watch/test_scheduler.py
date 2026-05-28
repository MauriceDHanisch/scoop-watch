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
    text = scheduler._SERVICE_TEMPLATE.format(
        project="demo", shim="/bin/scoop-watch", path=scheduler._SERVICE_PATH_SPECIFIER
    )
    assert "ExecStart=/bin/scoop-watch run demo" in text
    assert "Type=oneshot" in text


def test_service_template_exports_user_local_bin_on_path():
    """Regression: an overnight run reaches synthesis and fails with
    "'claude' not found on PATH" because systemd --user strips PATH down
    to system dirs only. The armed service must add %h/.local/bin (where
    claude / codex / uv normally live) so synthesis can find the agent."""
    text = scheduler._SERVICE_TEMPLATE.format(
        project="demo", shim="/bin/scoop-watch", path=scheduler._SERVICE_PATH_SPECIFIER
    )
    assert "Environment=PATH=" in text
    assert "%h/.local/bin" in text


def test_schedule_retry_exports_resolved_home_dir_on_path(monkeypatch):
    """Regression: the first PATH fix shipped %h/.local/bin to BOTH the
    armed service template (correct: systemd expands %h in unit files)
    AND to `systemd-run --setenv` (wrong: --setenv values are taken
    literally, so the retry's PATH ended up containing the literal
    string "%h" and synthesis still failed with "claude not found").
    The transient retry must carry the expanded $HOME-prefixed PATH."""
    import subprocess
    from pathlib import Path

    captured: dict = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler.paths, "shim_path", lambda: "/x/scoop-watch")

    scheduler.schedule_retry(
        "ofdft", attempt=1, delay_minutes=60, session_date="2026-05-25"
    )
    path_flags = [arg for arg in captured["cmd"] if arg.startswith("--setenv=PATH=")]
    assert path_flags, "transient retry unit must export PATH"
    flag = path_flags[0]
    assert "%h" not in flag, "must NOT contain unexpanded %h specifier"
    assert f"{Path.home()}/.local/bin" in flag


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


def test_schedule_retry_invokes_systemd_run_with_correct_unit_and_args(monkeypatch):
    """A retry is scheduled as a transient systemd unit fired ``delay`` from
    now, running ``scoop-watch run <project> --retry-attempt=N``. The unit
    name embeds project + attempt so simultaneous retries don't collide on
    the bus; the function returns the unit name so the caller can log it."""
    import subprocess

    captured: dict = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler.paths, "shim_path", lambda: "/x/scoop-watch")

    unit = scheduler.schedule_retry(
        "ofdft", attempt=2, delay_minutes=60, session_date="2026-05-25"
    )
    assert unit == "scoop-watch-ofdft-retry-2"
    cmd = captured["cmd"]
    assert cmd[:2] == ["systemd-run", "--user"]
    assert "--on-active=60min" in cmd
    assert "--unit=scoop-watch-ofdft-retry-2" in cmd
    # The transient unit runs the shim with retry-attempt AND session-date,
    # so the retry's log appends to the same per-session file.
    assert cmd[-5:] == [
        "/x/scoop-watch",
        "run",
        "ofdft",
        "--retry-attempt=2",
        "--session-date=2026-05-25",
    ]


def test_schedule_retry_returns_empty_string_on_systemd_run_failure(monkeypatch):
    """If `systemd-run` is missing or rejects the request, the function
    returns an empty string rather than raising, so the abort path always
    completes (the CLI then degrades to a 'rerun manually' hint)."""
    import subprocess

    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            [], returncode=1, stdout="", stderr="boom"
        ),
    )
    monkeypatch.setattr(scheduler.paths, "shim_path", lambda: "/x/scoop-watch")
    assert (
        scheduler.schedule_retry(
            "ofdft", attempt=1, delay_minutes=60, session_date="2026-05-25"
        )
        == ""
    )


def test_timer_template_is_reboot_ephemeral():
    text = scheduler._TIMER_TEMPLATE.format(
        project="demo", on_calendar="Mon,Wed *-*-* 07:30:00"
    )
    assert "OnCalendar=Mon,Wed *-*-* 07:30:00" in text
    assert "Persistent=false" in text
