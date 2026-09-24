.PHONY: install lint format typecheck test check run

UV ?= uv

install:
	$(UV) sync

lint:
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests

format:
	$(UV) run ruff format src tests
	$(UV) run ruff check --fix src tests

typecheck:
	$(UV) run mypy --strict src

test:
	$(UV) run pytest

check: lint typecheck test

run:
	$(UV) run jargonator
