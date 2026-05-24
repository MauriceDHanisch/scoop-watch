"""Deep-mode batched synthesis with per-batch checkpointing.

Today's daily synthesis runs ≤3 agent calls (one per recency tier). Deep mode
spans years of papers (1k–5k typical), which does not fit in a single agent
context. This module splits the corpus into fixed-size batches, runs an agent
call on each in parallel, then runs one merge pass that combines the batch
outputs into a coherent survey.

Checkpoints are file-existence: each batch writes its result to
``deep-batches/<date>/batch_NN.md`` as soon as the call returns, and a resume
run skips batches whose file already exists. The merge output lands at
``deep-archive/<date>.md``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config, paths
from .fetch import Paper, papers_as_dicts
from .synthesize import _run_agent

# Per-batch paper count. 100 × ~500 tokens/paper ≈ 50k input tokens, plus the
# system prompt and project description ≈ 55k total per call. Well inside
# Claude Opus's 200k-token context and leaves headroom for the agent's output.
DEFAULT_BATCH_SIZE = 100

# Concurrent agent calls during fan-out. The local probe (30 trivial calls,
# all succeeded, p95 ~13s) suggests Claude's per-org gateway will allow more,
# but real synthesis prompts are ~50k tokens vs. the probe's ~30. 5 keeps the
# token-per-minute burst well under any reasonable tier limit and the tail
# latency clean.
DEFAULT_MAX_WORKERS = 5


def _batch(papers: list[Paper], size: int) -> list[list[Paper]]:
    """Split papers into fixed-size chunks, last chunk may be short."""
    return [papers[i : i + size] for i in range(0, len(papers), size)]


def _batch_prompt(project: str, batch: list[Paper], batch_idx: int, total: int) -> str:
    """Assemble the per-batch prompt."""
    return (
        f"{paths.package_text('synthesis_deep.md')}\n\n"
        f"# Batch context\nThis is batch {batch_idx + 1} of {total} from a "
        f"multi-year survey. Produce only the survey content for the papers "
        f"in this batch.\n\n"
        f"# Project description\n{config.project_description(project)}\n\n"
        f"# Papers (JSON)\n{json.dumps(papers_as_dicts(batch), indent=2)}\n"
    )


_CONFIRMED = "🚨 Confirmed Scoop"
_POTENTIAL = "⚠️ Potential Scoop"
_SECTION_ORDER = (_CONFIRMED, _POTENTIAL)
_NO_THEME = ""  # bucket for entries the batch placed under a section without `###`


@dataclass(frozen=True)
class _Entry:
    """One paper entry parsed out of a batch output, kept as the verbatim
    markdown block plus its (YYYY-MM) date for sorting."""

    date: str  # YYYY-MM; entries with a missing/bad date sort last
    text: str  # the full entry block ending right before the closing `---`


@dataclass
class _ParsedBatch:
    """All entries from one batch, bucketed (section, theme) -> entries."""

    by_section_theme: dict[tuple[str, str], list[_Entry]] = field(default_factory=dict)


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_THEME_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
# Author line: "<names> · [arxiv:<id>](<url>) · YYYY-MM" (optional trailing -DD
# is tolerated so an agent that wrote a full ISO date still parses cleanly).
_DATE_IN_AUTHOR_RE = re.compile(r"·\s*(\d{4}-\d{2})(?:-\d{2})?\s*$", re.MULTILINE)
# arxiv adopted the ``YYMM.NNNNN`` identifier in April 2007, so any 4-digit
# prefix maps unambiguously to ``20YY-MM``. Used as a fallback date source
# when the author-line date is missing, malformed, or implausible.
_ARXIV_ID_RE = re.compile(r"arxiv:(\d{2})(\d{2})\.\d{4,5}", re.IGNORECASE)


def _date_from_arxiv_id(text: str) -> str:
    """Extract ``YYYY-MM`` from the first ``arxiv:YYMM.NNNNN`` reference."""
    match = _ARXIV_ID_RE.search(text)
    if not match:
        return ""
    yy, mm = int(match.group(1)), int(match.group(2))
    if not (1 <= mm <= 12):
        return ""
    return f"20{yy:02d}-{mm:02d}"


def _resolve_entry_date(text: str) -> str:
    """Best-effort ``YYYY-MM`` for one entry, with three fallbacks.

    Source 1: the ``· YYYY-MM`` token at the end of the author line.
    Source 2: the arxiv id prefix (e.g. ``arxiv:2506.06623`` → ``2025-06``).
    Returns ``""`` only when neither yields a plausible calendar year.

    The arxiv id fallback recovers two real-world agent failure modes: writing
    the YYMM prefix as the "year" (``· 2506-06``) and writing a full ISO date
    with the day appended (``· 2025-09-30``). The arxiv link is the only
    machine-readable date source on the entry that we can trust in those cases.
    """
    match = _DATE_IN_AUTHOR_RE.search(text)
    if match:
        date = match.group(1)
        year = int(date[:4])
        if 1900 <= year <= 2100:
            return date
    return _date_from_arxiv_id(text)


def _project_theme_order(project: str) -> list[str]:
    """Theme names declared as ``## Theme: <name>`` in the project description.

    Used to order themes inside each section of the merged survey. Themes that
    a batch invents but the project does not declare go after these,
    alphabetically — see ``_merge_themes_in_order``. A missing project.md
    (test fixtures, freshly scaffolded projects) returns an empty list and
    falls back to all-alphabetical ordering downstream.
    """
    try:
        text = config.project_description(project)
    except FileNotFoundError:
        return []
    return re.findall(r"^##\s+Theme:\s*(.+?)\s*$", text, re.MULTILINE)


def _split_into_sections(body: str) -> dict[str, str]:
    """Split a batch output into ``{section heading: section body}``.

    Anything before the first recognised section heading is discarded; the
    batch prompt forbids preamble, so this only protects against the agent
    emitting boilerplate the prompt told it not to.
    """
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for idx, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        sections[heading] = body[start:end].strip()
    return sections


def _split_section_into_themes(section_body: str) -> dict[str, str]:
    """Split a section body into ``{theme name: theme body}``.

    A section without any `###` heading means the project declared no themes;
    the entire body goes under the empty-string key (``_NO_THEME``).
    """
    themes: dict[str, str] = {}
    matches = list(_THEME_RE.finditer(section_body))
    if not matches:
        if section_body.strip():
            themes[_NO_THEME] = section_body.strip()
        return themes
    # Anything before the first `###` is implicit-no-theme leading content;
    # capture it so a malformed batch does not silently drop entries.
    leading = section_body[: matches[0].start()].strip()
    if leading:
        themes[_NO_THEME] = leading
    for idx, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section_body)
        body = section_body[start:end].strip()
        # Same theme appearing twice in one batch (unusual): concatenate.
        if name in themes:
            themes[name] = themes[name] + "\n\n" + body
        else:
            themes[name] = body
    return themes


def _split_theme_into_entries(theme_body: str) -> list[_Entry]:
    """Split a theme body on the closing ``---`` rule between entries.

    Each entry's verbatim text is preserved (minus any leading/trailing
    whitespace). The YYYY-MM date is extracted from the author line for
    sorting; entries without a parseable date get the empty string, which
    sorts them after dated entries.
    """
    chunks = re.split(r"^\s*---\s*$", theme_body, flags=re.MULTILINE)
    entries: list[_Entry] = []
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue
        # Heuristic to drop batches that wrote prose like "no scoops" instead of
        # entries: a real entry starts with a bold title.
        if not text.startswith("**"):
            continue
        entries.append(_Entry(date=_resolve_entry_date(text), text=text))
    return entries


def _parse_batch(body: str) -> _ParsedBatch:
    """Walk a batch output and bucket its entries by (section, theme)."""
    parsed = _ParsedBatch()
    sections = _split_into_sections(body)
    for section_name in _SECTION_ORDER:
        section_body = sections.get(section_name, "").strip()
        if not section_body:
            continue
        for theme_name, theme_body in _split_section_into_themes(section_body).items():
            entries = _split_theme_into_entries(theme_body)
            if not entries:
                continue
            parsed.by_section_theme.setdefault((section_name, theme_name), []).extend(
                entries
            )
    return parsed


def _merge_themes_in_order(
    section_buckets: dict[str, list[_Entry]], declared_order: list[str]
) -> list[tuple[str, list[_Entry]]]:
    """Return ``[(theme, entries)]`` in declared-order, undeclared at the end."""
    declared_set = set(declared_order)
    ordered: list[tuple[str, list[_Entry]]] = []
    # Declared themes (project.md order), only if they have entries.
    for theme in declared_order:
        if theme in section_buckets and section_buckets[theme]:
            ordered.append((theme, section_buckets[theme]))
    # Themes a batch invented that the project did not declare: alphabetical.
    extras = sorted(
        name
        for name in section_buckets
        if name not in declared_set and section_buckets[name]
    )
    for theme in extras:
        ordered.append((theme, section_buckets[theme]))
    return ordered


_UNDATED_YEAR = "Undated"  # bucket label for entries with a missing/bad date


def _normalize_theme(name: str) -> str:
    """Case-insensitive comparison key with Unicode dash variants folded.

    Real-world drift: a batch paraphrases a theme name with an en-dash where
    the project description used a hyphen (or vice versa). Without folding,
    those land in two different buckets and surface as duplicated themes in
    the survey. Folded forms compare equal so the merger collapses them onto
    the project's canonical spelling.
    """
    folded = name.lower().strip()
    for dash in ("‐", "‑", "‒", "–", "—", "―", "−"):
        folded = folded.replace(dash, "-")
    # Collapse runs of whitespace so "Foo  bar" and "Foo bar" compare equal.
    return " ".join(folded.split())


def _canonicalize_theme_name(name: str, declared: list[str]) -> str:
    """Map a parsed theme name onto the project's declared spelling if any
    declared theme matches under ``_normalize_theme``. Otherwise return the
    name verbatim (it stays in the alphabetical bucket for undeclared themes).
    """
    key = _normalize_theme(name)
    for declared_name in declared:
        if _normalize_theme(declared_name) == key:
            return declared_name
    return name


def _year_of(date: str) -> str:
    """Return the entry's calendar year as a 4-digit string, or ``_UNDATED_YEAR``.

    Defensive against a common agent failure mode: writing the arxiv id's
    YYMM prefix (e.g. ``2509`` for September 2025) as if it were the calendar
    year, producing dates like ``2509-09``. The format contract demands
    YYYY-MM with YYYY in the plausible range; anything else is treated as
    undated so it surfaces in the trailing bucket instead of inventing a
    bogus year heading.
    """
    if not date or "-" not in date:
        return _UNDATED_YEAR
    year_str = date.split("-", 1)[0]
    if not (year_str.isdigit() and len(year_str) == 4):
        return _UNDATED_YEAR
    year = int(year_str)
    if 1900 <= year <= 2100:
        return year_str
    return _UNDATED_YEAR


def _group_entries_by_year(entries: list[_Entry]) -> list[tuple[str, list[_Entry]]]:
    """Group already-sorted entries into ``[(year, entries)]`` runs.

    Caller has already sorted entries by date descending, so a single pass
    suffices; consecutive entries sharing a year emit one group. Entries with
    a missing/unparseable/implausible date land in a trailing ``Undated``
    group. The sort+single-pass approach keeps Undated trailing because the
    empty string sorts before any plausible date under ``reverse=True``.
    """
    groups: list[tuple[str, list[_Entry]]] = []
    for entry in entries:
        year = _year_of(entry.date)
        if groups and groups[-1][0] == year:
            groups[-1][1].append(entry)
        else:
            groups.append((year, [entry]))
    # Move the Undated group to the end if it landed in the middle (can happen
    # when entries have a mix of plausible and implausible dates that mis-sort).
    dated = [g for g in groups if g[0] != _UNDATED_YEAR]
    undated = [g for g in groups if g[0] == _UNDATED_YEAR]
    if len(undated) > 1:
        # Merge multiple Undated groups into one.
        merged = (
            _UNDATED_YEAR,
            [e for _y, es in undated for e in es],
        )
        undated = [merged]
    return dated + undated


# Pulls the title from the leading `**Title**` line and splits the rest into
# the author line (line 2) and the analysis body (lines 3+).
_ENTRY_HEAD_RE = re.compile(
    r"^\*\*(?P<title>.+?)\*\*\s*\n(?P<author>[^\n]+)(?:\n+(?P<body>.+))?",
    re.DOTALL,
)
# Splits "Authors · [arxiv:id](url) · YYYY-MM" into the three pieces.
_AUTHOR_LINE_RE = re.compile(
    r"^(?P<authors>.+?)\s+·\s+\[(?P<label>arxiv:[^\]]+)\]\((?P<url>[^)]+)\)"
)


def _render_entry(entry: _Entry) -> str:
    """Wrap one entry in a `<details>` whose summary holds title + authors +
    date and whose body holds the arxiv link + analysis + distinction line.

    The summary uses `<small>` on the metadata after the title so the
    entry line reads visibly lighter and smaller than the parent year and
    theme summaries above it — three sizes (big/regular/small) without
    using heading tags (which break the chevron when placed in `<summary>`).
    """
    match = _ENTRY_HEAD_RE.match(entry.text.strip())
    if not match:
        return entry.text
    title = match.group("title").strip()
    author_line = match.group("author").strip()
    analysis = (match.group("body") or "").strip()

    head = _AUTHOR_LINE_RE.match(author_line)
    if head:
        authors = head.group("authors").strip()
        link_md = f"[{head.group('label')}]({head.group('url')})"
    else:
        # Author line that does not parse: keep it whole in the body and skip
        # the duplication in the summary.
        authors = author_line
        link_md = ""

    date = entry.date or "—"
    summary = f"<strong>{title}</strong> <small>· {authors} · {date}</small>"
    body_parts = [p for p in (link_md, analysis) if p]
    body = "\n\n".join(body_parts)
    return f"<details>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


def _render_year_group(year: str, entries: list[_Entry]) -> str:
    """Render one year as a collapsible `<details>` (closed by default)
    whose body is a `<blockquote>` holding one `<details>` per entry.

    GitHub's markdown renderer does NOT apply margin-left to nested
    `<details>` by default — browser-CSS indentation is the convention but
    GitHub's stylesheet overrides it to zero. To get visible indentation we
    wrap inner blocks in `<blockquote>`, which GitHub styles with both a
    left indent and a vertical bar; the bar doubles as a hierarchy cue.
    The summary stays `<strong>` (inline) so the chevron remains attached
    to the visible label — block tags like `<h4>` inside `<summary>` push
    the chevron onto its own line and make it look attached to the wrong
    element. Visual ladder: theme bar > year bar > entry analysis.
    """
    plural = "paper" if len(entries) == 1 else "papers"
    label = f"<strong>📅 {year} &nbsp;·&nbsp; {len(entries)} {plural}</strong>"
    inner = "\n\n".join(_render_entry(e) for e in entries)
    body = f"<blockquote>\n\n{inner}\n\n</blockquote>"
    return f"<details>\n<summary>{label}</summary>\n\n{body}\n\n</details>"


def _render_theme(theme: str, entries: list[_Entry]) -> str:
    """Render a theme as a collapsible `<details>` (closed by default)
    whose body is a `<blockquote>` holding its year groups.

    Like the year summary, the theme summary is `<strong>` (inline) — block
    tags inside `<summary>` displace the chevron and make it look attached
    to the wrong element. The `<blockquote>` wrapper is where the visible
    indentation comes from on GitHub (nested `<details>` alone is not
    indented by GitHub's stylesheet). Every level is collapsed by default;
    the reader drills from section → theme → year → paper analysis on
    demand, with each step indented one bar deeper than the last.
    """
    plural = "paper" if len(entries) == 1 else "papers"
    year_groups = _group_entries_by_year(entries)
    inner = "\n\n".join(_render_year_group(y, es) for y, es in year_groups)
    if not theme:
        # Project declared no themes — emit year groups directly (no theme
        # `<details>` wrap; the year groups carry their own indent).
        return inner
    body = f"<blockquote>\n\n{inner}\n\n</blockquote>"
    # `<big>` is inline (unlike `<h3>`/`<h4>` which break the chevron when
    # placed inside `<summary>`) and GitHub renders it ~1.17x larger,
    # giving the theme summary visible size weight over the year and entry
    # summaries below it. Three sizes total: theme > year > entry.
    label = (
        f"<big><strong>📂 {theme} &nbsp;·&nbsp; {len(entries)} {plural}</strong></big>"
    )
    return f"<details>\n<summary>{label}</summary>\n\n{body}\n\n</details>"


def _slug(text: str) -> str:
    """Lowercase, ASCII-alnum-only slug for `<a name>` anchors and #links."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _section_anchor(section: str) -> str:
    """Stable anchor for a section heading. We don't trust GitHub's auto-
    generated anchors (they depend on emoji handling and count suffixes)."""
    return _slug(section.encode("ascii", "ignore").decode("ascii"))


def _render_toc(
    buckets: dict[str, dict[str, list[_Entry]]],
    declared_themes: list[str],
    section_totals: dict[str, int],
) -> str:
    """A nested bullet list linking to each section, with per-theme counts.

    Theme entries do not get anchors — the section anchor lands the reader
    next to the theme they want; one click on the section heading expands.
    Skipping theme anchors keeps the markup small and avoids the brittle
    `<a name>` injection inside `<summary>` tags.
    """
    lines = ["## Contents", ""]
    for section in _SECTION_ORDER:
        section_anchor = _section_anchor(section)
        lines.append(f"- [{section}](#{section_anchor}) ({section_totals[section]})")
        for theme, entries in _merge_themes_in_order(buckets[section], declared_themes):
            count = len(entries)
            plural = "paper" if count == 1 else "papers"
            lines.append(f"  - {theme} ({count} {plural})")
    return "\n".join(lines)


def _programmatic_merge(
    project: str,
    bodies: list[str],
    years: int,
    start_date: str,
    end_date: str,
    total_papers: int,
    on_warning: Callable[[str], None] | None = None,
) -> str:
    """Combine batch outputs into the final survey, deterministically.

    Replaces what was an 8-minute LLM merge call with a parser. The per-batch
    synthesis (where the analytical work happens) is unchanged; this function
    only sorts, groups, counts and wraps the entries those batches produced.
    Entries are kept verbatim — never paraphrased — so no analytical content
    is at risk.

    A non-empty batch body that parses to zero entries almost always means
    the agent drifted from the format contract (see ``synthesis_deep.md``):
    when ``on_warning`` is provided, the merger reports each such batch by
    its 1-based index so the caller can surface the warning to the user.
    """
    warn = on_warning or (lambda _msg: None)
    declared_themes = _project_theme_order(project)

    # Bucket all entries from all batches by (section, theme). Theme names
    # parsed out of a batch are mapped onto the project's declared spelling
    # whenever a (case-insensitive, dash-folded) match exists; this collapses
    # innocent paraphrases like "Kohn–Sham" vs "Kohn-Sham" onto one bucket.
    buckets: dict[str, dict[str, list[_Entry]]] = {
        section: {} for section in _SECTION_ORDER
    }
    for batch_idx, body in enumerate(bodies, start=1):
        parsed = _parse_batch(body)
        entry_count = sum(len(es) for es in parsed.by_section_theme.values())
        # A legitimately-empty batch ("no scoops in this batch") emits the two
        # expected section headings with no entries — that's the contract, not
        # drift. Only warn when the parser found no entries AND none of the
        # expected `## 🚨 / ## ⚠️` headings either: in that case the agent
        # produced text that the parser doesn't recognise at all.
        sections_present = _split_into_sections(body)
        recognised = sum(1 for s in _SECTION_ORDER if s in sections_present)
        if entry_count == 0 and body.strip() and recognised == 0:
            warn(
                f"batch {batch_idx} parsed to 0 entries and no recognised "
                f"section headings; the agent may have drifted from the "
                f"deep-survey format (see synthesis_deep.md). Inspect "
                f"deep/batches/<date>/batch_{batch_idx - 1:02d}.md."
            )
        for (section, theme), entries in parsed.by_section_theme.items():
            canonical = _canonicalize_theme_name(theme, declared_themes)
            buckets[section].setdefault(canonical, []).extend(entries)

    # Sort each (section, theme) by date descending (newest first). The empty
    # string from a missing date sorts last because '' < any real YYYY-MM.
    for section_buckets in buckets.values():
        for theme in section_buckets:
            section_buckets[theme].sort(key=lambda entry: entry.date or "", reverse=True)

    confirmed_total = sum(len(v) for v in buckets[_CONFIRMED].values())
    potential_total = sum(len(v) for v in buckets[_POTENTIAL].values())
    surfaced = confirmed_total + potential_total
    non_overlapping = max(total_papers - surfaced, 0)

    section_totals = {_CONFIRMED: confirmed_total, _POTENTIAL: potential_total}

    parts: list[str] = []
    parts.append(f"# 🔬 Scoop-watch Deep Survey — {project}")
    parts.append(
        f"*{years}-year window: {start_date} → {end_date} · "
        f"{total_papers} papers scanned · {surfaced} surfaced*"
    )
    parts.append("")
    parts.append("## Summary")
    parts.append(f"- {confirmed_total} confirmed scoops requiring response")
    parts.append(f"- {potential_total} potential scoops worth monitoring")
    parts.append(f"- {non_overlapping} papers reviewed and judged non-overlapping")
    parts.append("")

    # Table of contents — anchors link to the section headings below. Cheap
    # navigation for surveys with many themes.
    parts.append(_render_toc(buckets, declared_themes, section_totals))
    parts.append("")
    parts.append("---")
    parts.append("")

    section_titles = {
        _CONFIRMED: f"## {_CONFIRMED} ({confirmed_total})",
        _POTENTIAL: f"## {_POTENTIAL} ({potential_total})",
    }
    for section in _SECTION_ORDER:
        # Explicit `<a name>` anchor so TOC links land here regardless of
        # GitHub's auto-anchor generation quirks (emoji handling, count
        # suffix changes between runs).
        parts.append(f'<a name="{_section_anchor(section)}"></a>')
        parts.append(section_titles[section])
        parts.append("")
        themes = _merge_themes_in_order(buckets[section], declared_themes)
        # Horizontal rule between themes inside a section, so the reader sees
        # a clean visual cut between theme blocks even when every theme is
        # collapsed and they would otherwise stack flush against each other.
        for idx, (theme, entries) in enumerate(themes):
            if idx > 0:
                parts.append("---")
                parts.append("")
            parts.append(_render_theme(theme, entries))
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _merge_prompt(
    project: str,
    batch_bodies: list[str],
    years: int,
    start_date: str,
    end_date: str,
    total_papers: int,
) -> str:
    """Assemble the merge-pass prompt from all batch outputs."""
    joined = "\n\n=== batch boundary ===\n\n".join(batch_bodies)
    surfaced_hint = (
        "Count Confirmed + Potential entries across all batches to fill in "
        "<surfaced>, <N>, <M>; compute <K> as total_papers - surfaced."
    )
    return (
        f"{paths.package_text('synthesis_deep_merge.md')}\n\n"
        f"# Data\n"
        f"project: {project}\n"
        f"years: {years}\n"
        f"start_date: {start_date}\n"
        f"end_date: {end_date}\n"
        f"total_papers: {total_papers}\n\n"
        f"{surfaced_hint}\n\n"
        f"# Project description\n{config.project_description(project)}\n\n"
        f"# Batch outputs to merge\n{joined}\n"
    )


def _run_batch_with_checkpoint(
    project: str,
    agent: str,
    model: str | None,
    batch: list[Paper],
    batch_idx: int,
    total: int,
    out_path: Path,
) -> str:
    """Run one batch's agent call and persist the result to ``out_path``.

    Atomic write via ``.tmp`` + rename: a crash mid-write leaves no half file,
    so the next resume run cleanly re-fires only the truly missing batches.
    """
    prompt = _batch_prompt(project, batch, batch_idx, total)
    body = _run_agent(agent, model, prompt)
    tmp = out_path.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.rename(out_path)
    return body


def synthesize_deep(
    project: str,
    papers: list[Paper],
    years: int,
    date: str,
    agent: str | None = None,
    model: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Batched synthesis with on-disk checkpoints and a merge pass.

    Returns the path to the final merged briefing under ``deep-archive/``.
    Re-running with the same ``date`` skips batches whose ``batch_NN.md``
    already exists, so a partial run resumes naturally.
    """
    agent = agent or config.agent()
    model = model if model is not None else config.model()
    report = progress or (lambda _message: None)

    batches = _batch(papers, batch_size)
    total = len(batches)
    batches_dir = paths.deep_batches_dir(project, date)
    batches_dir.mkdir(parents=True, exist_ok=True)

    # Identify which batches still need running. Existing files are loaded
    # verbatim so the merge pass sees the full set.
    todo: list[tuple[int, list[Paper], Path]] = []
    bodies: list[str | None] = [None] * total
    for idx, batch in enumerate(batches):
        batch_path = batches_dir / f"batch_{idx:02d}.md"
        if batch_path.is_file():
            bodies[idx] = batch_path.read_text(encoding="utf-8")
            report(f"batch {idx + 1}/{total}: cached (skipped)")
        else:
            todo.append((idx, batch, batch_path))

    if todo:
        report(
            f"running {len(todo)} batch(es) with max_workers={max_workers}; "
            f"{total - len(todo)} cached"
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _run_batch_with_checkpoint,
                    project,
                    agent,
                    model,
                    batch,
                    idx,
                    total,
                    batch_path,
                ): idx
                for idx, batch, batch_path in todo
            }
            for future in as_completed(futures):
                idx = futures[future]
                bodies[idx] = future.result()
                report(f"batch {idx + 1}/{total}: done")

    # Merge pass. The bodies list is now fully populated (either from disk or
    # from the executor above). The merge is deterministic and runs in Python:
    # the per-batch synthesis (where the analytical work happens) is the only
    # place an LLM is needed; sorting, grouping, counting and `<details>`
    # wrapping are mechanical work the old LLM merge wasted ~8 minutes on.
    assert all(body is not None for body in bodies), "every batch must have a body"
    end = dt.date.fromisoformat(date)
    start = end - dt.timedelta(days=years * 365)
    report(f"merging {total} batch output(s) into the final survey...")
    merged = _programmatic_merge(
        project,
        [body for body in bodies if body is not None],
        years=years,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        total_papers=len(papers),
        on_warning=lambda msg: report(f"WARNING: {msg}"),
    )

    archive = paths.deep_archive_dir(project)
    archive.mkdir(parents=True, exist_ok=True)
    out_path = archive / f"{date}_{years}y.md"
    tmp = out_path.with_suffix(".md.tmp")
    tmp.write_text(merged, encoding="utf-8")
    tmp.rename(out_path)
    return out_path
