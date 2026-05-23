# 🔬 Scoop-watch

> **Daily arXiv briefings that flag papers overlapping your active research
> project, so you find out who else is working on your problem before they
> scoop you.**

Most arXiv digests rank papers by generic popularity. Scoop-watch scores each
new paper against a description *you* write of your project and sorts the
results into four sections, ordered by urgency:

- 🚨 **Confirmed Scoop**: same problem, same approach
- ⚠️ **Potential Scoop**: adjacent method, or the same problem in a different
  domain
- 🛠️ **Potentially Helpful**: not competing, but could improve or extend your
  work (new architectures, training tricks, datasets, benchmarks)
- 📡 **Broader Field**: notable, only tangentially related

The two low-priority sections render **collapsed** with a paper count, so the
scoops are what you read first. The briefing is split into three top-level
recency tiers so the time-critical reading is obvious:

- ⚡ **Last 24 hours** — what showed up since your last run
- 📅 **Last 7 days** — the rest of this week
- 🗂️ **Last N days** — the rest of the configured search window

Built for researchers who want to stay current without skimming hundreds of
abstracts a week. Linux and macOS supported.

---

## How it works

Three stages, kept separate on purpose:

1. **Fetch** — queries the arXiv API for your keywords over the configured
   search window, filters to your categories.
2. **Synthesize** — hands the papers, your `project.md`, and your `layout.md`
   to an agent CLI (`claude` or `codex`), which writes the briefing. Three
   recency passes run in parallel (24 hours, 7 days, the rest of the window),
   so each tier is judged with only its own papers in context. No API key,
   just whatever the CLI is logged in with.
3. **Archive** — the briefing lands as a dated markdown file under
   `archive/`, and a copy of the raw fetched papers under `fetch-archive/`.

---

## Install

Linux (systemd user timers) or macOS (launchd LaunchAgents).

```bash
curl -fsSL https://raw.githubusercontent.com/MauriceDHanisch/scoop-watch/main/install.sh | bash
```

This installs `uv` if it is missing, places the program in
`~/.local/share/scoop-watch` (a uv-managed isolated environment), puts a
`scoop-watch` command on your PATH, and runs an interactive first-time setup.
Re-run the same command to update. Uninstall with `uninstall.sh`.

You need one agent CLI installed and logged in: `claude` (default) or `codex`.
Scoop-watch autodetects which is available; override it in setup.

## Layout

```
~/.local/share/scoop-watch/      the program — installer-managed, ignore it
~/.local/bin/scoop-watch         the command

~/Documents/scoop-watch/         your data, visible and yours to edit
├── .env                        agent, model, schedule, search window
└── projects/
    └── <project>/
        ├── project.md          the project description (scoring input)
        ├── layout.md           how the briefing is structured
        ├── config.yaml         arXiv categories + keyword queries
        ├── read.json           papers you have marked as read (filtered out)
        ├── archive/
        │   └── 2026-05-21.md   the daily briefings
        └── fetch-archive/
            └── 2026-05-21.json the raw fetched papers, kept for debugging
```

If `~/Documents` does not exist the data directory falls back to
`~/scoop-watch`. Override with the `WATCH_DATA_DIR` env var or during setup.

---

## Usage

```bash
scoop-watch setup                # configure agent, model, schedule, search window
scoop-watch author [project]     # launch an agent to write project.md + layout.md
scoop-watch new <project>        # scaffold a project from templates (no agent)
scoop-watch run [project]        # briefing for one project, or all projects
scoop-watch resynth [project]    # re-run only the agent on an existing fetch JSON
scoop-watch arm [project]        # schedule it on the global schedule
scoop-watch disarm [project]     # stop the schedule
scoop-watch read [project]       # tick papers you have read; hide from future runs
scoop-watch status [project]     # schedule + last briefing
scoop-watch update               # check GitHub and update in place
scoop-watch uninstall            # remove scoop-watch (keeps your data)
```

`author`, `arm`, `disarm` and `read` prompt for a project when none is given.
A project is "active" once it is armed, no separate enable/disable step.
`scoop-watch update --check` reports whether a newer version exists without
installing it; `uninstall --purge` also deletes your projects and briefings.

### Marking papers as read

`scoop-watch read` opens a checkbox picker over the most recent fetch and
appends what you tick to the project's `read.json`. Every later run filters
those papers out before synthesis, so a paper you have seen does not come back
week after week. Matching is **version-agnostic**: marking `2602.20232v1` as
read also hides `v2` when it appears.

### Authoring a project

`scoop-watch author <project>` scaffolds the project, then launches your agent
CLI primed to write it. Run it without a name to pick an existing project or
name a new one interactively. The agent first asks for any existing material — a
writeup, paper draft, or proposal — works from that, fills gaps by interview,
writes `project.md` and tunes `layout.md`, then checks the result with you. A
precise description is what makes the briefings precise.

### The two project files you own

- **`project.md`** — what your project is: the problem, the method, the novel
  contribution, and any sub-themes (`## Theme:` headings). The agent judges
  paper overlap from this.
- **`layout.md`** — how the briefing is structured: sections and how scoop
  candidates group under your sub-themes. Edit it freely.

---

## Scheduling — deliberate, not forgotten

`scoop-watch arm` installs a per-session timer: a systemd user timer on Linux
(**started but never enabled**) or a launchd LaunchAgent bootstrapped into the
GUI session on macOS. It runs on the schedule for as long as the user session
stays up, and a reboot stops it. Resuming is a deliberate `scoop-watch arm`.
The automation cannot quietly outlive your attention.

The schedule (weekdays and time) and the search window are **global** — one
setting for every project, chosen during `scoop-watch setup` and stored in the
data-root `.env`. `arm` just applies it to a project; it does not ask again.
Re-run `scoop-watch setup` any time to change them; every prompt shows the
current value as the default, so press Enter to keep or type to override.

No `sudo`. Check state with `scoop-watch status`, or `systemctl --user
list-timers` on Linux / `launchctl print gui/$UID/com.scoop-watch.<project>`
on macOS. A missed day is not lost: every run refetches the whole search
window (90 days by default), so skipped papers resurface under "Last 7 days".

---

## Example project

`examples/orbitall/` is a complete worked project showing a real `project.md`,
`config.yaml` and `layout.md`. It is based on the published paper **OrbitAll**
(Kang et al., [arXiv:2507.03853](https://arxiv.org/abs/2507.03853)) and is
included with attribution as a worked example. Copy it into your data directory
to try it:

```bash
cp -r examples/orbitall ~/Documents/scoop-watch/projects/
scoop-watch run orbitall
```

---

## Development

Dependencies and tooling are managed with `uv`.

```bash
git clone git@github.com:MauriceDHanisch/scoop-watch.git
cd scoop-watch
make setup     # uv sync + install pre-commit hooks
make check     # ruff lint + format check + ty types + pytest
```

`make check` is the gate run by CI. `make format` applies formatting; `make
lint` / `make typecheck` / `make test` run the steps individually.

The pre-commit framework (`.pre-commit-config.yaml`) wires the same checks into
`git commit`: it runs ruff lint, ruff format, ty type check and pytest, and
blocks the commit if anything fails. CI (`.github/workflows/ci.yml`) runs `make
check` on every push and pull request.
