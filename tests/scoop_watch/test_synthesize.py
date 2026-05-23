"""Tests for agent invocation selection and the two-pass synthesis."""

import datetime as dt

import pytest

from scoop_watch import scaffold, synthesize


def test_claude_invocation_uses_stdin():
    argv, stdin = synthesize._agent_invocation("claude", None, "PROMPT")
    assert argv == ["claude", "-p"]
    assert stdin == "PROMPT"


def test_claude_invocation_with_model():
    argv, stdin = synthesize._agent_invocation("claude", "opus", "PROMPT")
    assert argv == ["claude", "-p", "--model", "opus"]
    assert stdin == "PROMPT"


def test_codex_invocation_uses_stdin():
    argv, stdin = synthesize._agent_invocation("codex", None, "PROMPT")
    assert argv == ["codex", "exec", "--skip-git-repo-check", "-"]
    assert stdin == "PROMPT"


def test_codex_invocation_with_model():
    argv, stdin = synthesize._agent_invocation("codex", "gpt-x", "PROMPT")
    assert argv == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--model",
        "gpt-x",
        "-",
    ]
    assert stdin == "PROMPT"


def test_codex_invocation_skips_git_repo_check():
    """Regression: codex must be told to run outside a git repository, since
    the project data directory is not one."""
    argv, _ = synthesize._agent_invocation("codex", None, "PROMPT")
    assert "--skip-git-repo-check" in argv


def test_codex_prompt_never_passed_as_argv():
    """Regression: a large prompt passed in argv overflows ARG_MAX and raises
    OSError 'Argument list too long'. The prompt must travel on stdin."""
    big_prompt = "x" * 500_000
    argv, stdin = synthesize._agent_invocation("codex", None, big_prompt)
    assert big_prompt not in argv
    assert stdin == big_prompt
    assert argv[-1] == "-"


def test_claude_prompt_never_passed_as_argv():
    """Regression: the claude prompt must travel on stdin, not in argv."""
    big_prompt = "x" * 500_000
    argv, stdin = synthesize._agent_invocation("claude", None, big_prompt)
    assert big_prompt not in argv
    assert stdin == big_prompt


def test_unknown_agent_rejected():
    with pytest.raises(ValueError):
        synthesize._agent_invocation("bard", None, "PROMPT")


