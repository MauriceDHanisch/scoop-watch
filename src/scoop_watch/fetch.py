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
from itertools import combinations
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


@dataclass(frozen=True)
class _MergedQuery:
    """One arXiv request built from one or more config-level queries.

    ``operator == "passthrough"`` carries a single config query verbatim.
    ``operator == "AND-OR"`` represents a merged group:
    ``anchor`` are AND-shared terms, ``alternatives`` are the per-query
    distinguishing terms that get OR'd. The resulting arXiv expression is::

        (anchor[0] AND anchor[1] ...) AND (alt[0] OR alt[1] ...)
    """

    name: str
    operator: str
    anchor: tuple[str, ...]
    alternatives: tuple[str, ...]
    originals: tuple[dict[str, Any], ...]


def group_queries(queries: list[dict[str, Any]]) -> list[_MergedQuery]:
    """Smart group-merge: AND queries that share (n-1) terms merge into one
    request. OR queries and ungroupable AND queries pass through unchanged.

    Greedy: at each step, pick the (n-1)-term subset shared by the most
    remaining AND queries and pull those into one merged group. Stop when no
    subset is shared by at least two queries.
    """
    and_queries: list[dict[str, Any]] = []
    or_queries: list[dict[str, Any]] = []
    for query in queries:
        op = str(query.get("operator", "OR")).upper()
        (and_queries if op == "AND" else or_queries).append(query)

    merged: list[_MergedQuery] = []
    remaining = list(and_queries)

    while remaining:
        anchor_to_queries: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for query in remaining:
            terms = list(query.get("terms", []))
            if len(terms) < 2:
                continue
            for anchor in combinations(sorted(terms), len(terms) - 1):
                anchor_to_queries.setdefault(anchor, []).append(query)

        if not anchor_to_queries:
            break

        # Pick the anchor used by the most queries; lexicographic tiebreak
        # so the result is deterministic.
        best_anchor, best_group = max(
            anchor_to_queries.items(),
            key=lambda item: (len(item[1]), item[0]),
        )
        if len(best_group) < 2:
            break

        # The "alternatives" are the leftover terms each grouped query
        # contributes (the one term that isn't in the anchor).
        alts: list[str] = []
        for query in best_group:
            for term in query["terms"]:
                if term not in best_anchor and term not in alts:
                    alts.append(term)

        anchor_label = " AND ".join(best_anchor)
        merged.append(
            _MergedQuery(
                name=f"{anchor_label} (any)",
                operator="AND-OR",
                anchor=tuple(best_anchor),
                alternatives=tuple(alts),
                originals=tuple(best_group),
            )
        )
        for query in best_group:
            remaining.remove(query)

    # Remaining AND queries and all OR queries pass through unchanged.
    for query in remaining + or_queries:
        merged.append(
            _MergedQuery(
                name=str(query.get("name") or "(unnamed)"),
                operator="passthrough",
                anchor=(),
                alternatives=(),
                originals=(query,),
            )
        )
    return merged


def _merged_query_string(entry: _MergedQuery, days: int) -> str:
    """Build the arXiv ``search_query`` for one merged or passthrough entry."""
    if entry.operator == "AND-OR":
        anchor_part = " AND ".join(_term_clause(t) for t in entry.anchor)
        alts_part = " OR ".join(_term_clause(t) for t in entry.alternatives)
        return f"({anchor_part}) AND ({alts_part}) AND {_date_clause(days)}"
    original = entry.originals[0]
    return build_query(original["terms"], str(original.get("operator", "OR")), days)


def _has_term(text: str, term: str) -> bool:
    """Best-effort text match: literal or hyphen-stripped form in the text."""
    needle = term.lower().strip()
    return needle in text or needle.replace("-", " ") in text


def _attribute_to_originals(
    title: str, abstract: str, originals: tuple[dict[str, Any], ...]
) -> list[str]:
    """Best-effort: which of ``originals`` does this paper satisfy by text?"""
    text = (title + " " + abstract).lower()
    matched: list[str] = []
    for orig in originals:
        terms = orig.get("terms", [])
        op = str(orig.get("operator", "OR")).upper()
        if op == "AND":
            satisfies = all(_has_term(text, t) for t in terms)
        else:
            satisfies = any(_has_term(text, t) for t in terms)
        if satisfies:
            matched.append(str(orig.get("name") or "(unnamed)"))
    return matched


