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
| 2026-09-24 | `TurnOrder` tracks `written` (users who already wrote this cycle), and rows are `(cycle_no, position, user_id, consumed)` | Matches the `consumed` column in spec §10, and lets `append` ignore a player who rejoins after writing, so nobody writes twice in one cycle. |
| 2026-09-24 | A player who leaves and rejoins after writing waits for the next cycle | Keeps "everyone writes once per cycle" (spec §3.3) true. |
| 2026-09-24 | `apply_cluster_orders` takes the clusters explicitly: `(sorted_valid, clusters, orders)` | It needs the cluster membership to validate an ordering. The prompt plan's two-argument signature would have had to recompute clusters without knowing the margin. |
| 2026-09-24 | Tie margin comparisons use a 1e-9 tolerance | Otherwise 0.80 vs 0.78 (float difference 0.0200000000000000018) would not count as within a 0.02 margin. |
| 2026-09-24 | When the tie-break falls back, the whole cluster is ordered by submission time, even if similarities differ slightly within it | This is spec §3.7 step 4 taken literally. Differences within the margin are treated as noise. |
| 2026-09-24 | Clusters are measured from each cluster's highest member and don't overlap | Follows spec §3.7 step 4. Avoids chains where a 0.80/0.79/0.78/0.77 run would all tie. |
