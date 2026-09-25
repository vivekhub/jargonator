# Jargonator

A multiplayer Slack game. A writer submits a simple sentence about themselves, an LLM (via
OpenRouter) rewrites it as corporate jargon, the other players get it by DM and have 60 s to
guess the original, and an LLM judge scores each guess 0-100 on meaning (10/5/1 points). Python 3.12,
async Slack Bolt in Socket Mode, SQLite. `spec.md` is the full specification, and
`prompt_plan.md` is the step-by-step build plan.

## Commands

- `make check`: lint + format check + `mypy --strict src tests/fakes scripts` + tests with coverage ≥ 85% on `domain/` and `engine/` (must pass after every step)
- `make test`: run the tests (no coverage)
- `make coverage`: tests plus the coverage gate
- `make try-llm`: try the real prompts against OpenRouter (needs `OPENROUTER_API_KEY`)
- `make format`: auto-format and auto-fix lint
- `make run`: run the bot

## Standing rules

- `spec.md` is the source of truth. Cite the section you implement in module docstrings.
- TDD: write failing tests first, then the minimal code, then refactor.
- Every step ends with `make check` passing.
- `domain/` is pure and synchronous: no I/O, no Slack/LLM/DB imports.
- `engine/` depends only on protocols (Repo, SlackGateway, LLMTasks, Clock, Scheduler), never
  on concrete Slack/OpenRouter/DB classes.
- No network access in tests. Use the fakes in `tests/fakes/`.
- Never log user sentences or guesses above DEBUG. Never log tokens.
- Record any decision not covered by the spec in `DECISIONS.md`.
- No orphaned code: anything new must be used by the composition root or by code already wired.
- Game data is never deleted (no purge or cleanup jobs).
- All AI work goes through the LLM (OpenRouter). No local ML models (spec v1.1).
