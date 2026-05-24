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
    assert (
        "<summary><big><strong>📂 Solo &nbsp;·&nbsp; 1 paper</strong></big></summary>"
        in merged
    )
    assert (
        "<summary><big><strong>📂 Pair &nbsp;·&nbsp; 2 papers</strong></big></summary>"
        in merged
    )
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
    assert merged.find("📂 Z ") < merged.find("📂 A ")
    assert merged.find("📂 A ") < merged.find("📂 M ")


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


def test_merge_canonicalizes_theme_names_across_dash_variants(tmp_path, monkeypatch):
    """Real-world failure: one batch wrote a theme with an en-dash, another
    with a hyphen — the merger duplicated the theme. After canonicalization
    they collapse onto the project's declared spelling."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    # Declared spelling uses an en-dash.
    (tmp_path / "projects" / "demo" / "project.md").write_text(
        "## Theme: Learning the Kohn–Sham diagonalization step\n", encoding="utf-8"
    )

    batch_en = _batch_md(
        confirmed_by_theme={
            "Learning the Kohn–Sham diagonalization step": [("p1", "2025-01")]
        },
        potential_by_theme={},
    )
    batch_hyphen = _batch_md(
        confirmed_by_theme={
            "Learning the Kohn-Sham diagonalization step": [("p2", "2024-06")]
        },
        potential_by_theme={},
    )

    merged = synthesize_deep._programmatic_merge(
        "demo",
        [batch_en, batch_hyphen],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=2,
    )
    # One theme bucket, two papers, declared spelling wins.
    assert (
        "<summary><big><strong>📂 Learning the Kohn–Sham diagonalization step &nbsp;·&nbsp; 2 papers</strong></big></summary>"
        in merged
    )
    # The hyphen-only variant does NOT survive as a separate theme bucket.
    assert "📂 Learning the Kohn-Sham diagonalization step" not in merged


def test_merge_canonicalization_is_case_insensitive(tmp_path, monkeypatch):
    """A batch that lower-cased a theme name still merges onto the declared
    spelling."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    (tmp_path / "projects" / "demo" / "project.md").write_text(
        "## Theme: Equivariant Message Passing\n", encoding="utf-8"
    )
    batch = _batch_md(
        confirmed_by_theme={"equivariant message passing": [("p", "2024-01")]},
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=1,
    )
    assert "📂 Equivariant Message Passing " in merged


def test_merge_groups_entries_by_year_within_each_theme():
    """Inside each theme `<details>`, entries are sub-grouped by submission
    year so a 30-paper theme reads as N smaller piles rather than one list."""
    batch = _batch_md(
        confirmed_by_theme={
            "T": [
                ("MARKER-2025A", "2025-10"),
                ("MARKER-2025B", "2025-03"),
                ("MARKER-2024A", "2024-08"),
                ("MARKER-2023A", "2023-12"),
                ("MARKER-2023B", "2023-01"),
            ]
        },
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=5,
    )
    # Year headings with per-year counts, in date-descending order.
    assert "<strong>📅 2025 &nbsp;·&nbsp; 2 papers</strong>" in merged
    assert "<strong>📅 2024 &nbsp;·&nbsp; 1 paper</strong>" in merged
    assert "<strong>📅 2023 &nbsp;·&nbsp; 2 papers</strong>" in merged
    # Year groups appear newest-first.
    assert (
        merged.find("<strong>📅 2025 ")
        < merged.find("<strong>📅 2024 ")
        < merged.find("<strong>📅 2023 ")
    )
    # Within a year, entries stay date-descending (2025-10 before 2025-03).
    assert merged.find("MARKER-2025A") < merged.find("MARKER-2025B")


def test_date_fallback_recovers_from_arxiv_id_when_author_line_is_implausible():
    """Real-world failure: the agent writes ``· 2506-06`` (the arxiv id YYMM
    prefix re-used as a fake YYYY-MM). The parser rejects the bogus year,
    then recovers ``2025-06`` from the arxiv id link itself."""
    text = (
        "**Some Paper**\n"
        "Author X · [arxiv:2506.06623](http://arxiv.org/abs/2506.06623) · 2506-06\n\n"
        "Analysis.\n**Distinction.**"
    )
    assert synthesize_deep._resolve_entry_date(text) == "2025-06"


