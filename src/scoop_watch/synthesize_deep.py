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
# Author line: "<names> · [arxiv:<id>](<url>) · YYYY-MM"
_DATE_IN_AUTHOR_RE = re.compile(r"·\s*(\d{4}-\d{2})\s*$", re.MULTILINE)


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
        date_match = _DATE_IN_AUTHOR_RE.search(text)
        date = date_match.group(1) if date_match else ""
        entries.append(_Entry(date=date, text=text))
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


def _render_theme(theme: str, entries: list[_Entry]) -> str:
    """Wrap one theme's entries in a `<details>` block with a paper count.

    Entries are emitted in the order received (caller sorts beforehand). Each
    entry's text is followed by the standalone ``---`` rule the format
    contract requires.
    """
    plural = "paper" if len(entries) == 1 else "papers"
    label = f"<strong>{theme}</strong> ({len(entries)} {plural})"
    # If there is no theme name, render the entries directly (no `<details>`
    # wrap) — the project declared no themes and a collapsible with an empty
    # label would read strangely.
    bodies = "\n\n---\n\n".join(entry.text for entry in entries) + "\n\n---\n"
    if not theme:
        return bodies
    return f"<details>\n<summary>{label}</summary>\n\n{bodies}\n</details>"


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

    # Bucket all entries from all batches by (section, theme).
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
            buckets[section].setdefault(theme, []).extend(entries)

    # Sort each (section, theme) by date descending (newest first). The empty
    # string from a missing date sorts last because '' < any real YYYY-MM.
    for section_buckets in buckets.values():
        for theme in section_buckets:
            section_buckets[theme].sort(key=lambda entry: entry.date or "", reverse=True)

    declared_themes = _project_theme_order(project)
    confirmed_total = sum(len(v) for v in buckets[_CONFIRMED].values())
    potential_total = sum(len(v) for v in buckets[_POTENTIAL].values())
    surfaced = confirmed_total + potential_total
    non_overlapping = max(total_papers - surfaced, 0)

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
    parts.append("---")
    parts.append("")

    section_titles = {
        _CONFIRMED: f"## {_CONFIRMED} ({confirmed_total})",
        _POTENTIAL: f"## {_POTENTIAL} ({potential_total})",
    }
    for section in _SECTION_ORDER:
        parts.append(section_titles[section])
        parts.append("")
        themes = _merge_themes_in_order(buckets[section], declared_themes)
        for theme, entries in themes:
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
