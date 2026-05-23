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