def test_date_fallback_recovers_from_arxiv_id_when_author_line_has_day():
    """Real-world failure: the agent appends the day, producing
    ``· 2025-09-30``. The regex now tolerates the trailing ``-DD``."""
    text = (
        "**Some Paper**\n"
        "Author X · [arxiv:2509.25724](http://arxiv.org/abs/2509.25724) · 2025-09-30\n\n"
        "Analysis.\n**Distinction.**"
    )
    assert synthesize_deep._resolve_entry_date(text) == "2025-09"


def test_date_fallback_uses_arxiv_id_when_author_line_date_is_missing_entirely():
    """If the author line has no date at all, the arxiv id link is still a
    reliable source (any post-April-2007 paper)."""
    text = (
        "**Some Paper**\n"
        "Author X · [arxiv:2401.12345](http://arxiv.org/abs/2401.12345)\n\n"
        "Analysis.\n**Distinction.**"
    )
    assert synthesize_deep._resolve_entry_date(text) == "2024-01"


def test_date_resolution_returns_empty_when_no_source_is_recoverable():
    """A pre-2007 paper with an old-format identifier (no YYMM prefix) and
    no author-line date leaves the entry truly undated."""
    text = "**Some Paper**\nAuthor X · hep-th/0405123\n\nAnalysis.\n**Distinction.**"
    assert synthesize_deep._resolve_entry_date(text) == ""


def test_merge_year_sanity_check_rejects_arxiv_id_prefix_as_year():
    """Real failure observed in production: the agent wrote dates like
    `2509-09` (the arxiv id YYMM prefix duplicated) instead of `2025-09`.
    The parser must reject implausible years (outside 1900-2100) and place
    those entries in the trailing Undated bucket rather than creating a
    `#### 2509` heading polluting the survey."""
    # Mix of plausible (2025) and arxiv-id-shaped (2509) dates.
    batch = _batch_md(
        confirmed_by_theme={
            "T": [
                ("MARKER-GOOD", "2025-09"),
                ("MARKER-BOGUS-YEAR", "2509-09"),
            ]
        },
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=2,
    )
    assert "<strong>📅 2025 &nbsp;·&nbsp; 1 paper</strong>" in merged
    assert "<strong>📅 Undated &nbsp;·&nbsp; 1 paper</strong>" in merged
    # The bogus year must NOT have become its own heading.
    assert "<strong>2509</strong>" not in merged
    # Both entries still surface — the bad-date one just lands in Undated.
    assert "MARKER-GOOD" in merged
    assert "MARKER-BOGUS-YEAR" in merged


def test_merge_year_grouping_puts_undated_entries_in_a_trailing_bucket():
    """Entries with a missing/unparseable date fall into a trailing 'Undated'
    year bucket so they remain visible instead of silently disappearing."""
    bad = "**Paper Without Date**\nSome Author · [arxiv:00.00](http://x)\n\nAnalysis.\n**Distinction.**"
    good_entry = _entry("Good Paper", "2024-06")
    body = (
        "## 🚨 Confirmed Scoop\n\n### T\n\n"
        + good_entry
        + "\n\n---\n\n"
        + bad
        + "\n\n---\n\n## ⚠️ Potential Scoop\n"
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [body],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=2,
    )
    assert "<strong>📅 2024 &nbsp;·&nbsp; 1 paper</strong>" in merged
    assert "<strong>📅 Undated &nbsp;·&nbsp; 1 paper</strong>" in merged
    # Undated bucket trails the real-year buckets.
    assert merged.find("<strong>📅 2024 ") < merged.find("<strong>📅 Undated ")


