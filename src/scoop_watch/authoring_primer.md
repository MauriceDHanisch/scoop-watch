You are helping set up a Scoop-watch project named "{{project}}".

Scoop-watch sends a daily arXiv briefing that flags papers overlapping an active
research project. The quality of that briefing depends on three files in this
directory, which you will write by working with me:

- {{project_md}} — the project description (what the work is)
- {{config_yaml}} — arXiv categories and keyword queries (what to search for)
- {{layout_md}} — how the briefing is structured

## Step 1 — ask me for existing material first

Before interviewing me from scratch, ask me to provide whatever I have already
written about the project: a writeup, abstract, paper draft, preprint, thesis
chapter, grant proposal, or detailed notes. Tell me I can paste it directly or
point you to a file, and that real material produces a far better result than a
cold interview. Wait for my response.

## Step 2 — fill the gaps by interview

Read whatever I provide. If I have no written material, or it leaves gaps, ask
focused questions, a few at a time, until you can describe precisely: the
problem and why standard approaches fall short; the method (models, data,
training setup, key equations); what is genuinely novel; and whether the
project has distinct sub-themes. Probe for the specifics a reader needs to
recognise a competing paper from its abstract alone.

## Step 3 — write the project description

Write **{{project_md}}** in clear prose. Give each distinct sub-theme a
`## Theme: <name>` heading, and state the novel contribution explicitly — that
is what a "scoop" overlaps with.

For shape, here is a worked example from a different research area. Match its
**structure** — intro paragraph, one `## Theme:` heading per sub-theme, and a
closing "A paper overlaps with this theme if..." sentence under each theme.
Do **not** copy its topic or vocabulary.

````markdown
{{example_project}}
````

## Step 4 — propose the search queries, then refine them with me

Derive arXiv categories and keyword queries from the project and **propose them
to me explicitly**. For each query, explain in one line why it is there and
what it is meant to catch. Then interview me: do these terms make sense? Is
anything missing? Walk through them with me and adjust.

Calibrate breadth deliberately, and tell me your reasoning:

- **Too broad** (e.g. a bare term like `neural network`) floods the briefing
  with irrelevant papers and buries real scoops.
- **Too narrow** (e.g. only the project's exact internal name) misses
  competing work that uses different vocabulary.
- Aim for terms a competing author would plausibly put in a title or abstract.
  Prefer several focused queries over one sprawling one.

Once I agree, write **{{config_yaml}}**. The schema:

```
categories: [list of arXiv categories, e.g. cs.LG, physics.chem-ph]
queries:
  - name: short label
    operator: AND          # or OR — combines the terms below
    terms: [term one, term two]
```

Multi-word terms are phrase-matched automatically. One query uses a single
operator; for a mixed query like `(A OR B) AND C`, split it into two queries
(`A AND C`, `B AND C`) — results are deduplicated across queries.

## Step 5 — tune the layout

Review **{{layout_md}}**. It has a sensible default; adjust only the sub-theme
grouping to match the `## Theme:` headings you wrote in the description. Do not
change the time buckets or section names unless I ask.

## Step 6 — check the result with me

Re-read {{project_md}} and sanity-check it: *could a reader, given only this
description and a paper's abstract, judge whether the paper overlaps?* If not,
it is too vague — revise. Show me the final description and the query list,
confirm both are accurate, and revise until I agree.

Finally, tell me explicitly to double-check {{config_yaml}} and {{project_md}}
myself before the first run — the briefing is only as good as those files.

## Rules

- Ask for existing material before interviewing. Interview before writing.
- Write the actual files to the paths above; do not just print their contents.
- Keep the description focused: a few well-written paragraphs per theme.

Begin now by asking me for any existing writeup or draft of the project.
