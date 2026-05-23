"""Self-update against the GitHub repository.

The installer records the commit it installed. `update` compares that against
the current HEAD of the default branch and, when behind, re-runs the installer.
`update_notice` surfaces a one-line hint, backed by a once-a-day cache.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request

from . import paths

REPO = "MauriceDHanisch/scoop-watch"
BRANCH = "main"

_API_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
_INSTALL_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/install.sh"
_CHECK_TTL = 86400  # re-check GitHub at most once a day


def installed_sha() -> str | None:
    """The commit recorded by the installer, or None if unknown."""
    marker = paths.app_install_dir() / ".installed-sha"
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def _parse_sha(payload: str) -> str:
    return json.loads(payload)["sha"]


def latest_sha(timeout: float = 10.0) -> str:
    """The current HEAD commit of the repository's default branch."""
    request = urllib.request.Request(
        _API_URL,
        headers={
            "User-Agent": "scoop-watch",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _parse_sha(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        raise RuntimeError(
            f"could not reach GitHub to check for updates: {error}"
        ) from error


def run_installer() -> None:
    """Re-run the installer, which updates the program in place."""
    result = subprocess.run(["bash", "-c", f"curl -fsSL {_INSTALL_URL} | bash"])
    if result.returncode != 0:
        raise RuntimeError("the installer exited with an error")


def _cached_latest_sha() -> str | None:
    """Latest sha from a daily cache, refreshing it when stale. Never raises."""
    cache = paths.app_install_dir() / ".update-check"
    now = time.time()
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if now - float(data["checked_at"]) < _CHECK_TTL:
                return str(data["latest_sha"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        latest = latest_sha(timeout=4.0)
    except RuntimeError:
        return None
    try:
        cache.write_text(
            json.dumps({"checked_at": now, "latest_sha": latest}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return latest


def update_notice() -> str | None:
    """One-line notice when a newer version exists, else None. Never raises."""
    installed = installed_sha()
    if not installed:
        return None
    latest = _cached_latest_sha()
    if latest and latest != installed:
        return "A new version of scoop-watch is available — run `scoop-watch update`."
    return None
