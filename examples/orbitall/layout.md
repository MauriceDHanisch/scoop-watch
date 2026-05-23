# Briefing layout: OrbitAll example project

The briefing is built in three recency passes (last 24 hours, last 7 days,
the rest of the search window) and you receive one tier of papers per call.

## Sections

Assign each paper to exactly one section.

### 🚨 Confirmed Scoop
Direct, specific overlap, high confidence only. The paper must work on
essentially the same problem: either (a) predicting quantum-mechanical
properties for charged, open-shell, or solvated molecules with one model, or
(b) building spin-polarized orbital-feature representations for an equivariant
graph network. If uncertain, use Potential Scoop.

### ⚠️ Potential Scoop
Plausible but not certain overlap: adjacent methods, partially overlapping
frameworks, or a related problem in a different domain. When in doubt between
Confirmed and Potential, always choose Potential.

### 🛠️ Potentially Helpful
Not competing, but could improve or extend the work: new equivariant
architectures, training tricks, molecular datasets, benchmarks, baselines.

### 📡 Broader Field
Notable but only tangentially related. One line each.

## Grouping by sub-theme

Within Confirmed Scoop and Potential Scoop, group papers under the two project
sub-themes, using these sub-headings:

- **🧪 Property prediction across electronic states**: overlaps with the application
- **🔷 Spin-polarized orbital-feature representation**: overlaps with the architecture

Omit a sub-theme heading if it has no papers.

## Per-paper format

```
**[Title](url)** | Authors | arxiv:XXXX.XXXXX | submitted DATE
> One to three sentences stating the specific overlap precisely.
```

## Rules

- Always emit all four section headings (Confirmed Scoop, Potential Scoop,
  Potentially Helpful, Broader Field), in order, even when empty. For an
  empty section write "Nothing notable." under the heading; never drop the
  heading itself.
- Omit empty sub-themes.
- Direct, scientific tone. No filler, no preamble.
- Use only papers from the provided JSON; never invent papers.
