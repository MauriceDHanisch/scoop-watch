"""Tests for project scaffolding from packaged templates."""

from scoop_watch import paths, scaffold


def test_scaffold_creates_project_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    created = scaffold.scaffold("demo")
    assert set(created) == {"config.yaml", "layout.md", "project.md"}
    assert paths.config_path("demo").is_file()
    assert paths.layout_path("demo").is_file()
    assert paths.description_path("demo").is_file()
    assert scaffold.exists("demo")


def test_scaffold_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    scaffold.scaffold("demo")
    assert scaffold.scaffold("demo") == []
