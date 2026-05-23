"""Platform-dispatching front end for the scheduler.

The actual unit/plist writing and CLI calls live in ``scheduler_linux`` and
``scheduler_macos``. This module picks the right backend at call time so the
rest of the codebase can stay platform-agnostic.

Reboot-ephemeral by design on both platforms: the timer (Linux) or LaunchAgent
(macOS) lives only for the current login session, so a reboot forces a
deliberate ``scoop-watch arm`` to resume.
"""

from __future__ import annotations

import platform
from types import ModuleType


def _backend() -> ModuleType:
    """Pick the scheduler backend module for the current OS.

    Resolved lazily and per call so a test can monkeypatch ``platform.system``
    and exercise either backend on either host.
    """
    system = platform.system()
    if system == "Linux":
        from . import scheduler_linux

        return scheduler_linux
    if system == "Darwin":
        from . import scheduler_macos

        return scheduler_macos
    raise RuntimeError(
        f"scoop-watch scheduling is not supported on {system}; "
        "run `scoop-watch run` manually instead"
    )


def arm(project: str, weekdays: list[str], time: str) -> None:
    _backend().arm(project, weekdays, time)


def disarm(project: str) -> None:
    _backend().disarm(project)


def is_armed(project: str) -> bool:
    return _backend().is_armed(project)


def timer_line(project: str) -> str:
    return _backend().timer_line(project)


def last_run_line(project: str) -> str:
    return _backend().last_run_line(project)
