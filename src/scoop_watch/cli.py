"""Command-line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import (
    __version__,
    author,
    config,
    fetch,
    paths,
    reading,
    scaffold,
    scheduler,
    synthesize,
    synthesize_deep,
    tips,
    ui,
    uninstaller,
    updater,
    wizard,
)


def _show_tip(command: str) -> None:
    """Print one rotating tip for the given command, if any."""
    tip = tips.next(command)
    if tip:
        ui.hint(f"Tip: {tip}")


def cmd_setup(args: argparse.Namespace) -> int:
    """Configure global settings (agent, model, schedule, data directory).

    Re-running setup re-prompts every field with the current persisted value
    as its default, so Enter keeps and typing overrides. The ``--reconfigure``
    flag is accepted for backward compatibility but no longer needed.
    """
    has_env = paths.env_file().is_file()
    if has_env:
        ui.step("re-running setup; press Enter to keep each current value")
        current = wizard.current_choices()
    else:
        current = None

    choices = wizard.resolve_setup(args.defaults, current=current)
    if choices.data_dir != paths.default_data_root():
        paths.set_data_root(choices.data_dir)

    root = paths.data_root()
    (root / "projects").mkdir(parents=True, exist_ok=True)

    lines = ["# scoop-watch settings", f"AGENT={choices.agent}"]
    if choices.model:
        lines.append(f"MODEL={choices.model}")
    lines.append(f"RUN_TIME={choices.run_time}")
    lines.append(f"RUN_WEEKDAYS={','.join(choices.weekdays)}")
    lines.append(f"RECENT_DAYS={choices.recent_days}")
    paths.env_file().write_text("\n".join(lines) + "\n", encoding="utf-8")

    ui.blank()
    ui.ok("scoop-watch configured")
    ui.detail("data root", str(root))
    ui.detail("agent", config.agent())
    ui.detail("model", config.model() or "agent default")
    ui.detail(
        "schedule",
        f"{','.join(config.default_weekdays())} at {config.default_run_time()}",
    )
    ui.detail("search window", f"{config.recent_days()} days")
    ui.blank()
    _show_tip("setup")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Scaffold a project from templates without launching an agent."""
    created = scaffold.scaffold(args.project)
    if created:
        ui.ok(f"created project '{args.project}'")
    else:
        ui.warn(f"project '{args.project}' already exists")
    ui.detail("location", str(paths.project_dir(args.project)))
    ui.blank()
    _show_tip("new")
    return 0


def cmd_author(args: argparse.Namespace) -> int:
    """Scaffold the project and launch the agent to write its files."""
    project = args.project
    if not project:
        if not wizard.interactive():
            raise RuntimeError(
                "specify a project name, or run in an interactive terminal"
            )
        project = wizard.pick_project_name()
        if not project:
            ui.hint("cancelled.")
            return 0
    author.author(project)
    return 0


def _append_fetch_failure_log(
    project: str,
    aborted: fetch.FetchAborted,
    session_date: str,
    attempt: int,
    next_retry_unit: str = "",
) -> Path:
    """Append a failed-fetch record to ``logs/<project>_<session_date>.log``.

    All attempts (original + retries) within one scheduling cycle share a
    single log file, so a morning post-mortem of "what happened overnight"
    reads as one chronological story instead of N separate files. Each
    attempt prepends a clear separator with its attempt number and the
    scheduled next-retry unit (if any) so the chain is visible at a glance.
    """
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{project}_{session_date}.log"
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    label = "initial attempt" if attempt == 0 else f"retry attempt {attempt}"
    chunk = (
        f"\n=== {label} @ {now_iso} ===\n"
        f"project:    {project}\n"
        f"query:      {aborted.query}\n"
        f"error:      {aborted.error}\n"
        f"url:        {aborted.url}\n"
    )
    if next_retry_unit:
        chunk += f"next retry: {next_retry_unit}\n"
    else:
        chunk += "next retry: none (cap reached or unsupported platform)\n"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(chunk)
    return log_path


