"""Tests for the deep-mode batcher, parallel synthesis, merge, and resume."""

from __future__ import annotations

from scoop_watch import scaffold, synthesize_deep
from scoop_watch.fetch import Paper


def _paper(idx: int) -> Paper:
    pid = f"24{idx:02d}.{idx:05d}"
    return Paper(
        arxiv_id=pid,
        title=f"Paper {idx}",
        authors=["Some Author"],
        abstract=f"Abstract for paper {idx}.",
        submitted="2024-06-01",
        categories=["cs.LG"],
        primary_category="cs.LG",
        url=f"http://arxiv.org/abs/{pid}",
        matched_queries=["q (any)"],
    )


def _stub_project_and_agent(monkeypatch, tmp_path):
    """Scaffold a project and replace _run_agent with a deterministic stub.

    The stub returns the prompt verbatim so tests can assert on what was sent
    to which batch.
    """
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    captured: list[str] = []

    def fake_agent(agent, model, prompt):
        captured.append(prompt)
        # The merge prompt embeds 'Batch outputs to merge'; tell merge from batch.
        is_merge = "# Batch outputs to merge" in prompt
        return "MERGED OUTPUT" if is_merge else f"BATCH OUTPUT #{len(captured)}"

    monkeypatch.setattr(synthesize_deep, "_run_agent", fake_agent)
    return captured


def test_batch_splits_into_equal_size_chunks_with_short_tail():
    papers = [_paper(i) for i in range(7)]
    batches = synthesize_deep._batch(papers, 3)
    assert [len(b) for b in batches] == [3, 3, 1]
    # No paper is lost or duplicated.
    flat = [p.arxiv_id for batch in batches for p in batch]
    assert flat == [p.arxiv_id for p in papers]


def test_batch_size_larger_than_corpus_yields_one_batch():
    papers = [_paper(i) for i in range(3)]
    assert len(synthesize_deep._batch(papers, 100)) == 1


def test_synthesize_deep_runs_one_call_per_batch_plus_merge(monkeypatch, tmp_path):
    captured = _stub_project_and_agent(monkeypatch, tmp_path)
    papers = [_paper(i) for i in range(250)]  # 3 batches at size 100

    out = synthesize_deep.synthesize_deep(
        "demo",
        papers,
        years=5,
        date="2026-05-23",
        agent="claude",
        batch_size=100,
        max_workers=2,
    )

    # 3 batch calls + 1 merge call.
    assert len(captured) == 4
    # Merge prompt embeds the date window and total count.
    merge_prompt = next(p for p in captured if "# Batch outputs to merge" in p)
    assert "total_papers: 250" in merge_prompt
    assert "years: 5" in merge_prompt
    assert "2026-05-23" in merge_prompt
    # Final survey lands in deep-archive with the years suffix.
    assert out.name == "2026-05-23_5y.md"
    assert out.read_text(encoding="utf-8") == "MERGED OUTPUT"


def test_synthesize_deep_writes_per_batch_checkpoint(monkeypatch, tmp_path):
    """Each batch's agent output is persisted before the merge runs."""
    _stub_project_and_agent(monkeypatch, tmp_path)
    papers = [_paper(i) for i in range(150)]  # 2 batches at size 100

    synthesize_deep.synthesize_deep(
        "demo",
        papers,
        years=5,
        date="2026-05-23",
        agent="claude",
        batch_size=100,
        max_workers=2,
    )

    batches_dir = tmp_path / "projects" / "demo" / "deep" / "batches" / "2026-05-23"
    files = sorted(batches_dir.glob("batch_*.md"))
    assert [f.name for f in files] == ["batch_00.md", "batch_01.md"]
    # Atomic write: no .tmp left behind.
    assert not list(batches_dir.glob("*.tmp"))


def test_synthesize_deep_resume_skips_existing_batch_files(monkeypatch, tmp_path):
    """A pre-existing batch_NN.md is loaded verbatim; only missing batches
    trigger fresh agent calls. The merge always runs."""
    captured = _stub_project_and_agent(monkeypatch, tmp_path)
    papers = [_paper(i) for i in range(150)]  # 2 batches

    # Pre-populate batch_00.md as if a previous run finished it.
    batches_dir = tmp_path / "projects" / "demo" / "deep" / "batches" / "2026-05-23"
    batches_dir.mkdir(parents=True, exist_ok=True)
    (batches_dir / "batch_00.md").write_text("CACHED BATCH 0", encoding="utf-8")

    synthesize_deep.synthesize_deep(
        "demo",
        papers,
        years=5,
        date="2026-05-23",
        agent="claude",
        batch_size=100,
        max_workers=2,
    )

    # Only batch_01 should have been fired, plus the merge — so 2 captures.
    assert len(captured) == 2
    merge_prompt = next(p for p in captured if "# Batch outputs to merge" in p)
    # The cached batch_00 body must reach the merge.
    assert "CACHED BATCH 0" in merge_prompt


def test_merge_prompt_requires_date_sort_and_collapsible_themes():
    """Regression: the merge prompt must tell the agent (1) sort by submission
    date newest first within each theme, no relevance re-ranking, and (2)
    wrap every theme in a <details> block with its paper count. These two
    requirements together let the reader skim by theme and drill into
    chronologically-sorted entries on demand."""
    from scoop_watch import paths

    text = paths.package_text("synthesis_deep_merge.md")
    assert "Sort by submission date, newest first" in text
    assert "<details>" in text
    assert "<summary><strong>" in text
    # Both singular and plural forms must be documented for the agent.
    assert "N papers" in text
    assert "1 paper" in text


def test_synthesize_deep_with_force_rebuild_uses_caller_cleanup(monkeypatch, tmp_path):
    """synthesize_deep itself does not implement --force; the CLI is
    responsible for wiping outputs before re-invoking. This test pins the
    contract: if batch files exist, they are reused."""
    captured = _stub_project_and_agent(monkeypatch, tmp_path)
    papers = [_paper(i) for i in range(50)]  # 1 batch

    batches_dir = tmp_path / "projects" / "demo" / "deep" / "batches" / "2026-05-23"
    batches_dir.mkdir(parents=True, exist_ok=True)
    (batches_dir / "batch_00.md").write_text("STALE", encoding="utf-8")

    synthesize_deep.synthesize_deep(
        "demo",
        papers,
        years=5,
        date="2026-05-23",
        agent="claude",
        batch_size=100,
        max_workers=1,
    )

    # No fresh batch call; only the merge ran.
    assert len(captured) == 1
    assert "# Batch outputs to merge" in captured[0]
