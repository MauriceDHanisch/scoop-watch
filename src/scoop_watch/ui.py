"""Helpers for clean, consistent terminal output.

Palette: blue accents, green for success, dim grey for secondary text. Red is
reserved strictly for errors. Color is suppressed for non-terminals and when
NO_COLOR is set.
"""

from __future__ import annotations

import os
import sys

_RESET = "\033[0m"

# 256-color tones (fixed RGB, not remapped by the terminal theme): a soft
# light blue and a soft light green. Muted on purpose.
BLUE = "38;5;110"
GREEN = "38;5;114"
RED = "31"
BOLD = "1"
DIM = "2"


def color_enabled() -> bool:
    """Color is on only for a real terminal without NO_COLOR set."""
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(code: str, text: str, *, enabled: bool | None = None) -> str:
    """Wrap text in an ANSI code, unless color is disabled."""
    if enabled is None:
        enabled = color_enabled()
    return f"\033[{code}m{text}{_RESET}" if enabled else text


def blank() -> None:
    print()


def title(text: str) -> None:
    """A bold title followed by a dim rule, for the top of a command."""
    print(paint(BOLD, text))
    print(paint(DIM, "─" * max(len(text), 12)))


def header(text: str) -> None:
    print(paint(BOLD, text))


def step(text: str) -> None:
    print(f"{paint(BLUE, '→')} {text}")


def ok(text: str) -> None:
    print(f"{paint(GREEN, '✓')} {text}")


def warn(text: str) -> None:
    print(f"{paint(BLUE, '•')} {text}")


def error(text: str) -> None:
    print(f"{paint(RED, '✗')} {text}", file=sys.stderr)


def detail(label: str, value: str) -> None:
    print(f"  {paint(DIM, f'{label:<15}')}{value}")


def substep(text: str) -> None:
    """An indented sub-line under a step, for progress within a stage."""
    print(f"  {paint(DIM, '·')} {text}")


def hint(text: str) -> None:
    print(paint(DIM, text))
