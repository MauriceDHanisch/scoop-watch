"""Tests for the deterministic deep-mode merger.

Replaces what used to be an LLM merge pass (~8 min / ~$1 per deep run). The
merge is mechanical: parse batch outputs, bucket by (section, theme), sort by
date descending, wrap themes in `<details>`. Tests pin every piece of that.
"""

from __future__ import annotations

import textwrap

from scoop_watch import scaffold, synthesize_deep


def _entry(title: str, date: str) -> str:
    """Build one batch-format entry block, exactly as the per-batch agent
    is told to emit (see synthesis_deep.md)."""
    return textwrap.dedent(
        f"""\
        **{title}**
        Some Author, Other Author · [arxiv:24XX.{title.replace(" ", "")}](http://example.com) · {date}

        Three to five sentences of analysis go here. The paper does X with Y,
        which overlaps with the project on the Z axis. Same methodology family,
        different target.
        **One bolded distinction line that the author of the project should
        re-read in six months.**
        """
    ).strip()


def _batch_md(confirmed_by_theme: dict, potential_by_theme: dict) -> str:
    """Build a synthetic batch markdown body in the exact format the
    per-batch agent emits, given a dict of {theme: [(title, date), ...]}."""
    sections = []
    sections.append("## 🚨 Confirmed Scoop")
    for theme, papers in confirmed_by_theme.items():
        sections.append(f"\n### {theme}\n")
        for title, date in papers:
            sections.append(_entry(title, date) + "\n\n---\n")
    sections.append("\n## ⚠️ Potential Scoop")
    for theme, papers in potential_by_theme.items():
        sections.append(f"\n### {theme}\n")
        for title, date in papers:
            sections.append(_entry(title, date) + "\n\n---\n")
    return "\n".join(sections)


def test_parse_batch_buckets_entries_by_section_and_theme():
    body = _batch_md(
        confirmed_by_theme={"A": [("p1", "2025-09"), ("p2", "2024-03")]},
        potential_by_theme={"B": [("p3", "2025-01")]},
    )
    parsed = synthesize_deep._parse_batch(body)
    keys = sorted(parsed.by_section_theme.keys())
    assert keys == [("⚠️ Potential Scoop", "B"), ("🚨 Confirmed Scoop", "A")]
    assert len(parsed.by_section_theme[("🚨 Confirmed Scoop", "A")]) == 2
    # Dates are pulled off the author line for sorting.
    dates = [e.date for e in parsed.by_section_theme[("🚨 Confirmed Scoop", "A")]]
    assert dates == ["2025-09", "2024-03"]


def test_parse_batch_drops_non_entry_prose():
    """A batch that disregarded the format and wrote prose under a section
    must not pollute the merger — only `**...**`-starting entries count."""
    body = (
        "## 🚨 Confirmed Scoop\n\n"
        "### Theme A\n\n"
        "Nothing notable in this batch.\n\n"
        "---\n\n"
        "## ⚠️ Potential Scoop\n\n"
        "### Theme A\n\n" + _entry("Real Paper", "2024-06") + "\n\n---\n"
    )
    parsed = synthesize_deep._parse_batch(body)
    assert parsed.by_section_theme.get(("🚨 Confirmed Scoop", "Theme A"), []) == []
    assert len(parsed.by_section_theme[("⚠️ Potential Scoop", "Theme A")]) == 1


def test_merge_sorts_entries_by_date_descending_across_batches():
    """Two batches contribute to the same theme; merge sorts entries newest-
    first regardless of which batch they came from or what order they appear."""
    # Use unambiguous title tokens that cannot collide with substring matches
    # inside the boilerplate analysis text (e.g. "**bolded**" contains "old").
    batch_a = _batch_md(
        confirmed_by_theme={"T": [("ZZZ-OLDEST", "2023-01"), ("ZZZ-NEWEST", "2025-12")]},
        potential_by_theme={},
    )
    batch_b = _batch_md(
        confirmed_by_theme={"T": [("ZZZ-MIDDLE", "2024-06")]},
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",  # project does not exist on disk; declared_order=[]
        [batch_a, batch_b],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=3,
    )
    # Order in the merged output is newest → oldest.
    assert (
        merged.find("ZZZ-NEWEST") < merged.find("ZZZ-MIDDLE") < merged.find("ZZZ-OLDEST")
    )


def test_merge_wraps_each_theme_in_details_with_paper_count():
    batch = _batch_md(
        confirmed_by_theme={"Solo": [("a", "2024-01")]},
        potential_by_theme={"Pair": [("b", "2024-01"), ("c", "2023-12")]},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=3,
    )
    # Singular vs plural form.
    assert "<summary><strong>Solo</strong> (1 paper)</summary>" in merged
    assert "<summary><strong>Pair</strong> (2 papers)</summary>" in merged
    # Section heading counts reflect the totals.
    assert "## 🚨 Confirmed Scoop (1)" in merged
    assert "## ⚠️ Potential Scoop (2)" in merged
    # Summary block has the three counts and the non-overlapping arithmetic.
    assert "1 confirmed scoops requiring response" in merged
    assert "2 potential scoops worth monitoring" in merged
    assert "0 papers reviewed and judged non-overlapping" in merged  # 3 - 3


