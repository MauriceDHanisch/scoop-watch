"""Tests for fetch.fetch_deep: per-merged-group JSONL + resume on existing files."""

from __future__ import annotations

import datetime as dt
import json

from scoop_watch import fetch


def _fake_paper_dict(paper_id: str) -> dict:
    return dict(
        arxiv_id=paper_id,
        title=f"Title {paper_id}",
        authors=["A. Researcher"],
        abstract="Abstract text.",
        submitted="2026-05-20",
        categories=["cs.LG"],
        primary_category="cs.LG",
        url=f"http://arxiv.org/abs/{paper_id}",
        matched_queries=["fake (any)"],
    )


class _FakeAuthor:
    def __init__(self, name):
        self.name = name


class _FakeResult:
    def __init__(self, paper_id, categories):
        self._paper_id = paper_id
        self.categories = categories
        self.title = f"Title {paper_id}"
        self.authors = [_FakeAuthor("A. Researcher")]
        self.summary = "Abstract text."
        self.published = dt.datetime(2026, 5, 20, 12, 0)
        self.primary_category = categories[0]
        self.entry_id = f"http://arxiv.org/abs/{paper_id}"

    def get_short_id(self):
        return self._paper_id


class _FakeClient:
    results_to_yield: list[_FakeResult] = []

    def __init__(self, *args, **kwargs):
        pass

    def results(self, search):
        yield from _FakeClient.results_to_yield


def test_group_slug_filesystem_safe():
    """Slug stays alnum + hyphen + underscore — safe to use as a filename."""
    assert fetch._group_slug("neural operator (any)") == "neural_operator__any"
    assert fetch._group_slug("Fourier neural operator") == "Fourier_neural_operator"
    assert fetch._group_slug("operator_learning") == "operator_learning"


def test_fetch_deep_writes_one_jsonl_per_merged_group(tmp_path, monkeypatch):
    """One JSONL file per merged group lands in out_dir, each line a paper."""
    _FakeClient.results_to_yield = [
        _FakeResult("2605.00001", ["cs.LG"]),
        _FakeResult("2605.00002", ["physics.chem-ph"]),
    ]
    monkeypatch.setattr(fetch.arxiv, "Client", _FakeClient)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    out = tmp_path / "deep-fetch"
    papers = fetch.fetch_deep(
        [{"name": "Q1", "operator": "OR", "terms": ["x"]}],
        days=1825,
        out_dir=out,
    )

    assert len(papers) == 2
    files = list(out.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["arxiv_id"] in {"2605.00001", "2605.00002"}


def test_fetch_deep_skips_groups_whose_jsonl_already_exists(tmp_path, monkeypatch):
    """Resume: a pre-existing JSONL means that group was already fetched, so
    fetch_deep skips its HTTP request and absorbs the cached papers verbatim."""
    out = tmp_path / "deep-fetch"
    out.mkdir(parents=True)
    cached = out / f"{fetch._group_slug('Q1')}.jsonl"
    cached.write_text(json.dumps(_fake_paper_dict("2401.00001")) + "\n", encoding="utf-8")

    fired: list[str] = []

    class _ShouldNotFire:
        def __init__(self, *a, **k):
            pass

        def results(self, search):
            fired.append("unexpected HTTP call")
            return iter([])

    monkeypatch.setattr(fetch.arxiv, "Client", _ShouldNotFire)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    papers = fetch.fetch_deep(
        [{"name": "Q1", "operator": "OR", "terms": ["x"]}],
        days=1825,
        out_dir=out,
    )

    assert fired == [], "resume must skip the HTTP request when the JSONL exists"
    assert len(papers) == 1
    assert papers[0].arxiv_id == "2401.00001"


def test_fetch_deep_resume_picks_up_after_partial_failure(tmp_path, monkeypatch):
    """Half a fetch on disk + the rest succeeds on the resume run = full union.

    Simulates: first run fetches Q1, 429s on Q2 (file never written), second
    run skips Q1 (cached), fetches Q2 successfully.
    """
    out = tmp_path / "deep-fetch"
    out.mkdir(parents=True)
    # Q1 already fetched (cached from a prior run).
    (out / "Q1.jsonl").write_text(
        json.dumps(_fake_paper_dict("2401.00001")) + "\n", encoding="utf-8"
    )

    # Q2 returns a new paper on the resume run.
    _FakeClient.results_to_yield = [_FakeResult("2401.00002", ["cs.LG"])]
    monkeypatch.setattr(fetch.arxiv, "Client", _FakeClient)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    papers = fetch.fetch_deep(
        [
            {"name": "Q1", "operator": "OR", "terms": ["x"]},
            {"name": "Q2", "operator": "OR", "terms": ["y"]},
        ],
        days=1825,
        out_dir=out,
    )

    assert sorted(p.arxiv_id for p in papers) == ["2401.00001", "2401.00002"]
    assert (out / "Q2.jsonl").is_file(), "the freshly-fetched group is persisted"


def test_fetch_deep_writes_atomically_via_tmp_rename(tmp_path, monkeypatch):
    """A SIGKILL mid-write must never leave a half-formed .jsonl on disk: the
    fetch writes to .jsonl.tmp and renames on success, so the only outcomes
    are 'file absent' or 'file complete'."""
    _FakeClient.results_to_yield = [_FakeResult("2605.00001", ["cs.LG"])]
    monkeypatch.setattr(fetch.arxiv, "Client", _FakeClient)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    out = tmp_path / "deep-fetch"
    fetch.fetch_deep(
        [{"name": "G", "operator": "OR", "terms": ["x"]}],
        days=1825,
        out_dir=out,
    )

    # The final .jsonl exists; no .jsonl.tmp remains.
    assert (out / "G.jsonl").is_file()
    assert not list(out.glob("*.jsonl.tmp"))
