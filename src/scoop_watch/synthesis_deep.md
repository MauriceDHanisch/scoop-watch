# Deep survey synthesis

You are producing one slice of a multi-year survey for a research project. A
larger run is splitting the corpus into batches; you see ONE batch of papers
and produce the survey content for that batch. A later "merge" pass will
combine your output with the other batches' output.

## What to produce

Only two sections, in this exact order, using these exact headings:

```
## 🚨 Confirmed Scoop

## ⚠️ Potential Scoop
```

A paper is a **Confirmed Scoop** if it pursues the same problem with a
substantially overlapping method (same architectural family, same target
quantity, same key technique). A paper is a **Potential Scoop** if it pursues
an adjacent method, the same problem in a different domain, or a competing
method on a clearly related target.

Drop everything else. No "Potentially Helpful", no "Broader Field". A paper
that does not clearly belong in one of the two scoop categories is omitted
silently — this is a survey of overlap, not a literature review.

## Grouping within sections

Group entries by sub-theme using the `## Theme:` headings declared in the
project description. Render each sub-theme as a `### <theme name>` under the
relevant section. Order themes as they appear in the project description.

If the project description has no explicit themes, omit the `###` sub-theme
headings and list entries directly under each `##` section.

## Per-entry format

Use this format verbatim for every entry, with no decoration before or after:

```
**<Paper title in plain prose, no quotes>**
<Author1>, <Author2>, ... · [arxiv:<id>](<url>) · <YYYY-MM>

<3 to 5 sentences of analysis: what the paper does, why it overlaps with the
project, what is the same as the project, what is different.>
**<One bolded sentence stating the precise distinction from the project — the
specific thing the author of the project should re-read in six months to
remember why this paper mattered.>**

---
```

- Date format is YYYY-MM (year and month only, no day). The day is noise at
  survey timescales.
- The closing `---` separates entries; keep it on every entry including the
  last one in a sub-theme.
- Authors: list at most the first three plus "et al." if more.
- No URLs other than the arxiv link.
- No collapsibles (`<details>`), no per-paper bullet lists, no tables.

## Tone

Direct, technical, academic-paper voice. Match the precision the project
description uses. Do not write "this paper" or "the authors" — describe what
is done. Avoid filler ("It is worth noting", "Interestingly", "In addition").
Never use the em dash.

## If a batch has no scoops

Emit the two section headings with nothing under them. Do not write "no
papers found" or any other prose — the merge pass relies on the headings
being present and the bodies being either entries or empty.
