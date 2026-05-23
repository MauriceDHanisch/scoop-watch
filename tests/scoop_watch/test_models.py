"""Tests for model-rejection detection."""

from scoop_watch import models


def test_detects_claude_rejection():
    output = (
        "There's an issue with the selected model (zzz). It may not exist "
        "or you may not have access to it."
    )
    assert models._is_rejection(output) is True


def test_detects_codex_rejection():
    output = (
        'ERROR: {"type":"error","status":400,"error":'
        '{"type":"invalid_request_error","message":"The \'zzz\' model is '
        'not supported when using Codex with a ChatGPT account."}}'
    )
    assert models._is_rejection(output) is True


def test_accepts_a_normal_reply():
    assert models._is_rejection("ok") is False


def test_validate_unknown_agent_is_none():
    assert models.validate("bard", "anything") is None


def test_argv_per_agent():
    assert models._argv("claude", "opus") == ["claude", "-p", "--model", "opus"]
    assert models._argv("codex", "gpt-5.5") == [
        "codex",
        "exec",
        "--model",
        "gpt-5.5",
    ]
    assert models._argv("bard", "x") is None
