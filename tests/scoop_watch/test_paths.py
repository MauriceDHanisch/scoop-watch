"""Tests for data-root resolution."""

from scoop_watch import paths


def test_data_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert paths.data_root() == tmp_path


def test_data_root_pointer(monkeypatch, tmp_path):
    monkeypatch.delenv("WATCH_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    target = tmp_path / "custom-data"
    paths.set_data_root(target)
    assert paths.data_root() == target


def test_data_root_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv("WATCH_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "empty-cfg")
    assert paths.data_root() == paths.default_data_root()


def test_archive_dir_under_project(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert paths.archive_dir("demo") == tmp_path / "projects" / "demo" / "archive"


def test_fetch_archive_dir_under_project(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert (
        paths.fetch_archive_dir("demo")
        == tmp_path / "projects" / "demo" / "fetch-archive"
    )


def test_read_json_path_under_project(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    assert paths.read_json_path("demo") == tmp_path / "projects" / "demo" / "read.json"


def test_next_version_stem_steps_through_v2_v3(monkeypatch, tmp_path):
    """First run uses the bare date; same-day reruns get -v2, -v3, ..."""
    import datetime as dt

    monkeypatch.setenv("WATCH_DATA_DIR", str(tmp_path))
    today = dt.date(2026, 5, 22)
    archive = paths.archive_dir("demo")
    fetch_archive = paths.fetch_archive_dir("demo")
    archive.mkdir(parents=True)
    fetch_archive.mkdir(parents=True)

    # First call: no files yet -> bare date.
    assert paths.next_version_stem("demo", today=today) == "2026-05-22"

    # Simulate the first run's outputs landing on disk.
    (archive / "2026-05-22.md").write_text("first")
    (fetch_archive / "2026-05-22.json").write_text("[]")
    assert paths.next_version_stem("demo", today=today) == "2026-05-22-v2"

    # And again after a -v2 run.
    (archive / "2026-05-22-v2.md").write_text("second")
    assert paths.next_version_stem("demo", today=today) == "2026-05-22-v3"

    # A single side of the pair is enough to claim that version.
    (fetch_archive / "2026-05-22-v3.json").write_text("[]")
    assert paths.next_version_stem("demo", today=today) == "2026-05-22-v4"
