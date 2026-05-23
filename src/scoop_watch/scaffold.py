"""Create new projects from the packaged templates."""

from __future__ import annotations

import shutil

from . import paths

_TEMPLATE_FILES = ("config.yaml", "layout.md", "project.md")


def exists(project: str) -> bool:
    return paths.config_path(project).is_file()


def scaffold(project: str) -> list[str]:
    """Create the project directory and any missing files.

    Returns the names of the files that were created.
    """
    project_dir = paths.project_dir(project)
    project_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    for filename in _TEMPLATE_FILES:
        destination = project_dir / filename
        if not destination.exists():
            shutil.copy(paths.template_dir() / filename, destination)
            created.append(filename)

    return created
