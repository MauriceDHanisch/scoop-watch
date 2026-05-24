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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    # from the executor above).
    assert all(body is not None for body in bodies), "every batch must have a body"
    end = dt.date.fromisoformat(date)
    start = end - dt.timedelta(days=years * 365)
    report(f"merging {total} batch output(s) into the final survey...")
    merged = _run_agent(
        agent,
        model,
        _merge_prompt(
            project,
            [body for body in bodies if body is not None],
            years,
            start.isoformat(),
            end.isoformat(),
            len(papers),
        ),
    )

    archive = paths.deep_archive_dir(project)
    archive.mkdir(parents=True, exist_ok=True)
    out_path = archive / f"{date}_{years}y.md"
    tmp = out_path.with_suffix(".md.tmp")
    tmp.write_text(merged, encoding="utf-8")
    tmp.rename(out_path)
    return out_path
