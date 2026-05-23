"""Regression tests for `scoop-watch author`.

`author` once launched the agent CLI without `--model`, so it ignored the
configured model. These tests pin the agent command it builds.
"""

from scoop_watch import author


def _capture_agent_command(monkeypatch):
    """Stub out shutil.which and subprocess.run; return a dict that the run
    stub fills with the command it was given."""
    captured: dict = {}
    monkeypatch.setattr(author.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        author.subprocess,
        "run",
        lambda command, **kwargs: captured.update(command=command),
    )
    return captured


def test_author_passes_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WATCH_MODEL", raising=False)
    (tmp_path / ".env").write_text("AGENT=claude\nMODEL=opus\n")
    captured = _capture_agent_command(monkeypatch)

    author.author("demo")

    command = captured["command"]
    assert command[0] == "claude"
    assert "--model" in command
    assert command[command.index("--model") + 1] == "opus"


def test_author_omits_model_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WATCH_MODEL", raising=False)
    (tmp_path / ".env").write_text("AGENT=claude\n")
    captured = _capture_agent_command(monkeypatch)

    author.author("demo")

    assert "--model" not in captured["command"]


def test_primer_embeds_a_worked_project_md_example():
    """The primer ships a project.md example so the agent has a target shape."""
    text = author._primer("demo")
    assert "{{example_project}}" not in text  # placeholder was substituted
    assert "## Theme:" in text  # the example carries theme headings
    assert "A paper overlaps with this theme if" in text  # and an overlap clause
