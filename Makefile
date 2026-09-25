.PHONY: install lint format typecheck test check run try-llm

UV ?= uv

install:
	$(UV) sync

lint:
	$(UV) run ruff check src tests scripts
	$(UV) run ruff format --check src tests scripts

format:
	$(UV) run ruff format src tests scripts
	$(UV) run ruff check --fix src tests scripts

typecheck:
	$(UV) run mypy --strict src tests/fakes scripts

test:
	$(UV) run pytest

check: lint typecheck test

run:
	$(UV) run jargonator

try-llm:
	$(UV) run python scripts/try_llm.py
