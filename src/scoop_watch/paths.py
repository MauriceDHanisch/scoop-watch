"""Filesystem locations for the program and its user-facing data.

The program lives in a hidden, installer-managed location. Everything the
user reads or edits (project descriptions, briefings, secrets) lives under a
visible data root, by default inside the XDG Documents directory.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from importlib.resources import files
from pathlib import Path

DATA_DIRNAME = "scoop-watch"


def _xdg_documents() -> Path | None:
    """Resolve the localized Documents directory, or None if unavailable."""
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DOCUMENTS"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            candidate = Path(result.stdout.strip())
            if str(candidate) and candidate != Path.home():
                return candidate
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    fallback = Path.home() / "Documents"
    return fallback if fallback.is_dir() else None


def default_data_root() -> Path:
    """The data root before any user override or saved choice."""
    base = _xdg_documents() or Path.home()
    return base / DATA_DIRNAME


def config_dir() -> Path:
    """Hidden directory holding the data-root pointer (not user-facing)."""
    return Path.home() / ".config" / "scoop-watch"


def _datadir_pointer() -> Path:
    return config_dir() / "datadir"


def set_data_root(path: Path) -> None:
    """Persist a custom data-root choice so later runs find it."""
    config_dir().mkdir(parents=True, exist_ok=True)
    _datadir_pointer().write_text(f"{path}\n", encoding="utf-8")


def data_root() -> Path:
    """Visible directory holding project descriptions, briefings and secrets.

    Resolution order: WATCH_DATA_DIR env var, saved pointer file, default.
    """
    override = os.environ.get("WATCH_DATA_DIR")
    if override:
        return Path(override).expanduser()
    pointer = _datadir_pointer()
    if pointer.is_file():
        stored = pointer.read_text(encoding="utf-8").strip()
        if stored:
            return Path(stored).expanduser()
    return default_data_root()


def project_dir(name: str) -> Path:
    return data_root() / "projects" / name


def archive_dir(name: str) -> Path:
    return project_dir(name) / "archive"


def fetch_archive_dir(name: str) -> Path:
    """Per-project directory where raw arXiv fetch results are saved.

    A copy of each day's fetched papers (the JSON list sent to the synthesis
    agent) is written here for debugging and provenance.
    """
    return project_dir(name) / "fetch-archive"


def logs_dir() -> Path:
    """Global logs directory under the data root (one folder for all projects).

    Created on first write. Failed fetches land here as
    ``<project>_<timestamp>.log`` so a missed run leaves a paper trail the user
    can read to decide whether to retry.
    """
    return data_root() / "logs"


def deep_dir(name: str) -> Path:
    """Top-level deep-mode directory, kept separate from the daily run tree.

    Layout:
        deep/
        ├── fetch/<date>/        per-merged-group JSONL files
        ├── batches/<date>/      per-batch synthesis outputs
        └── archive/             final merged surveys

    Consolidating under one ``deep/`` folder keeps the project root readable
    when both daily runs (fetch-archive/, archive/) and deep surveys coexist.
    """
    return project_dir(name) / "deep"


def deep_fetch_dir(name: str, date: str) -> Path:
    """One directory per deep-mode run, holding per-merged-group JSONL files.

    Per-group files (rather than one big partial JSONL) make resume trivial:
    the presence of ``<group>.jsonl`` means that group is done and its papers
    are persisted, so the next run skips its HTTP request entirely.
    """
    return deep_dir(name) / "fetch" / date


def deep_batches_dir(name: str, date: str) -> Path:
    """One directory per deep-mode run, holding per-batch synthesis outputs.

    A ``batch_NN.md`` file is written as soon as the agent call for batch N
    returns; resume only fires the missing batches.
    """
    return deep_dir(name) / "batches" / date


def deep_archive_dir(name: str) -> Path:
    """Final merged deep-survey output, separate from the daily archive."""
    return deep_dir(name) / "archive"


def read_json_path(name: str) -> Path:
    """Per-project list of arXiv ids the user has marked as read.

    Stored as JSON. The base arXiv id (version suffix removed) is the dedup
    key, so a later revision of an already-read paper is filtered too.
    """
    return project_dir(name) / "read.json"


def next_version_stem(name: str, today: dt.date | None = None) -> str:
    """Today's filename stem, suffixed ``-v2`` / ``-v3`` / ... on rerun.

    A run writes two files keyed by the same stem: ``archive/<stem>.md`` and
    ``fetch-archive/<stem>.json``. If either is already on disk for today,
    the next free ``-vN`` suffix is returned so a same-day rerun preserves
    the earlier outputs instead of overwriting them.
    """
    date = (today or dt.date.today()).isoformat()
    archive = archive_dir(name)
    fetch_archive = fetch_archive_dir(name)

    def used(stem: str) -> bool:
        return (archive / f"{stem}.md").exists() or (
            fetch_archive / f"{stem}.json"
        ).exists()

    if not used(date):
        return date
    version = 2
    while used(f"{date}-v{version}"):
        version += 1
    return f"{date}-v{version}"


def config_path(name: str) -> Path:
    """User-authored project config: arXiv categories and keyword queries."""
    return project_dir(name) / "config.yaml"


def layout_path(name: str) -> Path:
    return project_dir(name) / "layout.md"


def description_path(name: str) -> Path:
    return project_dir(name) / "project.md"


def env_file() -> Path:
    return data_root() / ".env"


def systemd_user_dir() -> Path:
    """Linux systemd user-unit directory (one of the user-search-paths)."""
    return Path.home() / ".config" / "systemd" / "user"


def launchd_plist_dir() -> Path:
    """Where macOS LaunchAgent plists are stored on disk.

    Deliberately NOT ``~/Library/LaunchAgents``: launchd auto-loads anything in
    that directory at login, which would defeat the reboot-ephemeral semantic
    we want to match Linux's `systemctl --user start` (no enable). Putting the
    plist under the data root means it survives reboot as a serialized
    schedule but only takes effect when the user runs ``scoop-watch arm``,
    which bootstraps it into ``gui/$UID`` for the current session.
    """
    return data_root() / ".scheduler"


def shim_path() -> Path:
    return Path.home() / ".local" / "bin" / "scoop-watch"


def app_install_dir() -> Path:
    """Where the installer places the program (used by `scoop-watch update`)."""
    return Path.home() / ".local" / "share" / "scoop-watch"


def package_text(filename: str) -> str:
    """Read a text resource shipped inside the package."""
    return (files("scoop_watch") / filename).read_text(encoding="utf-8")


def template_dir() -> Path:
    """Directory of per-project template files (config.yaml, layout.md, ...)."""
    return Path(str(files("scoop_watch") / "templates"))
