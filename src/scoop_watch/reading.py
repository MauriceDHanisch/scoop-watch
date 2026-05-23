"""Per-project tracking of papers the user has already read.

Each entry in ``<project>/read.json`` is an object with the base arXiv id
(version suffix removed), the title, and the date the entry was added. The
base id is the deduplication key, so a later revision (v2, v3, ...) of an
already-read paper is still filtered out on the next run.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from . import paths

_VERSION_SUFFIX = re.compile(r"v\d+$")


def strip_version(arxiv_id: str) -> str:
    """Drop the trailing ``vN`` from an arXiv id (``2602.20232v2`` -> ``2602.20232``)."""
    return _VERSION_SUFFIX.sub("", arxiv_id)


def _load(project: str) -> list[dict[str, Any]]:
    path = paths.read_json_path(project)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def read_ids(project: str) -> set[str]:
    """Set of base arXiv ids the user has marked as read for this project."""
    return {entry["arxiv_id"] for entry in _load(project)}


def add_read(project: str, papers: list[dict[str, Any]]) -> int:
    """Append papers to the project's read.json (deduped by base id).

    Returns the number of entries newly added.
    """
    existing = _load(project)
    seen = {entry["arxiv_id"] for entry in existing}
    today = dt.date.today().isoformat()
    added = 0
    for paper in papers:
        base = strip_version(paper["arxiv_id"])
        if base in seen:
            continue
        existing.append(
            {
                "arxiv_id": base,
                "title": paper.get("title", ""),
                "marked_read": today,
            }
        )
        seen.add(base)
        added += 1
    _save(project, existing)
    return added


def _save(project: str, entries: list[dict[str, Any]]) -> None:
    path = paths.read_json_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def reconcile_read(
    project: str,
    presented: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> tuple[int, int]:
    """Set the read state for the ``presented`` papers to exactly ``selected``.

    Anything in ``presented`` that is **not** in ``selected`` is removed from
    read.json (un-read). Anything in ``selected`` that isn't already in
    read.json is appended. Entries in read.json that aren't in ``presented``
    at all are left untouched, so old read history survives a picker that
    only shows today's fetch.

    Returns ``(added, removed)``.
    """
    presented_keys = {strip_version(p["arxiv_id"]) for p in presented}
    selected_keys = {strip_version(p["arxiv_id"]) for p in selected}

    existing = _load(project)
    existing_keys = {entry["arxiv_id"] for entry in existing}

    # Drop entries whose paper was presented but not ticked (un-read).
    kept = [
        entry
        for entry in existing
        if entry["arxiv_id"] not in presented_keys or entry["arxiv_id"] in selected_keys
    ]

    # Add presented-and-ticked papers that aren't already in read.json.
    today = dt.date.today().isoformat()
    have = {entry["arxiv_id"] for entry in kept}
    for paper in selected:
        base = strip_version(paper["arxiv_id"])
        if base in have:
            continue
        kept.append(
            {"arxiv_id": base, "title": paper.get("title", ""), "marked_read": today}
        )
        have.add(base)

    _save(project, kept)
    added = len(selected_keys - existing_keys)
    removed = len((presented_keys & existing_keys) - selected_keys)
    return added, removed
