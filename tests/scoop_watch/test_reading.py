"""Tests for the per-project read-tracking store."""

import json

from scoop_watch import reading


def test_strip_version_removes_trailing_version_only():
    assert reading.strip_version("2602.20232v1") == "2602.20232"
    assert reading.strip_version("2602.20232v17") == "2602.20232"
    assert reading.strip_version("2602.20232") == "2602.20232"


def test_read_ids_returns_empty_set_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert reading.read_ids("demo") == set()


def test_add_read_dedupes_across_versions(monkeypatch, tmp_path):
    """v1 and v2 of the same paper collapse to a single entry."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))

    added_v1 = reading.add_read("demo", [{"arxiv_id": "2602.20232v1", "title": "Paper"}])
    added_v2 = reading.add_read(
        "demo", [{"arxiv_id": "2602.20232v2", "title": "Paper revised"}]
    )

    assert added_v1 == 1
    assert added_v2 == 0  # v2 collapses to the v1 entry's base id
    assert reading.read_ids("demo") == {"2602.20232"}


def test_add_read_persists_to_json(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    reading.add_read(
        "demo",
        [
            {"arxiv_id": "2602.20232v1", "title": "First"},
            {"arxiv_id": "2604.07623", "title": "Second"},
        ],
    )
    path = tmp_path / "projects" / "demo" / "read.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert [e["arxiv_id"] for e in entries] == ["2602.20232", "2604.07623"]
    assert all("marked_read" in e for e in entries)


def test_reconcile_read_adds_unread_and_removes_unticked(monkeypatch, tmp_path):
    """The picker shows the current fetch; ticked = read, unticked = unread.
    Entries unrelated to the fetch survive untouched."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))

    # Pre-existing: A read, B read, C read (C is unrelated to the upcoming fetch).
    reading.add_read(
        "demo",
        [
            {"arxiv_id": "A", "title": "alpha"},
            {"arxiv_id": "B", "title": "beta"},
            {"arxiv_id": "C", "title": "gamma"},
        ],
    )

    # Picker shows A, B, D. User ticks A and D; unticks B (it was pre-ticked).
    presented = [
        {"arxiv_id": "A", "title": "alpha"},
        {"arxiv_id": "B", "title": "beta"},
        {"arxiv_id": "D", "title": "delta"},
    ]
    selected = [
        {"arxiv_id": "A", "title": "alpha"},
        {"arxiv_id": "D", "title": "delta"},
    ]

    added, removed = reading.reconcile_read("demo", presented, selected)

    assert added == 1  # D
    assert removed == 1  # B
    assert reading.read_ids("demo") == {"A", "C", "D"}  # C survived; B is gone


def test_reconcile_read_with_empty_selection_unreads_all_presented(monkeypatch, tmp_path):
    """An empty selection (everything unticked) un-reads every presented paper,
    but unrelated read entries stay."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    reading.add_read(
        "demo",
        [
            {"arxiv_id": "A", "title": "alpha"},
            {"arxiv_id": "B", "title": "beta"},
            {"arxiv_id": "OLD", "title": "old paper not in fetch"},
        ],
    )

    presented = [{"arxiv_id": "A", "title": "a"}, {"arxiv_id": "B", "title": "b"}]
    added, removed = reading.reconcile_read("demo", presented, selected=[])

    assert (added, removed) == (0, 2)
    assert reading.read_ids("demo") == {"OLD"}
