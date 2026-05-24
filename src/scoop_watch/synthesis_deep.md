# Deep survey synthesis

You are producing one slice of a multi-year survey for a research project. A
larger run is splitting the corpus into batches; you see ONE batch of papers
and produce the survey content for that batch. A later merge pass combines
your output with the other batches' output into a single document.

## Format contract — read this first

**Your output is parsed by a strict Python regex parser, not by a model.**
Any deviation from the format described below results in entries being
silently dropped, mis-grouped, or sorted incorrectly. The contract is small;
follow it exactly. The merge pass cannot recover from format drift.

The structural elements the parser keys on:

1. Section headings — exactly two, in this exact order, with these exact
   characters (the emoji is part of the heading):

   ```
   ## 🚨 Confirmed Scoop
   ## ⚠️ Potential Scoop
   ```

   No other `##` headings anywhere in your output. No "## Summary",
   no "## Notes", no preamble heading. The parser treats any other `##`
   as a phantom section and orphans its entries.

2. Theme headings — exactly `### ` (three hashes, one space) followed by
   the theme name copied **verbatim** from the project description's
   `## Theme: <name>` lines. Not `####`. Not `### Theme: <name>`. Just the
   bare theme name after `### `. A paraphrased or differently-cased theme
   name is treated as a brand-new theme and sorts alphabetically after the
   declared ones.

3. Entry start — every entry begins with `**<title>**` on its own line.
   Not `1. **<title>**`, not `- **<title>**`, not `# <title>`. The parser
   discards any chunk that does not start with `**`.

4. Author line — exactly this shape, on the second line of the entry:

   ```
   <Author1>, <Author2>, ... · [arxiv:<id>](<url>) · <YYYY-MM>
   ```

   The separators are middle-dot `·` (U+00B7) with one space on each side.
   Not `|`, not `•`, not en-dash. The date is `YYYY-MM` (e.g. `2025-09`)
   and it must be the last thing on the line — no trailing parenthesis,
   no trailing period, no "(preprint)", no "v2". The parser uses
   `· YYYY-MM` at end-of-line as the date anchor for sorting. Get this
   wrong and the entry sorts last.

5. Entry separator — exactly `---` on a line by itself, with blank lines
   above and below. Not `***`, not `___`, not `---next paper`, not inline.
   Every entry, including the last one under a theme, ends with this rule.

## Per-entry format (verbatim template)

```
**<Paper title in plain prose, no quotes>**
<Author1>, <Author2>, ... · [arxiv:<id>](<url>) · <YYYY-MM>

<3 to 5 sentences of analysis: what the paper does, why it overlaps with the
project, what is the same, what is different.>
**<One bolded sentence stating the precise distinction from the project —
the specific thing the author of the project should re-read in six months
to remember why this paper mattered.>**

---
```

Additional rules that do not break the parser but matter for the output:

- Authors: list at most the first three, then "et al." if more.
- The arxiv link is the only URL in the entry.
- No collapsibles (`<details>`), no bullet lists, no tables, no headings
  inside an entry.
- No commentary before, between, or after entries — every line must be
  inside one of the structural elements above.

## What to produce

A paper is a **Confirmed Scoop** if it pursues the same problem with a
substantially overlapping method (same architectural family, same target
quantity, same key technique). A paper is a **Potential Scoop** if it
pursues an adjacent method, the same problem in a different domain, or a
competing method on a clearly related target.

Drop everything else. No "Potentially Helpful", no "Broader Field". A paper
that does not clearly belong in one of the two scoop categories is omitted
silently — this is a survey of overlap, not a literature review.

## Grouping within sections

Group entries by sub-theme using the `## Theme:` headings declared in the
project description. Theme names must be copied character-for-character;
the merge pass orders themes according to their declaration order in
`project.md`, so a paraphrase will land your entries in the wrong place.

If the project description has no explicit themes, omit the `###` sub-theme
headings and list entries directly under each `##` section.

## Tone

Direct, technical, academic-paper voice. Match the precision the project
description uses. Do not write "this paper" or "the authors" — describe
what is done. Avoid filler ("It is worth noting", "Interestingly", "In
addition"). Never use the em dash.

## If a batch has no scoops

Emit the two section headings with nothing under them:

```
## 🚨 Confirmed Scoop

## ⚠️ Potential Scoop
```

Do not write "no papers found" or any other prose. The merge pass tolerates
empty sections; it does not tolerate filler text.
