"""Interactive prompts (questionary-backed) for setup and project management.

Every prompt needs a terminal. Callers check `interactive()` first and fall
back to defaults or command-line flags when there is no TTY.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import questionary

from . import config, models, paths, ui

# Low-key palette: a soft blue text colour, no bold highlight on list rows.
# Every token is set so nothing falls back to questionary's default theme
# (which uses cyan).
_BLUE = "#87afd7"
_GREY = "#9e9e9e"
_STYLE = questionary.Style(
    [
        ("qmark", f"fg:{_BLUE}"),
        ("question", "bold"),
        ("answer", f"fg:{_BLUE}"),
        ("pointer", f"fg:{_BLUE}"),
        # `noreverse` strips the reverse-video highlight that prompt_toolkit's
        # base style applies; the cursor row is marked by the arrow alone, and
        # checked items get a plain blue text colour.
        ("highlighted", "noreverse"),
        ("selected", f"fg:{_BLUE} noreverse"),
        ("separator", f"fg:{_GREY}"),
        ("instruction", f"fg:{_GREY}"),
        ("text", ""),
        ("disabled", f"fg:{_GREY} italic"),
    ]
)

_MODEL_DEFAULT = "(agent default)"
_MODEL_OTHER = "other (enter manually)"


@dataclass(frozen=True)
class SetupChoices:
    agent: str
    model: str
    data_dir: Path
    run_time: str
    weekdays: list[str]
    recent_days: int


def interactive() -> bool:
    """True when both stdin and stdout are real terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _require(value: object, action: str) -> object:
    """Treat a None answer (Ctrl-C / EOF) as a cancelled action."""
    if value is None:
        raise RuntimeError(f"{action} cancelled")
    return value


def _select(message: str, choices: list, default: str | None = None) -> object:
    return _require(
        questionary.select(message, choices=choices, default=default, style=_STYLE).ask(),
        "setup",
    )


def _text(message: str, default: str = "") -> str:
    return str(
        _require(
            questionary.text(message, default=default, style=_STYLE).ask(),
            "setup",
        )
    ).strip()


def _int_text(message: str, default: int) -> int:
    """Text prompt parsed as a positive integer; falls back to the default."""
    answer = _text(message, default=str(default))
    try:
        value = int(answer)
    except ValueError:
        return default
    return value if value > 0 else default


def confirm(message: str, *, default: bool = False) -> bool:
    """Yes/no prompt. Returns the default if cancelled or non-interactive."""
    if not interactive():
        return default
    answer = questionary.confirm(message, default=default, style=_STYLE).ask()
    return default if answer is None else bool(answer)


def setup_defaults() -> SetupChoices:
    return SetupChoices(
        agent=config.detect_agent(),
        model="",
        data_dir=paths.default_data_root(),
        run_time=config.DEFAULT_TIME,
        weekdays=list(config.DEFAULT_WEEKDAYS),
        recent_days=config.DEFAULT_RECENT_DAYS,
    )


def _pick_model(agent: str) -> str:
    """Choose a model from a per-agent list, validating it against the CLI."""
    curated = config.MODEL_CHOICES.get(agent, [])
    while True:
        choice = _select(
            f"Model for {agent}:",
            choices=[_MODEL_DEFAULT, *curated, _MODEL_OTHER],
            default=_MODEL_DEFAULT,
        )
        if choice == _MODEL_DEFAULT:
            return ""
        model = (
            _text(f"{agent} model identifier:") if choice == _MODEL_OTHER else str(choice)
        )
        if not model:
            return ""

        ui.step(f"checking '{model}' with {agent}...")
        verdict = models.validate(agent, model)
        if verdict is False:
            ui.warn(f"{agent} rejected '{model}' — choose another")
            continue
        if verdict is None:
            ui.hint(f"could not verify '{model}' — using it anyway")
        else:
            ui.ok(f"'{model}' works")
        return model


def pick_weekdays(current: list[str]) -> list[str]:
    """Checkbox of weekdays; an empty answer keeps the current selection."""
    chosen = questionary.checkbox(
        "Which weekdays should briefings run? (space toggles, enter confirms)",
        choices=[
            questionary.Choice(day, checked=day in current) for day in config.WEEKDAYS
        ],
        style=_STYLE,
    ).ask()
    return chosen if chosen else list(current)


def pick_project(projects: list[str], message: str) -> str | None:
    """Single-select a project name; returns None if cancelled."""
    return questionary.select(message, choices=projects, style=_STYLE).ask()


def pick_papers(
    papers: list[dict], already_marked: set[str] | None = None
) -> list[dict] | None:
    """Checkbox over a list of paper dicts; returns the selected entries.

    Papers whose base arXiv id is in ``already_marked`` are pre-ticked, so the
    picker reflects the current state of read.json. Untick a pre-ticked paper
    to un-read it. Returns ``None`` on cancel (Ctrl-C / Esc), the full
    selection list otherwise (possibly empty if everything was unticked).
    """
    if not papers:
        return []
    already = already_marked or set()
    from . import reading  # local import; wizard is a leaf module otherwise

    choices = [
        questionary.Choice(
            f"[{paper.get('submitted', '????-??-??')}] "
            f"{paper.get('title', 'untitled')[:70]}  "
            f"({paper.get('arxiv_id', 'unknown')})",
            value=paper,
            checked=reading.strip_version(paper.get("arxiv_id", "")) in already,
        )
        for paper in papers
    ]
    return questionary.checkbox(
        "Tick the papers you have read (space toggles, enter confirms)",
        choices=choices,
        style=_STYLE,
    ).ask()


def pick_project_name() -> str | None:
    """Ask which project to author: pick an existing one or name a new one.

    Returns None if the prompt is cancelled.
    """
    new_label = "+ new project"
    existing = config.list_projects()
    if existing:
        choice = questionary.select(
            "Which project do you want to author?",
            choices=[*existing, new_label],
            style=_STYLE,
        ).ask()
        if choice is None:
            return None
        if choice != new_label:
            return choice
    name = questionary.text("New project name:", style=_STYLE).ask()
    return name.strip() if name else None


def resolve_setup(use_defaults: bool) -> SetupChoices:
    """Prompt for global settings, or return defaults when non-interactive."""
    if use_defaults or not interactive():
        return setup_defaults()

    agent = str(
        _select(
            "Which agent CLI should write the briefings?",
            choices=[
                questionary.Choice(
                    name if shutil.which(name) else f"{name}  (not installed)",
                    value=name,
                )
                for name in config.SUPPORTED_AGENTS
            ],
            default=config.detect_agent(),
        )
    )
    model = _pick_model(agent)
    data_dir = _text("Data directory:", default=str(paths.default_data_root()))
    run_time = _text("Daily run time (HH:MM):", default=config.DEFAULT_TIME)
    weekdays = pick_weekdays(list(config.DEFAULT_WEEKDAYS))
    recent_days = _int_text(
        "How many days back should each briefing search?",
        config.DEFAULT_RECENT_DAYS,
    )

    return SetupChoices(
        agent=agent,
        model=model,
        data_dir=Path(data_dir).expanduser(),
        run_time=run_time or config.DEFAULT_TIME,
        weekdays=weekdays,
        recent_days=recent_days,
    )