# A single 04:00 timer firing into a transient arXiv 429 used to lose the
# whole night. The scheduled run now schedules up to N follow-up retries
# 60 minutes apart, so a flaky 30-min window does not eat the briefing.
# A normal interactive `scoop-watch run` (retry_attempt=0) only schedules
# one retry; a retry that itself aborts schedules the next one until the
# cap is reached.
_RETRY_DELAY_MINUTES = 60
_MAX_RETRY_ATTEMPTS = 3


def _run_one(
    project: str, retry_attempt: int = 0, session_date: str | None = None
) -> None:
    session_date = session_date or dt.date.today().isoformat()
    project_config = config.load_config(project)
    days = config.recent_days()
    queries = project_config["queries"]
    merged_count = len(fetch.group_queries(queries))
    if retry_attempt:
        ui.step(
            f"{project}  retry attempt {retry_attempt}/{_MAX_RETRY_ATTEMPTS} "
            f"after a previous fetch abort"
        )
    if merged_count < len(queries):
        ui.step(
            f"{project}  fetching arXiv ({days}-day window, "
            f"{len(queries)} queries -> {merged_count} merged requests)"
        )
    else:
        ui.step(f"{project}  fetching arXiv ({days}-day window, {len(queries)} queries)")

    def _warn_query(name: str, error: str) -> None:
        ui.substep(f"query '{name}' failed: {error.splitlines()[0]}")

    try:
        papers = fetch.fetch(
            queries,
            project_config.get("categories", []),
            days,
            on_query_error=_warn_query,
            progress=ui.substep,
        )
    except fetch.FetchAborted as aborted:
        ui.warn(f"{project}  fetch aborted; no briefing written")
        next_attempt = retry_attempt + 1
        unit = ""
        if next_attempt <= _MAX_RETRY_ATTEMPTS:
            try:
                unit = scheduler.schedule_retry(
                    project,
                    next_attempt,
                    _RETRY_DELAY_MINUTES,
                    session_date=session_date,
                )
            except RuntimeError:
                unit = ""  # platform unsupported; degrade gracefully
            if unit:
                ui.detail(
                    "retry",
                    f"scheduled attempt {next_attempt}/{_MAX_RETRY_ATTEMPTS} "
                    f"in {_RETRY_DELAY_MINUTES} min (unit {unit})",
                )
            else:
                ui.hint(
                    "could not schedule auto-retry; rerun `scoop-watch run` manually."
                )
        else:
            ui.hint(
                f"retry cap reached ({_MAX_RETRY_ATTEMPTS} attempts); "
                "rerun `scoop-watch run` manually."
            )
        # Append to the shared per-session log AFTER deciding whether to
        # schedule a retry, so the entry records the next unit name.
        log_path = _append_fetch_failure_log(
            project, aborted, session_date, retry_attempt, next_retry_unit=unit
        )
        ui.detail("log", str(log_path))
        return

    # Same-day reruns are kept as -v2 / -v3 / ... rather than overwriting.
    stem = paths.next_version_stem(project)
    if stem != dt.date.today().isoformat():
        ui.detail("rerun", f"earlier run today exists; writing as {stem}")

    # Archive the RAW fetch (before the read filter) so the JSON is a faithful
    # 'what arXiv returned today' log; the read appendix in the briefing makes
    # the deferred set visible.
    fetch_path = paths.fetch_archive_dir(project) / f"{stem}.json"
    fetch.archive_papers(fetch_path, fetch.by_recency(papers))
    ui.detail("fetch saved", str(fetch_path))

    already_read = reading.read_ids(project)
    unread: list = []
    deferred: list = []
    for paper in papers:
        if reading.strip_version(paper.arxiv_id) in already_read:
            deferred.append(paper)
        else:
            unread.append(paper)
    if deferred:
        ui.detail(
            "read filter",
            f"{len(deferred)} paper(s) deferred to the 'Already read' appendix",
        )

    ui.step(
        f"{project}  synthesizing briefing ({len(unread)} papers, passes run in parallel)"
    )
    briefing_path = paths.archive_dir(project) / f"{stem}.md"
    synthesize.synthesize(
        project,
        unread,
        progress=ui.substep,
        out_path=briefing_path,
        read_papers=deferred,
    )
    ui.ok(f"{project}  briefing written")
    ui.detail("file", str(briefing_path))
    _show_tip("run")