def test_merge_recovers_from_all_production_drift_modes_at_once(tmp_path, monkeypatch):
    """End-to-end regression for the failure modes observed in the first
    real deep run on the ofdft project:

      A. theme name with en-dash where the project uses en-dash (canonical
         path: identical strings, no canonicalization needed)
      B. theme name with hyphen where the project uses en-dash (must
         canonicalize onto the declared spelling)
      C. theme name lower-cased (must canonicalize)
      D. author-line date is the arxiv YYMM prefix `2506-06` (implausible
         year; must recover `2025-06` from the arxiv id)
      E. author-line date includes the day `2025-09-30` (regex must tolerate)
      F. author-line date is missing entirely (must recover from arxiv id)

    A single coherent survey must emerge: one theme bucket (not three), all
    five papers in valid year sub-buckets, no `Undated` bucket, no `#### 2506`
    pollution. Every one of these failure modes broke the production output
    before this regression was added.
    """
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    # Declared spelling uses an en-dash, to mirror the real ofdft project.
    (tmp_path / "projects" / "demo" / "project.md").write_text(
        "## Theme: Learning the Kohn–Sham diagonalization step\n", encoding="utf-8"
    )

    def entry(title: str, author_date: str, arxiv_id: str) -> str:
        return (
            f"**{title}**\n"
            f"Author X · [arxiv:{arxiv_id}](http://arxiv.org/abs/{arxiv_id}) · {author_date}\n\n"
            f"Three-sentence analysis here. Same family as the project.\n"
            f"**Bolded distinction line.**"
        )

    def entry_no_date(title: str, arxiv_id: str) -> str:
        return (
            f"**{title}**\n"
            f"Author X · [arxiv:{arxiv_id}](http://arxiv.org/abs/{arxiv_id})\n\n"
            f"Three-sentence analysis here.\n**Distinction.**"
        )

    # Three batches, each contributing a different drift mode + the canonical case.
    batch_a = (
        "## 🚨 Confirmed Scoop\n\n"
        "### Learning the Kohn–Sham diagonalization step\n\n"  # case A — canonical
        + entry("PAPER-A1", "2025-09-30", "2509.00001")  # case E — date with day
        + "\n\n---\n\n"
        + "## ⚠️ Potential Scoop\n"
    )
    batch_b = (
        "## 🚨 Confirmed Scoop\n\n"
        "### Learning the Kohn-Sham diagonalization step\n\n"  # case B — hyphen variant
        + entry("PAPER-B1", "2506-06", "2506.06623")  # case D — arxiv prefix as date
        + "\n\n---\n\n"
        + "## ⚠️ Potential Scoop\n"
    )
    batch_c = (
        "## 🚨 Confirmed Scoop\n\n"
        "### learning the kohn-sham diagonalization step\n\n"  # case C — lowercase + hyphen
        + entry_no_date("PAPER-C1", "2401.12345")  # case F — no author-line date
        + "\n\n---\n\n"
        + "## ⚠️ Potential Scoop\n"
    )

    merged = synthesize_deep._programmatic_merge(
        "demo",
        [batch_a, batch_b, batch_c],
        years=5,
        start_date="2021-01-01",
        end_date="2026-05-24",
        total_papers=3,
    )

    # One theme, one bucket — the three variants collapsed onto the declared
    # spelling. None of the variant spellings appears as its own theme.
    declared = "Learning the Kohn–Sham diagonalization step"
    assert (
        f"<summary><big><strong>📂 {declared} &nbsp;·&nbsp; 3 papers</strong></big></summary>"
        in merged
    )
    assert "📂 Learning the Kohn-Sham" not in merged  # no hyphen variant
    assert "<strong>learning the kohn" not in merged  # no lower-cased variant

    # All three papers reach the survey, in plausible year sub-buckets.
    #   PAPER-A1: arxiv 2509.00001 → 2025-09 (and author-line 2025-09 matches)
    #   PAPER-B1: arxiv 2506.06623 → 2025-06 (author-line `2506-06` rejected,
    #              arxiv-id fallback kicks in)
    #   PAPER-C1: arxiv 2401.12345 → 2024-01 (author-line has no date at all)
    # → 2025 has 2 papers, 2024 has 1; no `2506`/`2509` pollution.
    assert "PAPER-A1" in merged
    assert "PAPER-B1" in merged
    assert "PAPER-C1" in merged
    assert "<strong>📅 2025 &nbsp;·&nbsp; 2 papers</strong>" in merged
    assert "<strong>📅 2024 &nbsp;·&nbsp; 1 paper</strong>" in merged
    assert "<strong>2506</strong>" not in merged
    assert "<strong>2509</strong>" not in merged

    # No Undated bucket — the arxiv id fallback rescued every entry.
    assert "Undated" not in merged

    # Summary arithmetic adds up: 3 surfaced, 0 non-overlapping.
    assert "3 confirmed scoops requiring response" in merged
    assert "0 potential scoops worth monitoring" in merged
    assert "0 papers reviewed and judged non-overlapping" in merged


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


