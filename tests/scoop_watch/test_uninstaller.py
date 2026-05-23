"""Tests for the uninstall helper (against temporary directories)."""

from scoop_watch import paths, uninstaller


def _stub_layout(monkeypatch, tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "marker").write_text("x")
    shim = tmp_path / "bin" / "scoop-watch"
    shim.parent.mkdir()
    shim.write_text("#!/bin/sh")
    data = tmp_path / "data"
    data.mkdir()
    (data / "keep.md").write_text("briefing")
    monkeypatch.setattr(paths, "app_install_dir", lambda: app)
    monkeypatch.setattr(paths, "shim_path", lambda: shim)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(paths, "systemd_user_dir", lambda: tmp_path / "no-units")
    monkeypatch.setenv("WATCH_DATA_DIR", str(data))
    return app, shim, data


def test_uninstall_removes_program_keeps_data(monkeypatch, tmp_path):
    app, shim, data = _stub_layout(monkeypatch, tmp_path)
    result = uninstaller.uninstall(remove_data=False)
    assert not app.exists()
    assert not shim.exists()
    assert data.exists()
    assert result.data_removed is False
    assert result.timers == []


def test_uninstall_purges_data(monkeypatch, tmp_path):
    app, shim, data = _stub_layout(monkeypatch, tmp_path)
    result = uninstaller.uninstall(remove_data=True)
    assert not data.exists()
    assert result.data_removed is True


def test_uninstall_flags_a_running_timer(monkeypatch, tmp_path):
    """A timer that was active is reported as `was_active`, and its unit file
    is deleted, so uninstall can tell the user it killed a live timer."""
    import subprocess

    _stub_layout(monkeypatch, tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    timer = units / "scoop-watch-demo.timer"
    timer.write_text("[Timer]\n")
    monkeypatch.setattr(uninstaller.paths, "systemd_user_dir", lambda: units)

    def fake_run(cmd, **kwargs):
        active = "is-active" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="active\n" if active else "")

    monkeypatch.setattr(uninstaller.subprocess, "run", fake_run)

    result = uninstaller.uninstall(remove_data=False)

    assert len(result.timers) == 1
    assert result.timers[0].name == "scoop-watch-demo.timer"
    assert result.timers[0].was_active is True
    assert not timer.exists()
