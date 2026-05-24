# Deep survey merge

You are merging N partial survey outputs (one per batch) into a single
coherent deep survey. Each partial uses the format described in the original
synthesis_deep prompt: two top-level sections (`## 🚨 Confirmed Scoop` and
`## ⚠️ Potential Scoop`), each containing zero or more entries grouped under
`### <theme name>` sub-headings.

## What to produce

A single document with this structure. Each theme inside a section is wrapped
in a `<details>` block that renders as a collapsed group on GitHub, showing
the theme name and its paper count in the summary line. The reader expands
only the themes they want to read in full.

```
# 🔬 Scoop-watch Deep Survey — <project>
*<years>-year window: <start_date> → <end_date> · <total_papers> papers
scanned · <surfaced> surfaced*

## Summary
- <N> confirmed scoops requiring response
- <M> potential scoops worth monitoring
- <K> papers reviewed and judged non-overlapping

---

## 🚨 Confirmed Scoop (<N>)

<details>
<summary><strong>&lt;Theme 1&gt;</strong> (&lt;count&gt; papers)</summary>

<entries, sorted by submission date newest first>

</details>

<details>
<summary><strong>&lt;Theme 2&gt;</strong> (&lt;count&gt; papers)</summary>

<entries, sorted by submission date newest first>

</details>

## ⚠️ Potential Scoop (<M>)

<details>
<summary><strong>&lt;Theme 1&gt;</strong> (&lt;count&gt; papers)</summary>

<entries, sorted by submission date newest first>

</details>
```

**Important:** keep the blank line after `<summary>...</summary>` and before
`</details>`. Markdown inside `<details>` only renders when the surrounding
HTML tags have blank lines around them.

`<project>`, `<years>`, `<start_date>`, `<end_date>`, `<total_papers>`,
`<surfaced>`, `<N>`, `<M>`, `<K>` are provided in the data block at the end
of the prompt — substitute them literally.

## Merging rules

1. **Preserve every entry from every batch.** Do not summarise, paraphrase,
   or shorten. Each entry's body (title, author line, analysis, bolded
   distinction line, closing `---`) is copied verbatim from the source
   batch.

2. **Deduplicate by arxiv id.** If the same arxiv id appears in multiple
   batches (it should not, but allow for it), keep the longest analysis.

3. **Sort by submission date, newest first.** Within each theme, order
   entries by the YYYY-MM date on the author line, descending. No relevance
   re-ranking; the reader sorts visually.

4. **Theme order matches the project description.** If the project has
   `## Theme: A`, `## Theme: B`, `## Theme: C` in that order, use that order
   inside each section. Themes not declared in the project but introduced by
   a batch go at the end, alphabetically.

5. **Drop empty themes.** If a sub-theme has no entries in either Confirmed
   or Potential, omit its `<details>` block from that section entirely.

6. **Drop batch boilerplate.** If a batch emitted prose like "no scoops in
   this batch" (it shouldn't have, but in case), discard that text — only
   real entries are kept.

7. **Wrap every theme in `<details>` with a paper count.** The summary line
   format is exactly: `<summary><strong>Theme name</strong> (N papers)</summary>`
   where N is the number of entries in that theme within that section.
   Singular form `(1 paper)` if N == 1.

## What to count for the summary

- **N** (confirmed scoops requiring response) = total number of entries
  surviving deduplication under `## 🚨 Confirmed Scoop`.
- **M** (potential scoops worth monitoring) = total under `## ⚠️ Potential Scoop`.
- **K** (reviewed and judged non-overlapping) = `total_papers - N - M`.

## Output discipline

Emit only the markdown document. No preamble, no commentary, no "here is the
merged survey" lead-in. The first character of your response must be `#`.
