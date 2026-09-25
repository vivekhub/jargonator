# Decisions

Decisions made during implementation that `spec.md` does not cover.

| Date | Decision | Reason |
|---|---|---|
| 2026-09-24 | No `.python-version` file; Python 3.12 is pinned via `requires-python = ">=3.12,<3.13"` | A `.python-version` pin made pyenv shims hide the `uv` binary on the dev machine. uv still selects 3.12 from `requires-python`. |
| 2026-09-24 | Makefile uses `UV ?= uv` | Lets developers point at a specific uv binary (`make check UV=/path/to/uv`). |
| 2026-09-24 | **(Superseded by spec v1.1)** Slow tests are excluded by default (`addopts = "-m 'not slow'"`); run them with `make test-slow` | Keeps the default test run fast and free of heavy deps (torch). |
| 2026-09-24 | State machine adds `PAUSED_PLAYERS --NEXT_ROUND_INSUFFICIENT_PLAYERS--> PAUSED_PLAYERS` | The host may click Next while still short of players. Staying paused is explicit rather than an invalid event. |
| 2026-09-24 | **(Superseded by spec v1.1)** `JUDGING --JUDGED--> AWAITING_NEXT` also covers a round voided during judging | Spec §5 has no separate edge for a judging failure. The outcome (go to AWAITING_NEXT) is the same. |
| 2026-09-24 | A round with zero guessers goes `GENERATING → GUESSING → JUDGING` immediately | Avoids a special-case edge in §5. The round still flows through the normal judging path. |
| 2026-09-24 | `TurnOrder` tracks `written` (users who already wrote this cycle), and rows are `(cycle_no, position, user_id, consumed)` | Matches the `consumed` column in spec §10, and lets `append` ignore a player who rejoins after writing, so nobody writes twice in one cycle. |
| 2026-09-24 | A player who leaves and rejoins after writing waits for the next cycle | Keeps "everyone writes once per cycle" (spec §3.3) true. |
| 2026-09-24 | **(Superseded by spec v1.1)** `apply_cluster_orders` takes the clusters explicitly: `(sorted_valid, clusters, orders)` | It needs the cluster membership to validate an ordering. The prompt plan's two-argument signature would have had to recompute clusters without knowing the margin. |
| 2026-09-24 | **(Superseded by spec v1.1)** Tie margin comparisons use a 1e-9 tolerance | Otherwise 0.80 vs 0.78 (float difference 0.0200000000000000018) would not count as within a 0.02 margin. |
| 2026-09-24 | **(Superseded by spec v1.1)** When the tie-break falls back, the whole cluster is ordered by submission time, even if similarities differ slightly within it | This is spec §3.7 step 4 taken literally. Differences within the margin are treated as noise. |
| 2026-09-24 | **(Superseded by spec v1.1)** Clusters are measured from each cluster's highest member and don't overlap | Follows spec §3.7 step 4. Avoids chains where a 0.80/0.79/0.78/0.77 run would all tie. |
| 2026-09-24 | `RoundSummary.level` is optional | Skipped rounds never get a level assigned. |
| 2026-09-24 | The leakage stemmer also strips a trailing "e", and "es" is handled before "s" | Needed so "horse"/"horses" and "like"/"liked"/"liking" match. The prompt's suggested rule set would have mismatched them. |
| 2026-09-24 | `leaked_words` returns the words as written in the original, not their stems | They go into the stricter jargon prompt ("do not use: cats"), where real words read better than stems like "lik". |
| 2026-09-24 | The best-guess and most-unhinged highlights go to the earlier round on a tie | Deterministic, and rewards whoever got there first. |
| 2026-09-24 | Migrations live in `src/jargonator/db/migrations` (not a top-level `migrations/`) | They ship inside the installed package, so the Docker image can run them without the source tree. The root `alembic.ini` points there for CLI use. |
| 2026-09-24 | Migrations run synchronously (plain `sqlite` driver) via `asyncio.to_thread` | Alembic's async env would need a second event loop inside the app's running loop. |
| 2026-09-24 | Migrations are hand-written with plain SQLAlchemy types. A test asserts no autogenerate diff against the models | Keeps migrations independent of app code that may change later, while guaranteeing they match. |
| 2026-09-24 | A player returning from `left` gets a fresh `joined_at`. Inactive→active keeps it | Someone who left and came back shouldn't count as the longest-standing player for host transfer (spec §3.10). |
| 2026-09-24 | `find_active_game_for_user` counts `inactive` players as still in the game | Inactive players are still in the game (score kept, can rejoin with Join), so they can't be in two games at once. |
| 2026-09-24 | stdlib loggers (alembic, slack, aiohttp) go through structlog's JSON formatter; alembic/sqlalchemy are held at WARNING | Every stdout line stays valid JSON (spec §14). |
| 2026-09-24 | SQLite runs with a 30 s busy timeout, WAL mode and foreign keys on | Concurrent async writers wait instead of failing with "database is locked". |
| 2026-09-24 | Rounds get UNIQUE(game_id, number), and players get an index on user_id | Cheap integrity guarantee, and it speeds up the one-game-per-player lookup. |
| 2026-09-24 | No `status_message_channel` column on rounds (the prompt plan's optional addition) | Round status and results messages are always posted in the game's own channel, which the game record already stores. |
| 2026-09-24 | `games.last_writer` added in migration 0002. It's written only by `save_turn_order`, not via `update_game` | It belongs to the turn order and must be saved in the same transaction as the turn-order rows. |
| 2026-09-24 | `save_guess_results` takes `GuessResult` named tuples, not bare tuples | Keeps the argument self-documenting and type-checked. |
| 2026-09-24 | `add_guess` raises `DuplicateGuessError` only for UNIQUE violations. Other integrity errors (e.g. an unknown round) propagate unchanged | Avoids masking real bugs as "you already guessed". |
| 2026-09-24 | **(Superseded by spec v1.1)** `SentenceTransformerScorer` takes an injectable `loader` (default: lazy import of sentence_transformers) | Lets the scorer's own logic (normalising, batching, clamping, rounding, readiness, off-loop execution) be unit-tested with a stub model, without torch installed. |
| 2026-09-24 | **(Superseded by spec v1.1)** Dot products are computed in pure Python on `float` lists | Avoids a hard numpy dependency outside the optional extra. The cost is negligible (384-dim vectors, a handful of guesses). |
| 2026-09-24 | **(Superseded by spec v1.1)** `sentence-transformers` isn't installed in the dev environment by default. The real-model test self-skips (`make test-slow` after `uv sync --extra embeddings`) | Keeps the dev setup light. The Docker image installs the extra and bakes in the model (Prompt 25). |
| 2026-09-24 | **(Superseded by spec v1.1)** `torch` is listed in the `embeddings` extra and sourced from the CPU-only PyTorch index (`[tool.uv.sources]`) | The default Linux wheel bundles several GB of CUDA libraries the bot never uses. The CPU build installs in seconds and is what Docker will use. |
| 2026-09-24 | **Spec v1.1:** local embeddings removed. An LLM judge scores every guess 0–100 on meaning (§3.7, §7.4, §8) | Testing the real model showed it ranks by shared wording ("I have two dogs" 0.75 beat "I own a pair of kitties" 0.60 for "I have two cats"). A larger bi-encoder and a cross-encoder were also unreliable. Decided with the user. |
| 2026-09-24 | **Spec v1.1:** jargon generation and judging are "essential". A failure retries once after 30 s, then the game ends with the final scoreboard (§3.11). Moderation and the quip keep their own fallbacks | The user's rule. Applied to both essential steps for consistency. |
| 2026-09-24 | **Spec v1.1:** an exact judge-score tie goes to the earlier submission. There's no tie-break LLM call | The judge already compares all guesses in one call. Decided with the user. |
| 2026-09-24 | The judge defaults to the stronger model (`anthropic/claude-sonnet-5`) | Fair scoring is the core of the game. Configurable via `LLM_MODEL_JUDGE`. |
| 2026-09-24 | Migration 0003 drops `guesses.similarity` and adds `guesses.score` (int) and `rounds.llm_retry_at`. Earlier migrations are untouched | "Never edit a shipped migration." No production data exists yet, so dropping the float column loses nothing. |
| 2026-09-24 | `GameEvent.JARGON_FAILED` removed. A second essential failure is an `END` event | Matches the new failure rule. A first failure keeps the state and waits for the retry timer. |
| 2026-09-24 | `TurnOrder.requeue_front` kept | It's no longer needed for LLM failures, but it's a tested general tool, and cheap to keep for the engine. |
| 2026-09-25 | The OpenAI SDK is built with `max_retries=0` | `OpenRouterClient` owns the retry policy (spec §7.1). The SDK's default of 2 internal retries would silently triple every attempt. |
| 2026-09-25 | Any unexpected exception from the SDK counts as a non-retryable attempt failure (skip to the fallback), rather than crashing the caller | The engine only needs to handle `LLMError`. The original error is still logged and chained. |
| 2026-09-25 | A Markdown code fence around the JSON reply is stripped before parsing | Some models wrap JSON in fences even in JSON mode. Accepting it avoids a pointless retry. |
| 2026-09-25 | Empty/missing message content counts as a retryable parse failure | Same class of problem as malformed JSON. |
| 2026-09-25 | Every SQLite transaction starts with `BEGIN IMMEDIATE` (transactions run one at a time) | Opus review finding 1: "one active game per player" was a check-then-act race across games (reproduced: a user joined 2 games). Serialising transactions makes every check-then-act atomic. The concurrency loss is irrelevant at this scale, and the 30 s busy timeout queues waiters. |
| 2026-09-25 | `Repo.join_game` (check + upsert, atomic) replaces `add_or_reactivate_player`. `create_game` adds the host and its deadlines in the same transaction | Review findings 1 and 4: no cross-game double join, and no half-created game that would block the channel with no idle timer. |
| 2026-09-25 | Each LLM attempt is bounded by `asyncio.timeout(LLM_TIMEOUT_SECONDS)`, and a timeout is retryable | Review finding 2: the SDK timeout is per read/write step, so a trickling response ran for minutes (reproduced). |
| 2026-09-25 | `complete_json(..., validate=callback)`: a callback failure is a retryable attempt failure | Review finding 5: the judge's "one integer score per input id" check depends on the input, so it must run inside the retry loop. Judge scores will use strict ints (Prompt 10). |
| 2026-09-25 | Guesses are ordered by `submitted_at`, then SQLite `rowid` (insertion order), and `sort_valid` is stable | Review finding 3: ties were broken by random uuid4 ids (reproduced), which made "earlier submission wins" non-deterministic. |
| 2026-09-25 | `SystemClock.sleep_until` loops until the wall clock reaches the deadline | Review finding 6: a single monotonic sleep can wake early after a wall-clock adjustment during the 2 h idle wait. |
| 2026-09-25 | An autouse test fixture restores logging state after each test | Review finding 7: a stale root handler caused "--- Logging error ---" noise. |
| 2026-09-25 | Guesses are relabelled `g1, g2, …` before reaching the LLM and mapped back afterwards | Short ids are harder for the model to garble than uuids, save tokens, and keep internal ids out of prompts. |
| 2026-09-25 | Player text is escaped by replacing `<`/`>` with `‹`/`›` inside tags, and every system prompt says tag contents are data | Prompt-injection hardening (spec §7.1). A player can't close a tag and smuggle instructions. |
| 2026-09-25 | Blank jargon (after stripping quotes) counts as a failed attempt via the `validate` callback | An empty jargon DM would break the round. Retrying is the right response. |
| 2026-09-25 | The quip is truncated to 25 words with a trailing "…". A blank quip becomes `None` (no quip block) | Spec §7.5 word limit. Never post an empty line. |
| 2026-09-25 | `mypy --strict` now also checks `tests/fakes/` | The fakes stand in for real components in every engine test, so they must provably match the protocols (e.g. `FakeLLM` vs `LLMTasks`). |
| 2026-09-25 | The fake OpenAI connection moved to `tests/fakes/openai_stub.py` | Shared by the client and tasks tests. |
| 2026-09-25 | Optional `LLM_MODEL` setting switches every per-task model at once. Explicitly set per-task models win, and the fallback is untouched | The user wanted to switch to NVIDIA Nemotron easily. Keeping the fallback on another vendor means an outage at one provider doesn't take out both. |
| 2026-09-25 | `extract_json` also extracts the `{...}` object from surrounding text (chatter, `<think>` blocks), not just code fences | Some OpenRouter models (e.g. free Nemotron variants) don't support JSON mode, and OpenRouter silently ignores unsupported params. |
