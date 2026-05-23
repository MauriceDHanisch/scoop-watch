"""Briefing synthesis: hand papers + project files to an agent CLI.

The fetched papers are split into recency tiers and synthesised in two passes:
a focused pass over the last week, where scoop detection matters most, and a
sweep over the rest of the window. A smaller, single-tier prompt lets the agent
categorise the time-sensitive papers without the whole window as distraction.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config, fetch, paths
from .fetch import Paper, papers_as_dicts

_AGENT_TIMEOUT = 600

# Section titles wrapped in <details> blocks (collapsed by default), so the
# briefing leads with the scoop sections.
_COLLAPSIBLE_SECTIONS = ("🛠️ Potentially Helpful", "📡 Broader Field")


def _wrap_collapsible(text: str) -> str:
    """Wrap the low-priority sections in <details> blocks with paper counts."""
    for title in _COLLAPSIBLE_SECTIONS:
        text = _wrap_one_section(text, title)
    return text


def _wrap_one_section(text: str, title: str) -> str:
    """Replace every ``## <title>`` or ``### <title>`` block with a <details>.

    The agent's heading depth varies between passes (haiku in particular
    sometimes shifts every section from level 2 to level 3); the regex
    accepts either and the lookahead uses a backreference so a body ends
    only at a heading of the same level (or a higher tier-break ``# ``).
    """
    pattern = re.compile(
        rf"^(?P<h>##|###) {re.escape(title)}\s*\n(?P<body>.*?)"
        rf"(?=\n(?P=h) |\n# |\Z)",
        re.MULTILINE | re.DOTALL,
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body").rstrip()
        # Count `**[` paper-format lines. Empty sections legitimately show 0;
        # we still wrap them so the tier renders consistently.
        count = len(re.findall(r"^\*\*\[", body, re.MULTILINE))
        return (
            f"<details>\n"
            f"<summary><strong>{title}</strong> ({count})</summary>\n\n"
            f"{body}\n\n"
            f"</details>"
        )

    return pattern.sub(repl, text)


def _agent_invocation(
    agent: str, model: str | None, prompt: str
) -> tuple[list[str], str | None]:
    """Return (argv, stdin) for a non-interactive synthesis run."""
    if agent == "claude":
        argv = ["claude", "-p"]
        if model:
            argv += ["--model", model]
        return argv, prompt
    if agent == "codex":
        # --skip-git-repo-check: the project data directory is not a git repo.
        # The prompt is passed on stdin via the `-` argument: it embeds a large
        # JSON payload and would overflow the OS argv length limit (ARG_MAX)
        # if passed as a command-line argument.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["--model", model]
        argv.append("-")
        return argv, prompt
    raise ValueError(f"unknown agent '{agent}' (expected 'claude' or 'codex')")


def build_prompt(project: str, papers: list[Paper], scope: str) -> str:
    """Assemble the synthesis prompt for one recency tier of papers."""
    today = dt.date.today().isoformat()
    return (
        f"{paths.package_text('synthesis.md')}\n\n"
        f"# Today's date\n{today}\n\n"
        f"# Recency scope\nEvery paper below was submitted within {scope}. "
        f"This call covers ONE recency tier; do not emit any time-bucket "
        f"sub-headings (e.g. 'New', 'This week', 'Recent') within sections, "
        f"even if the layout describes them. The per-tier grouping is already "
        f"done for you.\n\n"
        f"# Project description\n{config.project_description(project)}\n\n"
        f"# Briefing layout\n{config.project_layout(project)}\n\n"
        f"# Papers (JSON)\n{json.dumps(papers_as_dicts(papers), indent=2)}\n"
    )


def _run_agent(agent: str, model: str | None, prompt: str) -> str:
    """Run one synthesis pass and return the agent's markdown output."""
    argv, stdin = _agent_invocation(agent, model, prompt)
    if shutil.which(argv[0]) is None:
        raise RuntimeError(
            f"'{argv[0]}' not found on PATH; install it or set AGENT in .env"
        )
    result = subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=_AGENT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{agent} exited {result.returncode}: {result.stderr.strip()}")
    output = result.stdout.strip()
    if not output:
        raise RuntimeError(f"{agent} produced no output")
    return output


def _run_passes(
    project: str,
    agent: str,
    model: str | None,
    passes: list[tuple[str, list[Paper], str]],
    report: Callable[[str], None],
) -> dict[str, str]:
    """Run every synthesis pass concurrently; return {tier heading: body}.

    The caller is expected to have already reported the per-tier queue (so
    empty tiers are visible too). This function only reports `done` lines as
    futures complete, plus the dict of agent outputs.
    """
    if not passes:
        return {}

    bodies: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(passes)) as pool:
        futures = {
            pool.submit(
                _run_agent, agent, model, build_prompt(project, tier_papers, scope)
            ): heading
            for heading, tier_papers, scope in passes
        }
        for future in as_completed(futures):
            heading = futures[future]
            bodies[heading] = future.result()
            report(f"{heading}: done")
    return bodies