def test_render_entry_wraps_in_details_summary_holds_title_authors_date():
    """Per-entry collapse: the summary line shows title + authors + date so
    the reader can skim; the analysis is hidden until clicked."""
    entry = synthesize_deep._Entry(
        date="2025-09",
        text=(
            "**V2Rho-FNO: Fourier Neural Operator**\n"
            "Jin et al. · [arxiv:2603.15669](http://arxiv.org/abs/2603.15669) · 2025-09\n\n"
            "Trains an FNO to map external potentials to ground-state electron densities.\n"
            "**Closest neighbor to the OF-DFT contribution.**"
        ),
    )
    rendered = synthesize_deep._render_entry(entry)
    assert rendered.startswith("<details>")
    assert rendered.endswith("</details>")
    # Summary holds title (bold) + authors and date in `<small>` so the entry
    # line reads visibly lighter and smaller than the parent year/theme.
    assert (
        "<summary><strong>V2Rho-FNO: Fourier Neural Operator</strong> "
        "<small>· Jin et al. · 2025-09</small></summary>"
    ) in rendered
    # Arxiv link lives inside the collapsed body, not in the summary.
    assert "[arxiv:2603.15669](http://arxiv.org/abs/2603.15669)" in rendered
    assert "[arxiv:2603.15669]" not in rendered.split("</summary>")[0]
    # Analysis + distinction line reach the body verbatim.
    assert "Trains an FNO" in rendered
    assert "**Closest neighbor to the OF-DFT contribution.**" in rendered


def test_theme_body_is_wrapped_in_blockquote_for_visible_indentation():
    """Regression for the layout: theme `<details>` wraps its year-group
    body in `<blockquote>`, and each year `<details>` wraps its entry body
    in `<blockquote>` too. Two levels of GitHub blockquote indent + left
    bar — the only way to get visible nested indentation on GitHub, which
    does not indent nested `<details>` by default."""
    batch = _batch_md(
        confirmed_by_theme={"T": [("p1", "2025-01")]},
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=1,
    )
    # Two blockquote pairs for the theme → year → entry chain.
    assert merged.count("<blockquote>") >= 2
    assert merged.count("</blockquote>") >= 2
    # Theme summary comes BEFORE the first blockquote (the blockquote wraps
    # the year group INSIDE the theme details, not the theme itself).
    assert merged.find("<summary><big><strong>📂") < merged.find("<blockquote>")


