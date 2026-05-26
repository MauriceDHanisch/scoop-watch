"""Tests for CLI command dispatch."""

import argparse

import pytest

from scoop_watch import cli


def test_author_with_explicit_project(monkeypatch):
    called: dict = {}
    monkeypatch.setattr(
        cli.author, "author", lambda project: called.update(project=project)
    )
    cli.cmd_author(argparse.Namespace(project="given"))
    assert called["project"] == "given"


def test_author_without_project_prompts(monkeypatch):
    """`author` with no name asks for one interactively, then uses it."""
    called: dict = {}
    monkeypatch.setattr(cli.wizard, "interactive", lambda: True)
    monkeypatch.setattr(cli.wizard, "pick_project_name", lambda: "picked")
    monkeypatch.setattr(
        cli.author, "author", lambda project: called.update(project=project)
    )
    cli.cmd_author(argparse.Namespace(project=None))
    assert called["project"] == "picked"


def test_author_without_project_non_interactive_errors(monkeypatch):
    monkeypatch.setattr(cli.wizard, "interactive", lambda: False)
    with pytest.raises(RuntimeError):
        cli.cmd_author(argparse.Namespace(project=None))


def test_author_without_project_cancelled(monkeypatch):
    """A cancelled name prompt exits cleanly without launching the agent."""
    monkeypatch.setattr(cli.wizard, "interactive", lambda: True)
    monkeypatch.setattr(cli.wizard, "pick_project_name", lambda: None)

    def fail(project: str) -> None:
        raise AssertionError("author must not run when the prompt is cancelled")

    monkeypatch.setattr(cli.author, "author", fail)
    assert cli.cmd_author(argparse.Namespace(project=None)) == 0


def test_setup_reruns_with_current_values_as_defaults(monkeypatch, tmp_path):
    """A second `scoop-watch setup` re-prompts every field; the existing .env
    values become the defaults so pressing Enter keeps them."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "AGENT=claude\nMODEL=opus\nRUN_TIME=07:15\nRUN_WEEKDAYS=Mon,Fri\nRECENT_DAYS=42\n"
    )

    captured: dict = {}

    def fake_resolve(use_defaults, current=None):
        captured["current"] = current
        return current  # echo back unchanged

    monkeypatch.setattr(cli.wizard, "resolve_setup", fake_resolve)
    monkeypatch.setattr(cli.paths, "set_data_root", lambda p: None)

    cli.cmd_setup(argparse.Namespace(defaults=False, reconfigure=False))

    current = captured["current"]
    assert current is not None, "re-run must pass current values into resolve_setup"
    assert current.agent == "claude"
    assert current.model == "opus"
    assert current.run_time == "07:15"
    assert current.weekdays == ["Mon", "Fri"]
    assert current.recent_days == 42


def test_setup_first_run_passes_no_current(monkeypatch, tmp_path):
    """Without a .env, `setup` does not synthesise a `current`; resolve_setup
    falls back to its own defaults."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    captured: dict = {}

    def fake_resolve(use_defaults, current=None):
        captured["current"] = current
        return cli.wizard.setup_defaults()

    monkeypatch.setattr(cli.wizard, "resolve_setup", fake_resolve)
    monkeypatch.setattr(cli.paths, "set_data_root", lambda p: None)

    cli.cmd_setup(argparse.Namespace(defaults=False, reconfigure=False))
    assert captured["current"] is None