def _paper(days_ago: int, index: int) -> synthesize.Paper:
    submitted = (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()
    return synthesize.Paper(
        arxiv_id=f"id{index}",
        title="t",
        authors=[],
        abstract="a",
        submitted=submitted,
        categories=[],
        primary_category="c",
        url="u",
    )


def _stub_project(monkeypatch, tmp_path) -> list[str]:
    """Scaffold a project and capture the prompts passed to the agent."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    prompts: list[str] = []
    monkeypatch.setattr(
        synthesize,
        "_run_agent",
        lambda agent, model, prompt: (prompts.append(prompt), "BODY")[1],
    )
    return prompts


def test_synthesize_runs_one_pass_per_nonempty_tier(monkeypatch, tmp_path):
    prompts = _stub_project(monkeypatch, tmp_path)
    papers = [_paper(0, 1), _paper(3, 2), _paper(40, 3)]  # spans all three tiers

    out = synthesize.synthesize("demo", papers, agent="claude")
    text = out.read_text(encoding="utf-8")

    assert len(prompts) == 3  # one pass per tier: 24h, 7d, window-days
    assert "# 🔬 Scoop-watch Briefing" in text
    assert "⚡ Last 24 hours" in text
    assert "📅 Last 7 days" in text
    assert "🗂️ Last 90 days" in text  # config.recent_days() default is 90
    # horizontal rule between tiers
    assert "\n---\n" in text


def test_synthesize_single_pass_when_only_recent(monkeypatch, tmp_path):
    prompts = _stub_project(monkeypatch, tmp_path)

    out = synthesize.synthesize("demo", [_paper(40, 1), _paper(60, 2)], agent="claude")
    text = out.read_text(encoding="utf-8")

    assert len(prompts) == 1
    assert "🗂️ Last 90 days" in text
    assert "⚡ Last 24 hours" not in text
    assert "📅 Last 7 days" not in text


def test_wrap_collapsible_wraps_helpful_and_broader_with_counts():
    """The low-priority sections are wrapped in <details> with their count."""
    briefing = (
        "## 🚨 Confirmed Scoop\n"
        "**[A](u)** | a | arxiv:1 | submitted 2026\n"
        "> note\n"
        "\n"
        "## 🛠️ Potentially Helpful\n"
        "**[B](u)** | b | arxiv:2 | submitted 2026\n"
        "> note\n"
        "**[C](u)** | c | arxiv:3 | submitted 2026\n"
        "> note\n"
        "\n"
        "## 📡 Broader Field\n"
        "**[D](u)** | d | arxiv:4 | submitted 2026\n"
        "> note\n"
    )
    out = synthesize._wrap_collapsible(briefing)
    assert "<summary><strong>🛠️ Potentially Helpful</strong> (2)</summary>" in out
    assert "<summary><strong>📡 Broader Field</strong> (1)</summary>" in out
    # Confirmed Scoop is left untouched.
    assert "## 🚨 Confirmed Scoop" in out
    assert "<summary><strong>🚨 Confirmed Scoop" not in out


def test_wrap_collapsible_wraps_empty_sections_with_zero_count():
    """Empty sections are still wrapped (with (0)) so every tier renders
    consistently — keeps users from wondering whether the agent dropped a
    section or it was genuinely empty."""
    briefing = "## 🛠️ Potentially Helpful\n\nNothing notable.\n"
    out = synthesize._wrap_collapsible(briefing)
    assert "<summary><strong>🛠️ Potentially Helpful</strong> (0)</summary>" in out
    assert "Nothing notable." in out


def test_wrap_collapsible_also_handles_level_three_headers():
    """Regression: agents sometimes emit ### Section instead of ## Section
    (haiku in particular). Both depths must be wrapped."""
    briefing = (
        "# 🗂️ Earlier in the window\n"
        "\n"
        "### 🛠️ Potentially Helpful\n"
        "**[A](u)** | a | arxiv:1 | submitted 2026\n"
        "> note\n"
        "\n"
        "### 📡 Broader Field\n"
        "**[B](u)** | b | arxiv:2 | submitted 2026\n"
        "> note\n"
        "**[C](u)** | c | arxiv:3 | submitted 2026\n"
        "> note\n"
    )
    out = synthesize._wrap_collapsible(briefing)
    assert "<summary><strong>🛠️ Potentially Helpful</strong> (1)</summary>" in out
    assert "<summary><strong>📡 Broader Field</strong> (2)</summary>" in out


def test_synthesize_appends_already_read_section(monkeypatch, tmp_path):
    """Papers passed via ``read_papers`` land in a trailing '📚 Already read'
    collapsible block, not in the agent's input."""
    prompts = _stub_project(monkeypatch, tmp_path)
    unread = [_paper(0, 1)]  # one new-tier paper for synthesis
    deferred = [_paper(15, 2)]  # already-read, should land in the appendix

    out = synthesize.synthesize("demo", unread, agent="claude", read_papers=deferred)
    text = out.read_text(encoding="utf-8")

    # One synthesis pass (only unread papers were sent to the agent).
    assert len(prompts) == 1
    assert "id2" not in prompts[0]  # the read paper was NOT in the prompt

    # The appendix is in the briefing, with the right count and a link.
    assert "<summary><strong>📚 Already read</strong> (1)</summary>" in text
    assert "[t](u)" in text  # the read paper rendered as a link


def test_synthesize_no_papers_writes_placeholder(monkeypatch, tmp_path):
    prompts = _stub_project(monkeypatch, tmp_path)

    out = synthesize.synthesize("demo", [], agent="claude")

    assert prompts == []
    assert "Nothing notable" in out.read_text(encoding="utf-8")