def _matched_queries_for(entry: _MergedQuery, title: str, abstract: str) -> list[str]:
    """Hybrid attribution for one paper: the merged group label always; plus
    the original query names the paper's title+abstract satisfies. Passthrough
    entries just carry their original name. The group label tells you exactly
    what was sent to arXiv; the individual labels are text-inferred (so they
    can under-report when arXiv matched on a field we don't store)."""
    if entry.operator == "passthrough":
        return [entry.name]
    result = [entry.name]
    for name in _attribute_to_originals(title, abstract, entry.originals):
        if name not in result:
            result.append(name)
    return result


class FetchAborted(RuntimeError):
    """Raised on the first failed merged request to halt the fetch stage.

    Carries ``query``, ``error`` and ``url`` so the CLI can write a useful log
    and the user can decide whether to retry or wait. The fetch is partial by
    the time this fires; the caller should discard it and not synthesize.
    """

    def __init__(self, query: str, error: str, url: str) -> None:
        super().__init__(f"fetch aborted on query '{query}': {error}")
        self.query = query
        self.error = error
        self.url = url


def fetch(
    queries: list[dict[str, Any]],
    categories: list[str],  # kept in the signature for backward compat; unused
    days: int,
    max_results: int = 200,
    on_query_error: Callable[[str, str], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[Paper]:
    """Run every merged request and return the deduped union of results.

    AND queries that share an (n-1)-term subset are smart-merged into a single
    arXiv request (``anchor AND (alt1 OR alt2 ...)``), which trims request
    count substantially without changing the union of results. Each paper's
    ``matched_queries`` carries a hybrid attribution: the merged group label
    (always, exact) plus any individual original-query names whose terms also
    appear in the paper's title or abstract (best-effort).

    Category is **not** used as a hard filter; arXiv's category index is loose
    and authors cross-post inconsistently, so a hard cut drops relevant
    cross-discipline papers. ``primary_category`` and ``categories`` are still
    stored on each Paper and passed downstream, where the synthesis agent
    treats them as a soft signal.

    Strict failure: the first request that fails (HTTP 429, transient parse
    error, ...) is reported via ``on_query_error`` and the function raises
    ``FetchAborted``. The caller is expected to log the failure and skip
    synthesis. ``categories`` is kept in the signature for backward compat.
    """
    del categories  # see docstring: category filtering is intentionally off

    # Defaults match arXiv's documented "no more than one request every three
    # seconds". Bigger spacing / more retries did not help in practice.
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    rows: dict[str, dict[str, Any]] = {}
    matches: dict[str, list[str]] = {}

    merged_entries = group_queries(queries)
    total = len(merged_entries)
    report = progress or (lambda _message: None)

    for idx, entry in enumerate(merged_entries, start=1):
        query_string = _merged_query_string(entry, days)
        search = arxiv.Search(
            query=query_string,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        # Pre-request line so the user sees the loop is alive even when arXiv
        # holds the response (the client enforces 3s spacing and may retry on
        # 429, which can stall a single request for tens of seconds).
        report(f"[{idx}/{total}] {entry.name}: requesting...")
        try:
            results = list(client.results(search))
        except arxiv.ArxivError as error:
            if on_query_error is not None:
                on_query_error(entry.name, str(error))
            url = getattr(error, "url", "") or ""
            raise FetchAborted(entry.name, str(error), url) from error

        rows_before = len(rows)
        for result in results:
            paper_id = result.get_short_id()
            title = " ".join(result.title.split())
            abstract = " ".join(result.summary.split())
            hits = matches.setdefault(paper_id, [])
            for name in _matched_queries_for(entry, title, abstract):
                if name not in hits:
                    hits.append(name)
            if paper_id in rows:
                continue
            rows[paper_id] = dict(
                arxiv_id=paper_id,
                title=title,
                authors=[author.name for author in result.authors],
                abstract=abstract,
                submitted=result.published.date().isoformat(),
                categories=list(result.categories),
                primary_category=result.primary_category,
                url=result.entry_id,
            )
        new_papers = len(rows) - rows_before
        report(
            f"[{idx}/{total}] {entry.name}: "
            f"{len(results)} returned, {new_papers} new ({len(rows)} unique)"
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
