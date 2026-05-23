# Briefing layout

This file controls how the briefing is structured. Edit it freely; the
synthesis agent follows it exactly. `scoop-watch author <project>` can also tune
it to match your project's sub-themes.

The briefing is built in three recency passes (last 24 hours, last 7 days,
the rest of the search window) and you receive one tier of papers per call;
you only need to define the **sections** and their **per-paper format** below.

## Sections

Assign each paper to exactly one section.

### 🚨 Confirmed Scoop
Direct, specific overlap — the paper works on essentially the same problem.
High confidence only. If uncertain, use Potential Scoop instead.

### ⚠️ Potential Scoop
Plausible but not certain overlap: adjacent methods, a partially overlapping
framework, or the same problem in a different domain. When in doubt between
Confirmed and Potential, always choose Potential.

### 🛠️ Potentially Helpful
Not competing, but could improve or extend the work: useful architectures,
training techniques, datasets, benchmarks, tooling.

### 📡 Broader Field
Notable for the field but only tangentially related. One line each.

## Grouping by sub-theme

Within Confirmed Scoop and Potential Scoop, group the papers under the
project's sub-themes — the `## Theme:` headings in project.md. Use the theme
name as a sub-heading. Omit a theme heading if it has no papers.

## Per-paper format

```
**[Title](url)** | Authors | arxiv:XXXX.XXXXX | submitted DATE
> One to three sentences stating the specific overlap or relevance precisely.
```

## Rules

- Always emit all four section headings (Confirmed Scoop, Potential Scoop,
  Potentially Helpful, Broader Field), in order, even when empty. For an
  empty section write "Nothing notable." under the heading; never drop the
  heading itself.
- Omit any sub-theme heading with no papers.
- Direct, scientific tone. No filler, no preamble.
- Use only papers from the provided JSON; never invent papers.