def _already_read_block(papers: list[Paper], today: dt.date | None = None) -> str:
    """Render the trailing '📚 Already read' collapsible block.

    Lists the already-read papers from today's fetch with their submission
    date and tier label, so the briefing tells the user which papers were
    deferred without re-running them through the agent.
    """
    today = today or dt.date.today()
    sorted_papers = sorted(papers, key=lambda p: p.submitted, reverse=True)
    lines = [
        "<details>",
        f"<summary><strong>📚 Already read</strong> ({len(sorted_papers)})</summary>",
        "",
    ]
    for paper in sorted_papers:
        age = (today - dt.date.fromisoformat(paper.submitted)).days
        if age <= fetch.NEW_DAYS:
            tier = "Last 24 hours"
        elif age <= fetch.WEEK_DAYS:
            tier = "Last 7 days"
        else:
            tier = "Earlier"
        lines.append(f"- [{paper.title}]({paper.url}) · {paper.submitted} · *{tier}*")
    lines.append("")
    lines.append("</details>")
    return "\n".join(lines)


def synthesize(
    project: str,
    papers: list[Paper],
    agent: str | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
    out_path: Path | None = None,
    read_papers: list[Paper] | None = None,
) -> Path:
    """Generate the briefing in two recency passes, run in parallel.

    `progress`, if given, is called with a short status line per pass (queued,
    then done) so the caller can show what the two agent calls are doing.
    `out_path`, if given, is the file to write the briefing to. When omitted,
    the briefing lands at ``archive/<today>.md``.
    `read_papers`, if given, are papers from today's fetch that the user has
    already marked as read; they are not sent to the agent, but are appended
    in a collapsed '📚 Already read' section so the audit trail is visible.
    """
    agent = agent or config.agent()
    model = model if model is not None else config.model()
    report = progress or (lambda _message: None)

    tiers = fetch.by_recency(papers)
    window_days = config.recent_days()
    tier_definitions = [
        ("⚡ Last 24 hours", tiers.new, "the last 24 hours"),
        (
            "📅 Last 7 days",
            tiers.this_week,
            "the last 7 days, but more than 24 hours ago",
        ),
        (
            f"🗂️ Last {window_days} days",
            tiers.recent,
            f"the last {window_days} days, but more than 7 days ago",
        ),
    ]
    # Only non-empty tiers reach the agent; empty tiers render a static
    # placeholder so their heading is still visible in the briefing (a
    # disappearing tier reads as "did it crash?" rather than "nothing new").
    # Progress is reported for every tier — including the empty ones — so
    # the user can see at a glance how today's papers split across windows.
    for heading, tier_papers, _scope in tier_definitions:
        suffix = "" if tier_papers else " (skipped)"
        report(f"{heading}: {len(tier_papers)} papers{suffix}")
    passes: list[tuple[str, list[Paper], str]] = [
        (heading, tier_papers, scope)
        for heading, tier_papers, scope in tier_definitions
        if tier_papers
    ]

    bodies = _run_passes(project, agent, model, passes, report)

    today = dt.date.today().isoformat()
    sections = [f"# 🔬 Scoop-watch Briefing — {today}"]
    if not any(tier_papers for _h, tier_papers, _s in tier_definitions):
        sections.append("Nothing notable: no papers in the fetch window.")
    else:
        # Wrap collapsibles per tier so a tier separator never gets swept
        # into the previous tier's <details> body.
        for heading, tier_papers, _scope in tier_definitions:
            if tier_papers:
                body = bodies[heading]
            else:
                body = "_No new papers in this window._"
            sections.append(_wrap_collapsible(f"# {heading}\n\n{body}"))

    if read_papers:
        sections.append(_already_read_block(read_papers))

    if out_path is None:
        out_path = paths.archive_dir(project) / f"{today}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Horizontal rule separator between top-level blocks (title + each tier).
    out_path.write_text("\n\n---\n\n".join(sections) + "\n", encoding="utf-8")
    return out_path
