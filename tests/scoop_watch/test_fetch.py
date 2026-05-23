"""Tests for arXiv query construction and result handling."""

import datetime as dt

import arxiv

from scoop_watch import fetch


def test_term_clause_plain():
    assert fetch._term_clause("FNO") == "all:FNO"


def test_term_clause_phrase_is_quoted():
    assert fetch._term_clause("neural operator") == 'all:"neural operator"'


def test_term_clause_hyphenated_term_is_quoted():
    """Regression: an unquoted hyphenated term is mangled by the arXiv API
    into a loose match that returns mostly irrelevant papers."""
    assert fetch._term_clause("delta-learning") == 'all:"delta-learning"'
    assert fetch._term_clause("open-shell") == 'all:"open-shell"'


def test_build_query_quotes_hyphenated_terms():
    query = fetch.build_query(["data-efficient", "quantum chemistry"], "AND", 7)
    assert 'all:"data-efficient" AND all:"quantum chemistry"' in query


def test_build_query_joins_with_operator():
    query = fetch.build_query(["a", "b c"], "or", 7)
    assert 'all:a OR all:"b c"' in query
    assert "submittedDate:[" in query


def test_build_query_date_window():
    query = fetch.build_query(["x"], "AND", 0)
    today = dt.date.today().strftime("%Y%m%d")
    assert f"{today}0000 TO {today}2359" in query


def _paper(submitted: str) -> fetch.Paper:
    return fetch.Paper(
        arxiv_id=submitted,
        title="t",
        authors=[],
        abstract="a",
        submitted=submitted,
        categories=[],
        primary_category="c",
        url="u",
    )


def test_archive_papers_tags_each_entry_with_recency_and_queries(tmp_path):
    """archive_papers serializes papers and tags each with its recency tier."""
    import json

    new_paper = _paper("2026-05-22")
    recent_paper = _paper("2026-03-01")
    tiered = fetch.TieredPapers(new=[new_paper], this_week=[], recent=[recent_paper])
    target = tmp_path / "fetch-archive" / "2026-05-22.json"

    fetch.archive_papers(target, tiered)

    assert target.is_file()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    recencies = {entry["arxiv_id"]: entry["recency"] for entry in loaded}
    assert recencies == {"2026-05-22": "new", "2026-03-01": "recent"}
    # `matched_queries` defaults to an empty list and is always serialised.
    assert all("matched_queries" in entry for entry in loaded)


def test_fetch_records_every_query_that_matched_a_paper(monkeypatch):
    """A paper hit by multiple queries records every query name once."""
    _FakeClient.results_to_yield = [_FakeResult("2605.00010", ["cs.LG"])]
    monkeypatch.setattr(fetch.arxiv, "Client", _FakeClient)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    papers = fetch.fetch(
        [
            {"name": "Query A", "terms": ["x"], "operator": "OR"},
            {"name": "Query B", "terms": ["y"], "operator": "OR"},
            {"name": "Query A", "terms": ["x"], "operator": "OR"},  # duplicate name
        ],
        categories=["cs.LG"],
        days=7,
    )

    assert papers[0].matched_queries == ["Query A", "Query B"]


def test_by_recency_splits_into_non_overlapping_tiers():
    today = dt.date(2026, 5, 22)

    def ago(days: int) -> str:
        return (today - dt.timedelta(days=days)).isoformat()

    papers = [_paper(ago(d)) for d in (0, 1, 3, 7, 8, 40)]
    tiers = fetch.by_recency(papers, today=today)

    assert [p.submitted for p in tiers.new] == [ago(0), ago(1)]
    assert [p.submitted for p in tiers.this_week] == [ago(3), ago(7)]
    assert [p.submitted for p in tiers.recent] == [ago(8), ago(40)]
    # the tiers partition the input: no overlap, nothing dropped
    assert len(tiers.new) + len(tiers.this_week) + len(tiers.recent) == len(papers)


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


class _FailingClient:
    """A client whose `results` raises a chosen exception (transient errors)."""

    raise_for: BaseException | None = None

    def __init__(self, *args, **kwargs):
        pass

    def results(self, search):
        if _FailingClient.raise_for is not None:
            raise _FailingClient.raise_for
        return iter([])


def test_fetch_skips_a_failing_query_and_continues(monkeypatch):
    """A 429 (or other arxiv.ArxivError) on one query is reported via the
    callback and the remaining queries still run."""
    states = iter([arxiv.HTTPError("u", 1, 429), None])  # first fails, second OK

    class _Client(_FailingClient):
        def results(self, search):
            err = next(states)
            if err is not None:
                raise err
            yield _FakeResult("2605.99999", ["physics.chem-ph"])

    monkeypatch.setattr(fetch.arxiv, "Client", _Client)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)
    failures: list[tuple[str, str]] = []

    papers = fetch.fetch(
        [
            {"name": "Q1", "terms": ["a"], "operator": "OR"},
            {"name": "Q2", "terms": ["b"], "operator": "OR"},
        ],
        categories=["physics.chem-ph"],
        days=7,
        on_query_error=lambda name, msg: failures.append((name, msg)),
    )

    assert [name for name, _ in failures] == ["Q1"]
    assert [p.arxiv_id for p in papers] == ["2605.99999"]


def test_fetch_raises_when_every_query_fails(monkeypatch):
    """If arXiv refuses every query, surface a clear error instead of silently
    returning an empty list (which would write an empty briefing)."""
    _FailingClient.raise_for = arxiv.HTTPError("u", 1, 429)
    monkeypatch.setattr(fetch.arxiv, "Client", _FailingClient)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    import pytest

    with pytest.raises(RuntimeError, match="rate limit"):
        fetch.fetch(
            [{"name": "Q1", "terms": ["a"], "operator": "OR"}],
            categories=[],
            days=7,
        )


def test_fetch_dedupes_and_filters_categories(monkeypatch):
    _FakeClient.results_to_yield = [
        _FakeResult("2605.00001", ["physics.chem-ph"]),
        _FakeResult("2605.00001", ["physics.chem-ph"]),  # duplicate id
        _FakeResult("2605.00002", ["cs.CV"]),  # category not requested
    ]
    monkeypatch.setattr(fetch.arxiv, "Client", _FakeClient)
    monkeypatch.setattr(fetch.arxiv, "Search", lambda **kwargs: None)

    papers = fetch.fetch(
        [{"terms": ["x"], "operator": "OR"}],
        categories=["physics.chem-ph"],
        days=7,
    )

    assert [p.arxiv_id for p in papers] == ["2605.00001"]
    assert papers[0].authors == ["A. Researcher"]