def test_run_schedules_one_retry_after_a_fetch_abort(monkeypatch, tmp_path):
    """When `fetch` raises FetchAborted on an interactive `scoop-watch run`
    (retry_attempt=0), the CLI calls `scheduler.schedule_retry(project, 1, 60)`
    so a transient one-shot unit picks the run back up an hour later. The
    failure log is still written; the run does not raise to the user."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "project.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "layout.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "config.yaml").write_text(
        "categories: []\nqueries:\n  - name: q\n    operator: OR\n    terms: [x]\n",
        encoding="utf-8",
    )

    def fake_fetch(*args, **kwargs):
        raise cli.fetch.FetchAborted("Q", "rate limited", "http://x")

    monkeypatch.setattr(cli.fetch, "fetch", fake_fetch)
    monkeypatch.setattr(cli.fetch, "group_queries", lambda qs: [])

    scheduled: dict = {}

    def fake_schedule(project, attempt, delay, session_date):
        scheduled["project"] = project
        scheduled["attempt"] = attempt
        scheduled["delay"] = delay
        scheduled["session_date"] = session_date
        return f"scoop-watch-{project}-retry-{attempt}"

    monkeypatch.setattr(cli.scheduler, "schedule_retry", fake_schedule)

    cli.cmd_run(argparse.Namespace(project="demo", retry_attempt=0, session_date=None))

    assert scheduled["project"] == "demo"
    assert scheduled["attempt"] == 1
    assert scheduled["delay"] == 60
    # Session date defaults to today when not passed by the scheduler.
    import datetime as _dt

    assert scheduled["session_date"] == _dt.date.today().isoformat()


def test_run_stops_scheduling_retries_after_the_cap(monkeypatch, tmp_path):
    """The third retry (attempt=3) that itself aborts must not schedule a
    fourth; the run gives up with a 'rerun manually' hint instead."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "project.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "layout.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "config.yaml").write_text(
        "categories: []\nqueries:\n  - name: q\n    operator: OR\n    terms: [x]\n",
        encoding="utf-8",
    )

    def fake_fetch(*args, **kwargs):
        raise cli.fetch.FetchAborted("Q", "still rate limited", "http://x")

    monkeypatch.setattr(cli.fetch, "fetch", fake_fetch)
    monkeypatch.setattr(cli.fetch, "group_queries", lambda qs: [])

    def fail_schedule(*args, **kwargs):
        raise AssertionError("must not schedule another retry past the cap")

    monkeypatch.setattr(cli.scheduler, "schedule_retry", fail_schedule)

    cli.cmd_run(argparse.Namespace(project="demo", retry_attempt=3, session_date=None))


def test_run_appends_each_attempt_to_one_per_session_log(monkeypatch, tmp_path):
    """All attempts inside one scheduling cycle (initial + retries) share a
    single ``logs/<project>_<session_date>.log`` file so a morning
    post-mortem reads as one chronological story, not N separate files."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "project.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "layout.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "config.yaml").write_text(
        "categories: []\nqueries:\n  - name: q\n    operator: OR\n    terms: [x]\n",
        encoding="utf-8",
    )

    def fake_fetch(*args, **kwargs):
        raise cli.fetch.FetchAborted("Q", "rate limited", "http://x")

    monkeypatch.setattr(cli.fetch, "fetch", fake_fetch)
    monkeypatch.setattr(cli.fetch, "group_queries", lambda qs: [])
    monkeypatch.setattr(
        cli.scheduler,
        "schedule_retry",
        lambda *a, **kw: "fake-unit",
    )

    # Initial attempt + simulated retries all sharing the same session date.
    for attempt in (0, 1, 2, 3):
        cli.cmd_run(
            argparse.Namespace(
                project="demo",
                retry_attempt=attempt,
                session_date="2026-05-25",
            )
        )

    log = tmp_path / "logs" / "demo_2026-05-25.log"
    assert log.is_file()
    text = log.read_text(encoding="utf-8")
    # One entry per attempt, in order, with clear separators.
    assert text.count("=== initial attempt @") == 1
    assert text.count("=== retry attempt 1 @") == 1
    assert text.count("=== retry attempt 2 @") == 1
    assert text.count("=== retry attempt 3 @") == 1
    # No second per-attempt file polluting the logs directory.
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "demo_2026-05-25.log"
    ]


def test_arm_uses_the_global_schedule(monkeypatch, tmp_path):
    """arm schedules from the global .env, not a per-project state file."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("RUN_TIME=05:30\nRUN_WEEKDAYS=Mon,Wed\n")
    captured: dict = {}
    monkeypatch.setattr(
        cli.scheduler,
        "arm",
        lambda project, weekdays, time: captured.update(
            project=project, weekdays=weekdays, time=time
        ),
    )
    monkeypatch.setattr(cli.scheduler, "timer_line", lambda project: "armed")

    cli.cmd_arm(argparse.Namespace(project="demo"))

    assert captured == {"project": "demo", "weekdays": ["Mon", "Wed"], "time": "05:30"}