def test_render_year_group_uses_markdown_heading_and_per_entry_collapsibles():
    """Year buckets are real `#### ` markdown headings (not collapsible),
    followed by one `<details>` per entry. Triple-nested collapsibles were
    fragile on GitHub (h3-in-summary collided with the chevron, nested
    toggles rendered inconsistently across viewers) — markdown headings
    give natural visual hierarchy without the rendering quirks, and the
    only collapsing the reader needs is the per-entry analysis."""
    e1 = synthesize_deep._Entry(
        date="2025-09",
        text="**T1**\nA · [arxiv:2509.0001](http://x) · 2025-09\n\nAnalysis 1.\n**D1.**",
    )
    e2 = synthesize_deep._Entry(
        date="2025-03",
        text="**T2**\nB · [arxiv:2503.0002](http://y) · 2025-03\n\nAnalysis 2.\n**D2.**",
    )
    rendered = synthesize_deep._render_year_group("2025", [e1, e2])
    # Year is a collapsible `<details>` with an inline `<strong>` summary
    # (inline so the chevron stays attached to the visible label).
    assert rendered.startswith(
        "<details>\n<summary><strong>📅 2025 &nbsp;·&nbsp; 2 papers</strong></summary>"
    )
    # 1 year + 2 entries = 3 `<details>` blocks total.
    assert rendered.count("<details>") == 3
    assert rendered.count("</details>") == 3
    # Entries appear in the order received (caller sorts by date beforehand).
    assert rendered.find("T1") < rendered.find("T2")
    # The entries body is wrapped in `<blockquote>` so GitHub renders it
    # with both an indent and a left bar — nested `<details>` alone is NOT
    # indented by GitHub's stylesheet.
    assert "<blockquote>" in rendered
    assert "</blockquote>" in rendered


def test_merged_survey_includes_table_of_contents_linking_to_section_anchors():
    """A `## Contents` block lists each section with its count and per-theme
    breakdown. Section links use stable explicit `<a name>` anchors, not
    GitHub's auto-anchors (those are platform-dependent and unstable)."""
    batch = _batch_md(
        confirmed_by_theme={"Theme1": [("p1", "2025-01")]},
        potential_by_theme={"Theme1": [("p2", "2024-06")]},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=2,
    )
    assert "## Contents" in merged
    # Section links + counts.
    assert "[🚨 Confirmed Scoop](#confirmed-scoop) (1)" in merged
    assert "[⚠️ Potential Scoop](#potential-scoop) (1)" in merged
    # Per-theme rollup (no links, just labels + counts).
    assert "- Theme1 (1 paper)" in merged
    # Explicit anchor targets exist for the TOC links to land on.
    assert '<a name="confirmed-scoop"></a>' in merged
    assert '<a name="potential-scoop"></a>' in merged
    # TOC appears before the section bodies.
    assert merged.find("## Contents") < merged.find('<a name="confirmed-scoop"></a>')


def test_merged_survey_has_no_cross_cutting_latest_block():
    """Regression: the `## ⚡ Latest N months` block was dropped — the
    multi-year survey is for stable inspection, not a "what's new this week"
    digest (the daily run already covers that). Including a Latest block
    duplicated content and added noise above the actual survey body."""
    batch = _batch_md(
        confirmed_by_theme={"T": [("p", "2025-12")]},
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=1,
    )
    assert "Latest" not in merged or "⚡" not in merged


def test_merged_survey_separates_themes_with_horizontal_rules():
    """Inside each section, adjacent themes are separated by `---` rules so
    the reader sees a visual cut between theme blocks even when every theme
    is collapsed and would otherwise stack flush."""
    batch = _batch_md(
        confirmed_by_theme={
            "ThemeAlpha": [("a", "2024-01")],
            "ThemeBeta": [("b", "2024-01")],
            "ThemeGamma": [("c", "2024-01")],
        },
        potential_by_theme={},
    )
    merged = synthesize_deep._programmatic_merge(
        "demo_no_themes",
        [batch],
        years=5,
        start_date="2020-01-01",
        end_date="2025-12-31",
        total_papers=3,
    )
    # Slice between Confirmed section header and Potential section header.
    confirmed_block = merged.split('<a name="confirmed-scoop">')[1].split(
        '<a name="potential-scoop">'
    )[0]
    # 3 themes ⇒ 2 horizontal rules between them, plus one before the section
    # opens (the doc-level summary/TOC ⇄ section divider) — confirm there is
    # at least one inter-theme rule.
    rules = [line for line in confirmed_block.splitlines() if line.strip() == "---"]
    assert len(rules) >= 2


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