def cmd_run(args: argparse.Namespace) -> int:
    """Run one project, or every project when none is given.

    ``--retry-attempt N`` (hidden) is passed by the scheduler-spawned retry
    invocations so each run knows whether it still has retries left if its
    fetch aborts. 0 = the user's original run (or the scheduled timer fire),
    1..3 = follow-up retries.
    """
    retry_attempt = getattr(args, "retry_attempt", 0)
    session_date = getattr(args, "session_date", None)
    projects = [args.project] if args.project else config.list_projects()
    if not projects:
        ui.warn("no projects yet")
        ui.hint("Create one with `scoop-watch author`.")
        return 0
    for project in projects:
        _run_one(project, retry_attempt=retry_attempt, session_date=session_date)
    return 0


def _choose_project(message: str) -> str | None:
    """Resolve a project: the given list of projects, picked interactively."""
    projects = config.list_projects()
    if not projects:
        ui.warn("no projects yet")
        ui.hint("Create one with `scoop-watch author`.")
        return None
    if not wizard.interactive():
        raise RuntimeError("specify a project, or run in an interactive terminal")
    return wizard.pick_project(projects, message)


def cmd_arm(args: argparse.Namespace) -> int:
    """Schedule a project's daily run on the global schedule (reboot-ephemeral)."""
    project = args.project or _choose_project("Arm which project?")
    if project is None:
        return 0

    weekdays = config.default_weekdays()
    run_time = config.default_run_time()

    scheduler.arm(project, weekdays, run_time)
    ui.ok(f"armed '{project}'")
    ui.detail("runs", f"{','.join(weekdays)} at {run_time}")
    ui.detail("status", scheduler.timer_line(project))
    ui.blank()
    ui.hint("Schedule comes from `scoop-watch setup`; stops at next reboot.")
    _show_tip("arm")
    return 0


