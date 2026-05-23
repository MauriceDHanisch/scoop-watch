"""arXiv retrieval via the official Atom API (through the `arxiv` package).

Keyword search runs against arXiv text fields, which index same-day. Category
filtering is applied client-side because the API's category index lags
submissions by weeks; filtering on the returned records avoids that lag.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import arxiv


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    submitted: str
    categories: list[str]
    primary_category: str
    url: str
    # Names of the config.yaml queries that brought this paper in. A paper can
    # match several; the list records every query that hit before deduplication.
    matched_queries: list[str] = field(default_factory=list)


def _term_clause(term: str) -> str:
    """Build an ``all:`` clause for one search term.

    Multi-word terms, and any term carrying punctuation (hyphens above all),
    are quoted. An unquoted hyphenated term such as ``delta-learning`` is split
    by the arXiv API into a loose token match that returns mostly irrelevant
    papers; quoting forces an exact phrase match.
    """
    term = term.strip()
    quoted = term if term.isalnum() else f'"{term}"'
    return f"all:{quoted}"


def _date_clause(days: int) -> str:
    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    return f"submittedDate:[{start:%Y%m%d}0000 TO {today:%Y%m%d}2359]"


def build_query(terms: list[str], operator: str, days: int) -> str:
    joined = f" {operator.strip().upper()} ".join(_term_clause(t) for t in terms)
    return f"({joined}) AND {_date_clause(days)}"


def fetch(
    queries: list[dict[str, Any]],
    categories: list[str],
    days: int,
    max_results: int = 200,
    on_query_error: Callable[[str, str], None] | None = None,
) -> list[Paper]:
    """Run every query, filter to the given categories, dedupe by arXiv id.

    A paper that matches several queries is kept once but records every query
    name that hit it in ``matched_queries``. A query that fails (HTTP 429,
    transient parse error, ...) is reported via ``on_query_error`` and skipped;
    the remaining queries still run. Only when *every* query fails does this
    raise ``RuntimeError`` so the caller can stop early.
    """
    # Defaults match arXiv's documented "no more than one request every three
    # seconds". Bigger spacing / more retries did not help in practice: once
    # the IP is in penalty, neither value clears it within a useful window.
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    category_set = set(categories)
    rows: dict[str, dict[str, Any]] = {}
    matches: dict[str, list[str]] = {}
    failed: list[str] = []

    for query in queries:
        query_name = str(query.get("name") or "(unnamed)")
        query_string = build_query(query["terms"], query.get("operator", "OR"), days)
        search = arxiv.Search(
            query=query_string,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        try:
            results = list(client.results(search))
        except arxiv.ArxivError as error:
            failed.append(query_name)
            if on_query_error is not None:
                on_query_error(query_name, str(error))
            continue

        for result in results:
            result_categories = list(result.categories)
            if category_set and not category_set.intersection(result_categories):
                continue
            paper_id = result.get_short_id()
            hits = matches.setdefault(paper_id, [])
            if query_name not in hits:
                hits.append(query_name)
            if paper_id in rows:
                continue
            rows[paper_id] = dict(
                arxiv_id=paper_id,
                title=" ".join(result.title.split()),
                authors=[author.name for author in result.authors],
                abstract=" ".join(result.summary.split()),
                submitted=result.published.date().isoformat(),
                categories=result_categories,
                primary_category=result.primary_category,
                url=result.entry_id,
            )

    if failed and len(failed) == len(queries):
        raise RuntimeError(
            f"arXiv rejected every query ({len(failed)} of {len(queries)}); "
            "likely a rate limit, try again in a few minutes"
        )

    papers = [Paper(**rows[pid], matched_queries=list(matches[pid])) for pid in rows]
    return sorted(papers, key=lambda p: p.submitted, reverse=True)


def papers_as_dicts(papers: list[Paper]) -> list[dict[str, Any]]:
    return [asdict(p) for p in papers]


def archive_papers(path: Path, tiered: TieredPapers) -> None:
    """Write the fetched papers to ``path`` as indented JSON for inspection.

    Each entry carries its source metadata (``matched_queries``) and the
    ``recency`` tier it falls in (``new`` / ``this-week`` / ``recent``).
    """
    entries: list[dict[str, Any]] = []
    for tier_name, papers in (
        ("new", tiered.new),
        ("this-week", tiered.this_week),
        ("recent", tiered.recent),
    ):
        for paper in papers:
            entry = asdict(paper)
            entry["recency"] = tier_name
            entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


# Recency tier boundaries, in days since submission.
NEW_DAYS = 1
WEEK_DAYS = 7


@dataclass(frozen=True)
class TieredPapers:
    """Fetched papers split into non-overlapping recency tiers."""

    new: list[Paper]  # submitted within the last NEW_DAYS days
    this_week: list[Paper]  # within WEEK_DAYS days, but older than `new`
    recent: list[Paper]  # the rest of the fetch window


def by_recency(papers: list[Paper], today: dt.date | None = None) -> TieredPapers:
    """Split papers into non-overlapping new / this-week / recent tiers."""
    today = today or dt.date.today()
    new: list[Paper] = []
    this_week: list[Paper] = []
    recent: list[Paper] = []
    for paper in papers:
        age = (today - dt.date.fromisoformat(paper.submitted)).days
        if age <= NEW_DAYS:
            new.append(paper)
        elif age <= WEEK_DAYS:
            this_week.append(paper)
        else:
            recent.append(paper)
    return TieredPapers(new=new, this_week=this_week, recent=recent)
