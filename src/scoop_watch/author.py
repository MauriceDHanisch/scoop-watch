"""Interactive project authoring.

Launches the configured agent CLI in the project directory, primed to
interview the user and write project.md and layout.md.
"""

from __future__ import annotations

import shutil
import subprocess

from . import config, paths, scaffold


def _primer(project: str) -> str:
    """Render the authoring primer with this project's file paths."""
    text = paths.package_text("authoring_primer.md")
    replacements = {
        "{{project}}": project,
        "{{project_md}}": str(paths.description_path(project)),
        "{{layout_md}}": str(paths.layout_path(project)),
        "{{config_yaml}}": str(paths.config_path(project)),
        "{{example_project}}": paths.package_text("example_project.md").rstrip("\n"),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def author(project: str) -> None:
    """Scaffold the project, then hand off to the agent CLI interactively."""
    created = scaffold.scaffold(project)
    if created:
        print(f"scaffolded {project}: {', '.join(created)}")

    agent = config.agent()
    if shutil.which(agent) is None:
        raise RuntimeError(
            f"'{agent}' not found on PATH; install it or set AGENT in .env"
        )

    # Both `claude` and `codex` accept --model as a top-level option.
    command = [agent]
    model = config.model()
    if model:
        command += ["--model", model]
    command.append(_primer(project))

    print(f"launching {agent} ({model or 'default model'}) for '{project}'...\n")
    subprocess.run(command, cwd=paths.project_dir(project))