def cmd_disarm(args: argparse.Namespace) -> int:
    project = args.project or _choose_project("Disarm which project?")
    if project is None:
        return 0
    scheduler.disarm(project)
    ui.ok(f"disarmed '{project}'")
    _show_tip("disarm")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    """Tick papers from the most recent fetch as read; future runs filter them."""
    project = args.project or _choose_project("Mark papers as read for which project?")
    if project is None:
        return 0

    archive_dir = paths.fetch_archive_dir(project)
    # Pick the most recently written fetch archive (mtime), not the lexically
    # last one: `-v2` sorts before `.json` lexically but is the newer run.
    archives = (
        sorted(archive_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if archive_dir.is_dir()
        else []
    )
    if not archives:
        ui.warn(f"no fetched papers for '{project}' yet")
        ui.hint("Run `scoop-watch run` first.")
        return 0

    papers = json.loads(archives[-1].read_text(encoding="utf-8"))
    if not papers:
        ui.warn("the most recent fetch is empty; nothing to mark")
        return 0
    if not wizard.interactive():
        raise RuntimeError("`read` needs a terminal to show the paper picker")

    selected = wizard.pick_papers(papers, already_marked=reading.read_ids(project))
    if selected is None:
        ui.hint("cancelled.")
        return 0

    added, removed = reading.reconcile_read(project, papers, selected)
    ui.ok(f"updated read.json for '{project}'")
    if added:
        ui.detail("added", f"{added} paper(s)")
    if removed:
        ui.detail("removed", f"{removed} paper(s)")
    if not added and not removed:
        ui.detail("changes", "none")
    ui.detail("stored in", str(paths.read_json_path(project)))
    _show_tip("read")
    return 0


def cmd_resynth(args: argparse.Namespace) -> int:
    """Re-synthesize from an existing fetch-archive JSON; no arXiv calls."""
    project = args.project or _choose_project("Resynth which project?")
    if project is None:
        return 0

    archive_dir = paths.fetch_archive_dir(project)
    if args.date:
        # Match the bare date first, then any -vN file for that date by mtime.
        candidates = sorted(
            archive_dir.glob(f"{args.date}*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            raise RuntimeError(f"no fetch-archive for '{project}' on {args.date}")
        source = candidates[-1]
    else:
        all_jsons = (
            sorted(archive_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
            if archive_dir.is_dir()
            else []
        )
        if not all_jsons:
            raise RuntimeError(
                f"no fetch archives for '{project}'; run 'scoop-watch run' first"
            )
        source = all_jsons[-1]

    ui.step(f"{project}  resynth from {source.name}")
    data = json.loads(source.read_text(encoding="utf-8"))
    papers = [
        fetch.Paper(**{k: v for k, v in entry.items() if k != "recency"})
        for entry in data
    ]
    ui.detail("source", str(source))
    ui.detail("papers", str(len(papers)))

    already_read = reading.read_ids(project)
    unread: list = []
    deferred: list = []
    for paper in papers:
        if reading.strip_version(paper.arxiv_id) in already_read:
            deferred.append(paper)
        else:
            unread.append(paper)
    if deferred:
        ui.detail(
            "read filter",
            f"{len(deferred)} paper(s) deferred to the 'Already read' appendix",
        )

    stem = paths.next_version_stem(project)
    briefing_path = paths.archive_dir(project) / f"{stem}.md"
    ui.step(
        f"{project}  synthesizing briefing ({len(unread)} papers, passes run in parallel)"
    )
    synthesize.synthesize(
        project,
        unread,
        progress=ui.substep,
        out_path=briefing_path,
        read_papers=deferred,
    )
    ui.ok(f"{project}  briefing written")
    ui.detail("file", str(briefing_path))
    _show_tip("resynth")
    return 0


def cmd_deep(args: argparse.Namespace) -> int:
    """Multi-year deep survey of one project, with per-batch checkpoints.

    The deep pipeline is three stages, each separately resumable:
      1. Fetch — per-merged-group JSONL files under ``deep-fetch/<date>/``.
      2. Synthesize — per-batch markdown under ``deep-batches/<date>/``.
      3. Merge — final survey under ``deep-archive/<date>_<years>y.md``.

    Re-running with the same ``--date`` skips any stage whose output exists.
    """
    project = args.project or _choose_project("Deep survey which project?")
    if project is None:
        return 0

    years = args.years
    days = years * 365
    date = args.date or dt.date.today().isoformat()

    archive_path = paths.deep_archive_dir(project) / f"{date}_{years}y.md"
    if archive_path.is_file() and not args.force:
        ui.warn(f"deep survey already exists: {archive_path}")
        ui.hint("Pass --force to rebuild, or --date YYYY-MM-DD to write a new one.")
        return 0
    if args.force and archive_path.is_file():
        archive_path.unlink()
    if args.force:
        # Re-doing every batch from scratch on --force; the fetch is preserved
        # because re-fetching is the slow + expensive part and rarely what
        # --force means in practice.
        batches_dir = paths.deep_batches_dir(project, date)
        for stale in batches_dir.glob("batch_*.md"):
            stale.unlink()

    project_config = config.load_config(project)
    queries = project_config["queries"]
    merged_count = len(fetch.group_queries(queries))
    ui.step(
        f"{project}  deep fetch ({years}y / {days}d window, "
        f"{len(queries)} queries -> {merged_count} merged requests)"
    )

    def _warn_query(name: str, error: str) -> None:
        ui.substep(f"query '{name}' failed: {error.splitlines()[0]}")

    try:
        papers = fetch.fetch_deep(
            queries,
            days=days,
            out_dir=paths.deep_fetch_dir(project, date),
            max_results=args.max_results,
            on_query_error=_warn_query,
            progress=ui.substep,
        )
    except fetch.FetchAborted as aborted:
        # Deep mode doesn't auto-retry; the user reruns `scoop-watch deep`
        # themselves and the fetch resumes from per-group JSONL on disk.
        log_path = _append_fetch_failure_log(
            project, aborted, dt.date.today().isoformat(), attempt=0
        )
        ui.warn(f"{project}  deep fetch aborted; resume with `scoop-watch deep`")
        ui.detail("log", str(log_path))
        ui.detail(
            "note",
            "successfully-fetched groups are preserved; rerun picks up where it stopped",
        )
        return 1

    ui.ok(f"{project}  fetched {len(papers)} unique papers")
    if args.fetch_only:
        ui.detail("location", str(paths.deep_fetch_dir(project, date)))
        ui.hint("Run `scoop-watch deep` again without --fetch-only to synthesize.")
        return 0

    if not papers:
        ui.warn(f"{project}  no papers to synthesize")
        return 0

    ui.step(
        f"{project}  deep synthesis "
        f"({len(papers)} papers in batches of {args.batch_size}, "
        f"max_workers={args.parallel})"
    )
    out_path = synthesize_deep.synthesize_deep(
        project,
        papers,
        years=years,
        date=date,
        batch_size=args.batch_size,
        max_workers=args.parallel,
        progress=ui.substep,
    )
    ui.ok(f"{project}  deep survey written")
    ui.detail("file", str(out_path))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    projects = [args.project] if args.project else config.list_projects()
    if not projects:
        ui.warn("no projects (run `scoop-watch author`)")
        return 0
    for index, project in enumerate(projects):
        if index:
            ui.blank()
        archive = paths.archive_dir(project)
        # Sort by mtime so -v2 / -v3 reruns sort after the bare date and the
        # most recent run is genuinely last (lexical sort puts `-vN` before `.`).
        briefings = (
            sorted(archive.glob("*.md"), key=lambda p: p.stat().st_mtime)
            if archive.is_dir()
            else []
        )
        last = briefings[-1].stem if briefings else "none"
        ui.header(project)
        ui.detail("schedule", scheduler.timer_line(project))
        last_run = scheduler.last_run_line(project)
        if last_run:
            ui.detail("last run", last_run)
        ui.detail("last briefing", f"{last} ({len(briefings)} total)")
    ui.blank()
    _show_tip("status")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Check GitHub for a newer version and, unless --check, install it."""
    installed = updater.installed_sha()
    latest = updater.latest_sha()

    if installed and installed == latest:
        ui.ok(f"scoop-watch is up to date ({latest[:7]})")
        return 0

    if installed:
        ui.step(f"update available: {installed[:7]} → {latest[:7]}")
    else:
        ui.step(f"latest version is {latest[:7]} (installed commit unknown)")

    if args.check:
        ui.hint("Install it with:  scoop-watch update")
        return 0

    ui.blank()
    updater.run_installer()
    ui.ok("scoop-watch updated")
    _show_tip("update")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove scoop-watch, its timers, and optionally its data."""
    if args.yes:
        remove_data = args.purge
    elif wizard.interactive():
        if not wizard.confirm("Uninstall scoop-watch?", default=False):
            ui.hint("cancelled.")
            return 0
        remove_data = wizard.confirm(
            "Also delete your projects and briefings?", default=False
        )
    else:
        raise RuntimeError("uninstall needs a terminal to confirm, or pass --yes")

    result = uninstaller.uninstall(remove_data)
    for unit in result.timers:
        if unit.was_active:
            ui.ok(f"stopped {unit.name} (timer was running) and removed it")
        else:
            ui.ok(f"removed {unit.name}")
    if not result.timers:
        ui.detail("timers", "none were scheduled")
    ui.ok("program and command removed")
    if result.data_removed:
        ui.ok(f"deleted {result.data_root}")
    else:
        ui.hint(f"kept your data: {result.data_root}")
    ui.blank()
    ui.ok("scoop-watch uninstalled")
    return 0


def _version_string() -> str:
    """Version plus the installed commit, when the installer recorded one."""
    sha = updater.installed_sha()
    return f"{__version__} ({sha[:7]})" if sha else __version__


def _build_parser() -> argparse.ArgumentParser:
    kwargs: dict = {
        "prog": "scoop-watch",
        "description": "Daily arXiv briefings that flag papers overlapping your project.",
    }
    if sys.version_info >= (3, 14):
        # Python 3.14+ colorizes help output; keep it plain and calm.
        kwargs["color"] = False
    parser = argparse.ArgumentParser(**kwargs)
    parser.add_argument("--version", action="version", version=_version_string())
    sub = parser.add_subparsers(dest="command", required=True)

    setup_parser = sub.add_parser("setup", help="configure agent, model, schedule")
    setup_parser.add_argument(
        "--defaults", action="store_true", help="skip prompts, accept defaults"
    )
    setup_parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="deprecated; `setup` now always re-prompts with current values",
    )

    new_parser = sub.add_parser("new", help="scaffold a project from templates")
    new_parser.add_argument("project")

    author_parser = sub.add_parser(
        "author", help="scaffold a project and launch an agent to write it"
    )
    author_parser.add_argument("project", nargs="?")

    run_parser = sub.add_parser("run", help="generate briefings (one project, or all)")
    run_parser.add_argument("project", nargs="?")
    # Hidden: set by the scheduler-spawned retry invocations so each run knows
    # which retry attempt it is and whether more retries remain after another
    # abort. End-users don't need this; it's set programmatically.
    run_parser.add_argument(
        "--retry-attempt", type=int, default=0, help=argparse.SUPPRESS
    )
    # Hidden: the session start date (the date of the first attempt in the
    # current scheduling cycle). All attempts in one cycle share this so
    # their logs append to a single per-session file.
    run_parser.add_argument("--session-date", default=None, help=argparse.SUPPRESS)

    resynth_parser = sub.add_parser(
        "resynth",
        help="re-synthesize from an existing fetch-archive JSON (no arXiv calls)",
    )
    resynth_parser.add_argument("project", nargs="?")
    resynth_parser.add_argument(
        "--date", help="YYYY-MM-DD; defaults to the latest fetch archive"
    )

    deep_parser = sub.add_parser(
        "deep",
        help="multi-year survey of one project, batched + checkpointed",
    )
    deep_parser.add_argument("project", nargs="?")
    deep_parser.add_argument(
        "--years", type=int, default=5, help="years back to fetch (default: 5)"
    )
    deep_parser.add_argument(
        "--date",
        help="YYYY-MM-DD label for this run (default: today). Resumes when the "
        "label already has partial outputs on disk.",
    )
    deep_parser.add_argument(
        "--max-results",
        type=int,
        default=2000,
        help="max results per merged request (default: 2000, arXiv's per-request cap)",
    )
    deep_parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="papers per synthesis batch (default: 100; ~50k tokens/call)",
    )
    deep_parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="concurrent agent calls during batch fan-out (default: 5)",
    )
    deep_parser.add_argument(
        "--fetch-only", action="store_true", help="fetch and checkpoint, then stop"
    )
    deep_parser.add_argument(
        "--force",
        action="store_true",
        help="wipe existing batch+archive outputs for this date (preserves fetch)",
    )

    arm_parser = sub.add_parser("arm", help="schedule a project's daily run")
    arm_parser.add_argument("project", nargs="?")

    disarm_parser = sub.add_parser("disarm", help="stop and remove a project's timer")
    disarm_parser.add_argument("project", nargs="?")

    read_parser = sub.add_parser(
        "read", help="tick papers from the last fetch as read (hide from future runs)"
    )
    read_parser.add_argument("project", nargs="?")

    status_parser = sub.add_parser("status", help="show schedule and last briefing")
    status_parser.add_argument("project", nargs="?")

    update_parser = sub.add_parser(
        "update", help="update scoop-watch to the latest version"
    )
    update_parser.add_argument(
        "--check", action="store_true", help="only check, do not install"
    )

    uninstall_parser = sub.add_parser(
        "uninstall", help="remove scoop-watch from this machine"
    )
    uninstall_parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    uninstall_parser.add_argument(
        "--purge", action="store_true", help="also delete projects and briefings"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    handlers = {
        "setup": cmd_setup,
        "new": cmd_new,
        "author": cmd_author,
        "run": cmd_run,
        "resynth": cmd_resynth,
        "deep": cmd_deep,
        "arm": cmd_arm,
        "disarm": cmd_disarm,
        "read": cmd_read,
        "status": cmd_status,
        "update": cmd_update,
        "uninstall": cmd_uninstall,
    }
    args = _build_parser().parse_args(argv)
    try:
        result = handlers[args.command](args)
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        ui.error(str(error))
        return 1

    if args.command != "update":
        notice = updater.update_notice()
        if notice:
            ui.blank()
            ui.warn(notice)
    return result


if __name__ == "__main__":
    sys.exit(main())
