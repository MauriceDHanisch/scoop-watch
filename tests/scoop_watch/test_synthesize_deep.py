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
        """Return a well-formed (but empty) batch body so the programmatic
        merger has something parseable to work with. The merger is exercised
        end-to-end here; its own behaviour is tested in test_merge_deep.py."""
        captured.append(prompt)
        return "## 🚨 Confirmed Scoop\n\n## ⚠️ Potential Scoop\n"

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


def test_synthesize_deep_runs_one_agent_call_per_batch_no_merge_call(
    monkeypatch, tmp_path
):
    """The merge is now deterministic Python — only the per-batch synthesis
    fires the agent. 3 batches → 3 calls, not 4."""
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

    assert len(captured) == 3, "no LLM merge call any more — only per-batch"
    assert out.name == "2026-05-23_5y.md"
    # The final survey is emitted by the programmatic merger and has its
    # header even when the (stubbed) batches produced no entries.
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# 🔬 Scoop-watch Deep Survey — demo")
    assert "5-year window" in text
    assert "250 papers scanned" in text


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

    # Pre-populate batch_00.md as if a previous run finished it. The body
    # is in the per-batch format so the merger surfaces its entries verbatim
    # — a distinctive phrase lets us assert the cached content reached the
    # final survey without an extra agent call.
    batches_dir = tmp_path / "projects" / "demo" / "deep" / "batches" / "2026-05-23"
    batches_dir.mkdir(parents=True, exist_ok=True)
    (batches_dir / "batch_00.md").write_text(
        "## 🚨 Confirmed Scoop\n\n### CachedTheme\n\n"
        "**Cached Paper Title**\n"
        "Some Author · [arxiv:00.00](http://x) · 2025-03\n\n"
        "Analysis with CACHED-BATCH-MARKER-PHRASE inside.\n"
        "**A bolded distinction line.**\n\n"
        "---\n\n"
        "## ⚠️ Potential Scoop\n",
        encoding="utf-8",
    )

    synthesize_deep.synthesize_deep(
        "demo",
        papers,
        years=5,
        date="2026-05-23",
        agent="claude",
        batch_size=100,
        max_workers=2,
    )

    # Only batch_01 should have been fired (the merge is now in-process).
    assert len(captured) == 1
    # The cached batch_00 body must reach the final survey verbatim.
    out_path = tmp_path / "projects" / "demo" / "deep" / "archive" / "2026-05-23_5y.md"
    assert "CACHED-BATCH-MARKER-PHRASE" in out_path.read_text(encoding="utf-8")


def test_synthesize_deep_with_force_rebuild_uses_caller_cleanup(monkeypatch, tmp_path):
    """synthesize_deep itself does not implement --force; the CLI is
    responsible for wiping outputs before re-invoking. This test pins the
    contract: if batch files exist, they are reused (no agent calls at all
    since the merge is now in-process)."""
    captured = _stub_project_and_agent(monkeypatch, tmp_path)
    papers = [_paper(i) for i in range(50)]  # 1 batch

    batches_dir = tmp_path / "projects" / "demo" / "deep" / "batches" / "2026-05-23"
    batches_dir.mkdir(parents=True, exist_ok=True)
    (batches_dir / "batch_00.md").write_text(
        "## 🚨 Confirmed Scoop\n\n## ⚠️ Potential Scoop\n", encoding="utf-8"
    )

    synthesize_deep.synthesize_deep(
        "demo",
        papers,
        years=5,
        date="2026-05-23",
        agent="claude",
        batch_size=100,
        max_workers=1,
    )

    # No agent calls at all: the cached batch is reused and the merge is
    # programmatic.
    assert captured == []
