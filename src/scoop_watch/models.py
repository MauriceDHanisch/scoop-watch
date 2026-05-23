"""Validate a model name with a trial run of the agent CLI.

Both `claude` and `codex` exit 0 even when the model is rejected and report
the failure in their output, so validation inspects the text rather than the
exit code.
"""

from __future__ import annotations

import shutil
import subprocess

_PROBE = "Reply with exactly: ok"
_TIMEOUT = 120

# Substrings (matched case-insensitively) that mean the model was rejected.
_REJECTION_MARKERS = (
    "issue with the selected model",  # claude
    "invalid_request_error",  # codex
    "is not supported",  # codex
    "model not found",
)


def _argv(agent: str, model: str) -> list[str] | None:
    if agent == "claude":
        return ["claude", "-p", "--model", model]
    if agent == "codex":
        return ["codex", "exec", "--model", model]
    return None


def _is_rejection(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _REJECTION_MARKERS)


def validate(agent: str, model: str) -> bool | None:
    """Trial-run `agent` with `model`.

    Returns True if the model was accepted, False if it was rejected, and
    None if it could not be determined (CLI missing, timeout, other failure).
    """
    argv = _argv(agent, model)
    if argv is None or shutil.which(argv[0]) is None:
        return None
    try:
        result = subprocess.run(
            argv,
            input=_PROBE,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return not _is_rejection(f"{result.stdout}\n{result.stderr}")
