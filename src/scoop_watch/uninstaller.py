"""Remove scoop-watch from the system."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass(frozen=True)
class RemovedUnit:
    """A systemd unit removed during uninstall.

    ``was_active`` records whether the unit was running when it was stopped,
    so the caller can report that a live timer was killed.
    """

    name: str
    was_active: bool


@dataclass(frozen=True)
class UninstallResult:
    timers: list[RemovedUnit]
    data_root: Path
    data_removed: bool


def _is_active(unit: str) -> bool:
    """True when systemd reports the unit as currently running."""
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "active"


def _remove_timers() -> list[RemovedUnit]:
    """Stop and delete every scoop-watch systemd user unit."""
    unit_dir = paths.systemd_user_dir()
    if not unit_dir.is_dir():
        return []
    units = sorted(unit_dir.glob("scoop-watch-*.timer")) + sorted(
        unit_dir.glob("scoop-watch-*.service")
    )
    removed: list[RemovedUnit] = []
    for unit in units:
        was_active = _is_active(unit.name)
        subprocess.run(["systemctl", "--user", "stop", unit.name], capture_output=True)
        unit.unlink(missing_ok=True)
        removed.append(RemovedUnit(name=unit.name, was_active=was_active))
    if removed:
        # daemon-reload drops the deleted units; reset-failed clears any
        # lingering failed state so nothing about scoop-watch survives.
        names = [unit.name for unit in removed]
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(
            ["systemctl", "--user", "reset-failed", *names], capture_output=True
        )
    return removed


def uninstall(remove_data: bool) -> UninstallResult:
    """Remove timers, the program and the command. Optionally remove data."""
    timers = _remove_timers()
    data_root = paths.data_root()

    shutil.rmtree(paths.app_install_dir(), ignore_errors=True)
    paths.shim_path().unlink(missing_ok=True)

    if remove_data:
        shutil.rmtree(data_root, ignore_errors=True)
        shutil.rmtree(paths.config_dir(), ignore_errors=True)

    return UninstallResult(timers=timers, data_root=data_root, data_removed=remove_data)
