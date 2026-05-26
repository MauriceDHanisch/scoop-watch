"""Tests for environment and per-project configuration."""

import pytest

from scoop_watch import config


def test_load_env_parses_and_skips_noise(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        '# comment\nAGENT="codex"\nRUN_TIME=06:30\n\nNO_EQUALS_SIGN\n'
    )
    assert config.load_env() == {"AGENT": "codex", "RUN_TIME": "06:30"}


def test_agent_uses_explicit_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("AGENT=codex\n")
    assert config.agent() == "codex"


def test_agent_autodetects_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WATCH_AGENT", raising=False)
    monkeypatch.setattr(
        config.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None
    )
    assert config.agent() == "codex"


def test_detect_agent_prefers_claude(monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert config.detect_agent() == "claude"


def test_detect_agent_defaults_when_none_installed(monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    assert config.detect_agent() == "claude"


def test_model_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("MODEL=opus\n")
    assert config.model() == "opus"


def test_model_none_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WATCH_MODEL", raising=False)
    assert config.model() is None


def test_load_config_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        config.load_config("does-not-exist")


def test_load_config_applies_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "config.yaml").write_text("queries:\n  - name: q\n    terms: [foo]\n")
    data = config.load_config("demo")
    assert data["categories"] == []


def test_recent_days_from_env(monkeypatch, tmp_path):
    """An explicit RECENT_DAYS in .env wins over the default. Use a value
    that does not equal the current default so the test verifies override,
    not coincidence."""
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("RECENT_DAYS=45\n")
    assert config.recent_days() == 45


def test_recent_days_fallback_when_unset_or_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert config.recent_days() == 30
    (tmp_path / ".env").write_text("RECENT_DAYS=not-a-number\n")
    assert config.recent_days() == 30
    (tmp_path / ".env").write_text("RECENT_DAYS=0\n")
    assert config.recent_days() == 30


def test_default_weekdays_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("RUN_WEEKDAYS=Mon,Wed,Fri\n")
    assert config.default_weekdays() == ["Mon", "Wed", "Fri"]


def test_default_weekdays_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert config.default_weekdays() == ["Mon", "Tue", "Wed", "Thu", "Fri"]


def test_list_projects(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    for name in ("alpha", "beta"):
        project = tmp_path / "projects" / name
        project.mkdir(parents=True)
        (project / "config.yaml").write_text("queries:\n  - name: q\n    terms: [x]\n")
    assert config.list_projects() == ["alpha", "beta"]
