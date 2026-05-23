# Makefile for scoop-watch

.PHONY: setup lint format format-check typecheck test check

# One-shot setup for collaborators.
setup:
	uv sync --extra dev
	git config --unset-all core.hooksPath 2>/dev/null || true
	uv run --extra dev pre-commit install

# Individual commands for granular control. `uv run --extra dev` ensures the
# dev tools (ruff, ty, pytest) are installed before running.
lint:
	uv run --extra dev ruff check .

format:
	uv run --extra dev ruff format .

format-check:
	uv run --extra dev ruff format --check .

typecheck:
	uv run --extra dev ty check src tests

test:
	uv run --extra dev pytest

# Combined gate, used by the pre-commit hook and CI. `format-check` only
# verifies formatting (it does not rewrite files); run `make format` to apply.
check: lint format-check typecheck test
