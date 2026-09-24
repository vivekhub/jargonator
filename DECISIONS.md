# Decisions

Decisions made during implementation that `spec.md` does not cover.

| Date | Decision | Reason |
|---|---|---|
| 2026-09-24 | No `.python-version` file; Python 3.12 is pinned via `requires-python = ">=3.12,<3.13"` | A `.python-version` pin made pyenv shims hide the `uv` binary on the dev machine. uv still selects 3.12 from `requires-python`. |
| 2026-09-24 | Makefile uses `UV ?= uv` | Lets developers point at a specific uv binary (`make check UV=/path/to/uv`). |
| 2026-09-24 | Slow tests are excluded by default (`addopts = "-m 'not slow'"`); run them with `make test-slow` | Keeps the default test run fast and free of heavy deps (torch). |
| 2026-09-24 | State machine adds `PAUSED_PLAYERS --NEXT_ROUND_INSUFFICIENT_PLAYERS--> PAUSED_PLAYERS` | The host may click Next while still short of players. Staying paused is explicit rather than an invalid event. |
| 2026-09-24 | `JUDGING --JUDGED--> AWAITING_NEXT` also covers a round voided during judging | Spec §5 has no separate edge for a judging failure. The outcome (go to AWAITING_NEXT) is the same. |
| 2026-09-24 | A round with zero guessers goes `GENERATING → GUESSING → JUDGING` immediately | Avoids a special-case edge in §5. The round still flows through the normal judging path. |
