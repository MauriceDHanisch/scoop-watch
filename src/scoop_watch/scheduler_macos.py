"""launchd LaunchAgent management — macOS counterpart to scheduler_linux.

Reboot-ephemeral by design: agents are bootstrapped into ``gui/$UID`` (the
current user's Aqua session) rather than dropped into ``~/Library/LaunchAgents``
permanently, so logout or reboot drops them. Resuming is a deliberate
``scoop-watch arm``, matching the Linux semantics.

launchctl exposes no direct "next firing" reading, so ``timer_line`` computes
the next-run timestamp from the schedule client-side; ``last_run_line`` parses
``last exit code`` out of ``launchctl print`` and is best-effort.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from pathlib import Path

from . import config, paths

_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{shim}</string>
    <string>run</string>
    <string>{project}</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{calendar_entries}
  </array>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{log_path}</string>
  <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""

# launchd uses Sunday=0..Saturday=6 for the Weekday key in
# StartCalendarInterval. Matches Apple's launchd.plist(5).
_WEEKDAY_TO_LAUNCHD = {
    "Sun": 0,
    "Mon": 1,
    "Tue": 2,
    "Wed": 3,
    "Thu": 4,
    "Fri": 5,
    "Sat": 6,
}


def _label(project: str) -> str:
    return f"com.scoop-watch.{project}"


def _plist_path(project: str) -> Path:
    return paths.launchd_agent_dir() / f"{_label(project)}.plist"


def _domain_target() -> str:
    return f"gui/{os.getuid()}"


def _service_target(project: str) -> str:
    return f"{_domain_target()}/{_label(project)}"


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
    )


def _calendar_entries(weekdays: list[str], time: str) -> str:
    """Render the ``StartCalendarInterval`` array body.

    One dict per selected weekday. An empty selection is treated as every day
    (mirrors ``scheduler_linux.on_calendar``).
    """
    hour_str, minute_str = time.split(":", 1)
    hour, minute = int(hour_str), int(minute_str)
    days = [day for day in config.WEEKDAYS if day in weekdays] or list(config.WEEKDAYS)
    blocks = []
    for day in days:
        blocks.append(
            "    <dict>\n"
            f"      <key>Weekday</key><integer>{_WEEKDAY_TO_LAUNCHD[day]}</integer>\n"
            f"      <key>Hour</key><integer>{hour}</integer>\n"
            f"      <key>Minute</key><integer>{minute}</integer>\n"
            "    </dict>"
        )
    return "\n".join(blocks)


def _next_firing(
    weekdays: list[str], time: str, now: dt.datetime | None = None
) -> dt.datetime:
    """Compute the next datetime matching any of ``weekdays`` at ``time``."""
    now = now or dt.datetime.now()
    hour_str, minute_str = time.split(":", 1)
    hour, minute = int(hour_str), int(minute_str)
    days = [day for day in config.WEEKDAYS if day in weekdays] or list(config.WEEKDAYS)
    # WEEKDAYS uses Mon..Sun (Python's weekday() is Mon=0..Sun=6).
    target_weekdays = {list(config.WEEKDAYS).index(day) for day in days}
    for delta in range(0, 8):  # today + 7 future days covers any weekly schedule
        candidate = (now + dt.timedelta(days=delta)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            continue
        if candidate.weekday() in target_weekdays:
            return candidate
    raise RuntimeError("could not compute next firing")  # unreachable


def arm(project: str, weekdays: list[str], time: str) -> None:
    """Write the LaunchAgent plist and bootstrap it into the user GUI session."""
    plist_dir = paths.launchd_agent_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.logs_dir() / f"{project}_launchd.log"
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    plist_file = _plist_path(project)
    plist_file.write_text(
        _PLIST_TEMPLATE.format(
            label=_label(project),
            shim=paths.shim_path(),
            project=project,
            calendar_entries=_calendar_entries(weekdays, time),
            log_path=log_path,
        ),
        encoding="utf-8",
    )

    # Bootout any prior copy so bootstrap doesn't fail with "service already
    # bootstrapped" — launchctl returns 5 (EBUSY) in that case.
    _launchctl("bootout", _service_target(project))
    result = _launchctl("bootstrap", _domain_target(), str(plist_file))
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to bootstrap LaunchAgent: {result.stderr.strip() or result.stdout.strip()}"
        )


def disarm(project: str) -> None:
    """Bootout the LaunchAgent and remove its plist."""
    _launchctl("bootout", _service_target(project))
    _plist_path(project).unlink(missing_ok=True)


def is_armed(project: str) -> bool:
    result = _launchctl("print", _service_target(project))
    return result.returncode == 0


def _read_calendar_from_plist(project: str) -> tuple[list[str], str] | None:
    """Recover (weekdays, HH:MM) from the plist on disk.

    Used to compute next-firing; cheaper than asking launchctl. Returns None
    if the plist is gone (disarmed) or doesn't parse.
    """
    plist = _plist_path(project)
    if not plist.is_file():
        return None
    text = plist.read_text(encoding="utf-8")
    weekday_to_name = {v: k for k, v in _WEEKDAY_TO_LAUNCHD.items()}
    weekdays = sorted(
        {
            weekday_to_name[int(m)]
            for m in re.findall(r"<key>Weekday</key><integer>(\d+)</integer>", text)
        },
        key=list(config.WEEKDAYS).index,
    )
    hours = re.findall(r"<key>Hour</key><integer>(\d+)</integer>", text)
    minutes = re.findall(r"<key>Minute</key><integer>(\d+)</integer>", text)
    if not hours or not minutes:
        return None
    return weekdays, f"{int(hours[0]):02d}:{int(minutes[0]):02d}"


def timer_line(project: str) -> str:
    """One-line next-run summary, computed client-side from the plist schedule."""
    if not is_armed(project):
        return "not armed (run `scoop-watch arm` to schedule)"
    schedule = _read_calendar_from_plist(project)
    if schedule is None:
        return "armed"
    weekdays, time = schedule
    nxt = _next_firing(weekdays, time)
    delta = nxt - dt.datetime.now()
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes = remainder // 60
    if hours >= 24:
        left = f"{hours // 24}d {hours % 24}h"
    elif hours:
        left = f"{hours}h {minutes}m"
    else:
        left = f"{minutes}m"
    return f"{nxt:%a %Y-%m-%d %H:%M} (in {left})"


def last_run_line(project: str) -> str:
    """Best-effort last-run line; parses launchctl print's `last exit code`."""
    if not is_armed(project):
        return ""
    result = _launchctl("print", _service_target(project))
    match = re.search(r"last exit code\s*=\s*(\S+)", result.stdout)
    if not match or match.group(1) == "(none)":
        return "never (just armed)"
    return f"last exit code {match.group(1)}"
