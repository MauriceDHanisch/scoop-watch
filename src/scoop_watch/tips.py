"""Per-command rotating tip pool.

Each command has its own short list of useful tips. ``next(command)`` returns
the next tip in that command's pool and persists the index, so consecutive
invocations of the same command surface different tips. State lives in a tiny
file under the program's config dir; failures to read or write it are
non-fatal (the tip just resets to the first one).
"""

from __future__ import annotations

from . import paths

_POOLS: dict[str, list[str]] = {
    "run": [
        "'scoop-watch read' to mark papers you've read so they defer next run.",
        "Same-day reruns are versioned (-v2, -v3, ...) so you never lose a briefing.",
        "The fetch-archive/ folder keeps the raw arXiv JSON behind every briefing.",
        "'scoop-watch status' shows when the next scheduled run is.",
        "Already-read papers appear in a collapsed appendix at the end of the briefing.",
        "'scoop-watch deep <project>' for a multi-year survey of overlapping work.",
    ],
    "arm": [
        "'scoop-watch run' to generate a briefing right now.",
        "Schedule comes from 'scoop-watch setup'; change it with --reconfigure.",
        "The timer stops at next reboot; re-run 'scoop-watch arm' to resume.",
        "'scoop-watch status' shows the next firing time.",
    ],
    "disarm": [
        "'scoop-watch arm' to schedule it again.",
        "'scoop-watch run' for a one-off briefing without scheduling.",
    ],
    "read": [
        "Read papers move to the briefing's 'Already read' appendix on next run.",
        "Untick a previously-read paper in the picker to un-read it.",
        "Matching is version-agnostic: marking 2602.20232v1 also hides v2.",
        "Edit read.json by hand to drop entries you want to surface again.",
    ],
    "new": [
        "'scoop-watch author <name>' to write project.md/layout.md with an agent.",
        "Or edit project.md and config.yaml by hand before running.",
    ],
    "status": [
        "'scoop-watch run' for a fresh briefing now.",
        "'scoop-watch read' to defer papers you've already read.",
        "'scoop-watch setup' to change the schedule or search window (Enter to keep).",
        "'scoop-watch deep <project>' for a once-a-year 5-year overlap survey.",
    ],
    "update": [
        "'scoop-watch run' to produce a fresh briefing with the new version.",
        "'scoop-watch update --check' just looks; it does not install.",
    ],
    "setup": [
        "Then 'scoop-watch author' to create your first project.",
        "Re-run 'scoop-watch setup' any time — current values are pre-filled defaults.",
        "After your first daily run, try 'scoop-watch deep <project>' for a 5-year survey.",
    ],
    "resynth": [
        "Useful when arXiv is rate-limited or you tweaked layout.md / project.md.",
        "The fetch is not re-done; only the agent runs.",
        "Pass --date YYYY-MM-DD to resynth a specific day's fetch.",
        "Resynths still produce a new -vN, never overwrite previous briefings.",
    ],
    "deep": [
        "Re-run with --force to re-synthesize without re-fetching.",
        "Per-merged-group JSONL is in deep/fetch/<date>/ — inspect or replay it freely.",
        "Batches checkpoint to deep/batches/<date>/ — a 429 mid-run loses only one.",
        "Use --years to widen or narrow the window (default 5).",
        "Default batches and concurrency are tuned for Claude Opus; --parallel adjusts.",
    ],
}


def _index_path() -> "paths.Path":
    return paths.config_dir() / "tip_index"


def _load_state() -> dict[str, int]:
    path = _index_path()
    if not path.is_file():
        return {}
    state: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        try:
            state[key.strip()] = int(value.strip())
        except ValueError:
            continue
    return state


def _save_state(state: dict[str, int]) -> None:
    path = _index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(f"{k}={v}" for k, v in sorted(state.items())) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # best-effort; missing tip persistence is harmless


def next(command: str) -> str | None:
    """Return the next tip for ``command`` and advance the index.

    Returns ``None`` if the command has no tip pool (caller skips the hint).
    """
    pool = _POOLS.get(command)
    if not pool:
        return None
    state = _load_state()
    idx = state.get(command, 0) % len(pool)
    tip = pool[idx]
    state[command] = (idx + 1) % len(pool)
    _save_state(state)
    return tip