def test_merge_orders_themes_by_project_declaration(tmp_path, monkeypatch):
    """Themes appear in the order they were declared in project.md, regardless
    of the order the batches happened to emit them."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    # Rewrite project.md so the declared order is Z, A, M.
    (tmp_path / "projects" / "demo" / "project.md").write_text(
        textwrap.dedent(
            """\
            # Project: ordering test

            ## Theme: Z
            First theme.

            ## Theme: A
            Second theme.

            ## Theme: M
            Third theme.
            """
        ),
        encoding="utf-8",
    )
    # Batch emits the themes in arbitrary order.
    batch = _batch_md(
        confirmed_by_theme={
            "A": [("a", "2024-01")],
            "M": [("m", "2024-01")],
            "Z": [("z", "2024-01")],
        },
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=3,
    )
    # Z first, then A, then M (project.md order).
    assert merged.find("<strong>Z</strong>") < merged.find("<strong>A</strong>")
    assert merged.find("<strong>A</strong>") < merged.find("<strong>M</strong>")


def test_merge_puts_unknown_themes_alphabetically_after_declared(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    (tmp_path / "projects" / "demo" / "project.md").write_text(
        "## Theme: KnownTheme\nbody\n", encoding="utf-8"
    )
    batch = _batch_md(
        confirmed_by_theme={
            "ZetaInvented": [("z", "2024-01")],
            "KnownTheme": [("k", "2024-01")],
            "AlphaInvented": [("a", "2024-01")],
        },
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=3,
    )
    # KnownTheme first; undeclared themes alphabetical.
    assert (
        merged.find("KnownTheme")
        < merged.find("AlphaInvented")
        < merged.find("ZetaInvented")
    )


def test_merge_drops_empty_themes_and_handles_no_entries():
    """A section with no entries at all still renders its heading and `(0)`."""
    empty_batch = "## 🚨 Confirmed Scoop\n\n## ⚠️ Potential Scoop\n"
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [empty_batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=100,
    )
    assert "## 🚨 Confirmed Scoop (0)" in merged
    assert "## ⚠️ Potential Scoop (0)" in merged
    # No `<details>` blocks because there are no themes with entries.
    assert "<details>" not in merged
    # 100 - 0 surfaced = 100 non-overlapping.
    assert "100 papers reviewed and judged non-overlapping" in merged


def test_merge_warns_when_a_batch_parses_to_zero_entries():
    """A non-empty batch that produces no parseable entries almost always
    means the agent drifted from the format contract; the merger reports
    each such batch so the user can re-inspect and re-fire it."""
    good = _batch_md(
        confirmed_by_theme={"T": [("p1", "2024-01")]},
        potential_by_theme={},
    )
    drifted = (
        "# Survey\nHere are some papers I found:\n\n"
        "1. Some Title (2024) — analysis here\n"
    )  # totally off-spec: no `##` sections, no `**` entries
    warnings: list[str] = []
    synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [good, drifted],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=2,
        on_warning=warnings.append,
    )
    assert any("batch 2 parsed to 0 entries" in w for w in warnings), warnings
    # The well-formed batch is unaffected by the warning.
    assert not any("batch 1" in w for w in warnings)


def test_merge_does_not_warn_on_legitimately_empty_batch():
    """A batch with the two section headings and no entries (the contract
    for 'no scoops in this batch') is not an error — no warning fires."""
    empty_but_correct = "## 🚨 Confirmed Scoop\n\n## ⚠️ Potential Scoop\n"
    warnings: list[str] = []
    synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [empty_but_correct],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=1,
        on_warning=warnings.append,
    )
    assert warnings == []


def test_synthesis_prompt_documents_the_strict_format_contract():
    """Regression: the prompt must explicitly tell the agent the output is
    parsed by a regex. The contract must name (in some form) every element
    the parser relies on, so any drift the agent might do is named-and-
    forbidden in the prompt."""
    from scoop_watch import paths

    text = paths.package_text("synthesis_deep.md")
    # The contract is announced.
    assert "regex parser" in text.lower() or "regex" in text.lower()
    # Each parser-load-bearing element is named with its exact form.
    assert "## 🚨 Confirmed Scoop" in text
    assert "## ⚠️ Potential Scoop" in text
    assert "### " in text  # theme-heading depth pinned
    assert "**<title" in text.lower() or "**<paper title" in text.lower()
    # Author-line + date end-of-line rule documented.
    assert "YYYY-MM" in text
    assert "·" in text  # the exact middle-dot separator is shown
    # `---` rule discipline is documented.
    assert "---" in text


def test_merge_preserves_entry_text_verbatim():
    """The merger never paraphrases; every entry's analysis block reaches the
    final output exactly as the per-batch agent wrote it."""
    distinctive = "ZZZ-UNIQUE-MARKER-THE-AGENT-WROTE-THIS-PHRASE"
    body = textwrap.dedent(
        f"""\
        ## 🚨 Confirmed Scoop

        ### T

        **Paper Title**
        Author · [arxiv:00.00](http://x) · 2024-01

        Analysis with {distinctive} inside.
        **A bolded distinction line.**

        ---

        ## ⚠️ Potential Scoop
        """
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [body],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=1,
    )
    assert distinctive in merged
