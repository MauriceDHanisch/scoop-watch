"""Reboot-ephemeral systemd user timer management.

The timer is started but never enabled: it runs on its schedule for the
current uptime and is gone after a reboot. Resuming is a deliberate
`scoop-watch arm`.
"""

from __future__ import annotations

import re
import subprocess

from . import config, paths

_SERVICE_TEMPLATE = """[Unit]
Description=scoop-watch briefing for {project}

[Service]
Type=oneshot
ExecStart={shim} run {project}
"""

_TIMER_TEMPLATE = """[Unit]
Description=scoop-watch daily timer for {project}

[Timer]
OnCalendar={on_calendar}
Persistent=false

[Install]
WantedBy=timers.target
"""


def on_calendar(weekdays: list[str], time: str) -> str:
    """Build a systemd OnCalendar expression for the given weekdays and time."""
    selected = [day for day in config.WEEKDAYS if day in weekdays]
    if not selected or len(selected) == len(config.WEEKDAYS):
        return f"*-*-* {time}:00"
    return f"{','.join(selected)} *-*-* {time}:00"


def _unit_stem(project: str) -> str:
    return f"scoop-watch-{project}"


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
    )


def arm(project: str, weekdays: list[str], time: str) -> None:
    """Write the units and start (not enable) the timer for this uptime."""
    unit_dir = paths.systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    stem = _unit_stem(project)

    (unit_dir / f"{stem}.service").write_text(
        _SERVICE_TEMPLATE.format(project=project, shim=paths.shim_path()),
        encoding="utf-8",
    )
    (unit_dir / f"{stem}.timer").write_text(
        _TIMER_TEMPLATE.format(project=project, on_calendar=on_calendar(weekdays, time)),
        encoding="utf-8",
    )

    _systemctl("daemon-reload")
    started = _systemctl("start", f"{stem}.timer")
    if started.returncode != 0:
        raise RuntimeError(f"failed to start timer: {started.stderr.strip()}")


def disarm(project: str) -> None:
    """Stop the timer, remove its unit files, and clear any failed state."""
    stem = _unit_stem(project)
    _systemctl("stop", f"{stem}.timer")
    for suffix in (".timer", ".service"):
        (paths.systemd_user_dir() / f"{stem}{suffix}").unlink(missing_ok=True)
    _systemctl("daemon-reload")
    _systemctl("reset-failed", f"{stem}.timer", f"{stem}.service")


def is_armed(project: str) -> bool:
    result = _systemctl("is-active", f"{_unit_stem(project)}.timer")
    return result.stdout.strip() == "active"


def _parse_list_timers(line: str) -> dict[str, str]:
    """Split one `systemctl list-timers --no-legend` row into named columns.

    Columns are NEXT, LEFT, LAST, PASSED, UNIT, ACTIVATES. systemctl pads
    them with two-or-more spaces; the values themselves only contain single
    spaces (e.g. "Mon 2026-05-25 04:00:00 PDT"), so splitting on `\\s{2,}` is
    unambiguous.
    """
    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) < 6:
        return {}
    keys = ("next", "left", "last", "passed", "unit", "activates")
    return dict(zip(keys, parts))


def _list_timer(project: str) -> dict[str, str]:
    result = _systemctl(
        "list-timers", "--no-pager", "--no-legend", f"{_unit_stem(project)}.timer"
    )
    return _parse_list_timers(result.stdout.strip())


def timer_line(project: str) -> str:
    """One-line next-run summary, or a hint if the timer is disarmed."""
    if not is_armed(project):
        return "not armed (run `scoop-watch arm` to schedule)"
    parsed = _list_timer(project)
    next_run = parsed.get("next", "").strip()
    if not next_run:
        return "armed"
    left = parsed.get("left", "").removesuffix(" left").strip()
    return f"{next_run} (in {left})" if left else next_run


def last_run_line(project: str) -> str:
    """One-line summary of the last firing of a project's timer."""
    if not is_armed(project):
        return ""
    parsed = _list_timer(project)
    last = parsed.get("last", "").strip()
    if not last or last == "n/a":
        return "never (just armed)"
    passed = parsed.get("passed", "").strip()
    return f"{last} ({passed} ago)" if passed and passed != "n/a" else last


def schedule_retry(
    project: str, attempt: int, delay_minutes: int, session_date: str
) -> str:
    """Schedule a one-shot retry via ``systemd-run --user --on-active=Nmin``.

    The retry command embeds ``--retry-attempt=N`` (so it knows whether it
    still has retries left) and ``--session-date=YYYY-MM-DD`` (so its log
    appends to the same per-session file as the initial attempt). Returns
    the transient unit name on success, empty string on failure.
    """
    unit = f"scoop-watch-{project}-retry-{attempt}"
    shim = paths.shim_path()
    cmd = [
        "systemd-run",
        "--user",
        f"--on-active={delay_minutes}min",
        f"--unit={unit}",
        f"--description=scoop-watch retry {attempt} for {project}",
        str(shim),
        "run",
        project,
        f"--retry-attempt={attempt}",
        f"--session-date={session_date}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return unit
