"""Tests for the self-update helpers."""

from scoop_watch import paths, updater


def test_parse_sha_extracts_commit():
    assert updater._parse_sha('{"sha": "abc123", "node_id": "n"}') == "abc123"


def test_installed_sha_reads_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_install_dir", lambda: tmp_path)
    (tmp_path / ".installed-sha").write_text("deadbeef\n")
    assert updater.installed_sha() == "deadbeef"


def test_installed_sha_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_install_dir", lambda: tmp_path)
    assert updater.installed_sha() is None
