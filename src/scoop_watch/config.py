"""Environment and per-project configuration.

Each project keeps one user-authored file, ``config.yaml`` (arXiv categories
and keyword queries). The schedule and the fetch window are global: they live
in the data-root ``.env`` and are set by `scoop-watch setup`.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

import yaml

from . import paths

SUPPORTED_AGENTS = ("claude", "codex")

# Curated model choices offered by `setup`, per agent. The user can always pick
# "other" and type a custom identifier; whatever is chosen is then validated by
# a trial run against the agent CLI (see models.validate).
MODEL_CHOICES = {
    "claude": ["opus", "sonnet", "haiku"],
    "codex": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"],
}

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# arXiv announces Sunday–Thursday evenings (US Eastern), so new listings land
# Monday–Friday. Default to weekdays, and to 04:00 local — late enough to be
# after the previous evening's announcement batch.
DEFAULT_WEEKDAYS = list(WEEKDAYS[:5])
DEFAULT_TIME = "04:00"
# 30 days is the daily-briefing search window. `scoop-watch deep` covers the
# multi-year span on demand, so the daily run does not need to re-walk a
# 90-day rolling window — that scope is what was driving the longest fetches
# and pushing arXiv toward rate-limiting.
DEFAULT_RECENT_DAYS = 30


def load_env() -> dict[str, str]:
    """Parse the data-root .env file into a dict (missing file -> empty)."""
    env_path = paths.env_file()
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def detect_agent() -> str:
    """Pick an installed agent CLI: claude preferred, codex as fallback."""
    for candidate in SUPPORTED_AGENTS:
        if shutil.which(candidate):
            return candidate
    return SUPPORTED_AGENTS[0]


def agent() -> str:
    """Synthesis agent CLI: explicit .env/env setting, else autodetected."""
    explicit = load_env().get("AGENT") or os.environ.get("WATCH_AGENT")
    return explicit or detect_agent()


def model() -> str | None:
    """Model passed to the agent CLI, or None to use the agent's default."""
    value = load_env().get("MODEL") or os.environ.get("WATCH_MODEL")
    return value or None


def default_run_time() -> str:
    return load_env().get("RUN_TIME", DEFAULT_TIME)


def default_weekdays() -> list[str]:
    """Schedule weekdays from .env (RUN_WEEKDAYS), else Mon–Fri."""
    raw = load_env().get("RUN_WEEKDAYS", "")
    chosen = {day.strip().capitalize() for day in raw.split(",") if day.strip()}
    return [day for day in WEEKDAYS if day in chosen] or list(DEFAULT_WEEKDAYS)


def recent_days() -> int:
    """Fetch window in days from .env (RECENT_DAYS), else 30."""
    raw = load_env().get("RECENT_DAYS", "")
    try:
        days = int(raw)
    except ValueError:
        return DEFAULT_RECENT_DAYS
    return days if days > 0 else DEFAULT_RECENT_DAYS


def load_config(project: str) -> dict[str, Any]:
    """Load a project's user-authored config.yaml with defaults applied."""
    path = paths.config_path(project)
    if not path.is_file():
        raise FileNotFoundError(
            f"no config.yaml for project '{project}' (expected {path})"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not data.get("queries"):
        raise ValueError(f"{path} defines no queries")
    data.setdefault("categories", [])
    return data


def project_description(project: str) -> str:
    path = paths.description_path(project)
    if not path.is_file():
        raise FileNotFoundError(
            f"no project.md for project '{project}' (run `scoop-watch author {project}`)"
        )
    return path.read_text(encoding="utf-8")


def project_layout(project: str) -> str:
    path = paths.layout_path(project)
    if not path.is_file():
        raise FileNotFoundError(
            f"no layout.md for project '{project}' (run `scoop-watch author {project}`)"
        )
    return path.read_text(encoding="utf-8")


def list_projects() -> list[str]:
    """All projects (a directory is a project once it has config.yaml)."""
    root = paths.data_root() / "projects"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "config.yaml").is_file())
