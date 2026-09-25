# 💼 Jargonator

A multiplayer Slack game. Each round, one player writes a simple sentence about themselves.
An AI rewrites it as dense corporate jargon, and everyone else gets it by DM with 60 seconds
to guess the original. An AI judge scores every guess 0–100 on meaning: the top three earn
**10 / 5 / 1** points, and if nobody gets close, the writer earns **+10**.

> "I have two cats" → *"I steward a dual-asset, low-touch feline stakeholder ecosystem,
> optimizing purr-driven engagement KPIs across my residential footprint."*

The full specification is in [`spec.md`](spec.md). Implementation decisions are in
[`DECISIONS.md`](DECISIONS.md).

## How to play

1. Invite the bot to a channel: `/invite @Jargonator`.
2. Run `/jargonator start` and click **Open lobby**. Players click **Join**.
3. The host clicks **Start game** (or the lobby auto-starts after the join window).
4. Each round:
   - The **writer** gets a DM and submits one simple, true sentence about themselves.
   - Everyone else gets the jargon by DM (with its level: 🌶️ Mild, 🌶️🌶️ Spicy or
     🌶️🌶️🌶️ Unhinged) and submits **one** guess.
   - Guessing closes after 60 s, or as soon as everyone has guessed. Results appear in the
     channel.
5. The host clicks **Next round** or **End game**. The game also ends after 2 hours idle.

| Command | Who | What it does |
|---|---|---|
| `/jargonator start` | anyone | Start a game in this channel |
| `/jargonator join` | anyone | Join the game (any time) |
| `/jargonator leave` | player | Leave (your score is kept if you come back) |
| `/jargonator status` | anyone | State, host, time left and scores |
| `/jargonator next` | host | Start the next round |
| `/jargonator kick @someone` | host | Remove a player |
| `/jargonator end` | host or workspace admin | End the game and post the final scoreboard |
| `/jargonator help` | anyone | Rules and commands |

If the host goes quiet, **Claim host** appears on the results after 5 minutes. A writer
who misses 3 turns in a row is marked inactive until they click Join again. If the AI
service fails, the bot retries once after 30 seconds, then ends the game with the final
scoreboard.

## Setup

### 1. Create the Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest**, pick your
   workspace, and paste [`slack-manifest.yaml`](slack-manifest.yaml).
2. **Install to Workspace**, then copy the **Bot User OAuth Token** (`xoxb-…`) from
   *OAuth & Permissions*.
3. Under *Basic Information* → **App-Level Tokens**, create a token with the
   `connections:write` scope (`xapp-…`). Socket Mode uses this, so no public URL is needed.

### 2. Get an OpenRouter key

Create a key at <https://openrouter.ai/keys> (`sk-or-…`) and add some credit. A round makes
about four small LLM calls.

### 3. Configure and run

```bash
cp .env.example .env        # fill in SLACK_BOT_TOKEN, SLACK_APP_TOKEN, OPENROUTER_API_KEY
docker compose up -d --build
docker compose logs -f      # JSON logs; look for "socket_connected"
curl localhost:8080/healthz # {"status": "ok", "socket": "connected", "db": "ok"}
```

Game data lives in the `jargonator-data` Docker volume (SQLite). It is **kept indefinitely**:
the bot never deletes games, sentences, guesses or scores.

### Choosing models

Every AI task can use any [OpenRouter model](https://openrouter.ai/models). To switch all
tasks at once:

```bash
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
```

Per-task settings (such as `LLM_MODEL_JUDGE`) override `LLM_MODEL`. Try a model's jargon and
judging before switching:

```bash
uv run python scripts/try_llm.py --model nvidia/nemotron-3-super-120b-a12b "I have two cats" "I own kitties" "I have two dogs"
```

## Configuration

Set these in `.env` (or the environment). Only the first three are required.

| Variable | Default | Meaning |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | Bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | — | App-level token for Socket Mode (`xapp-…`) |
| `OPENROUTER_API_KEY` | — | OpenRouter key (`sk-or-…`) |
| `LLM_MODEL` | — | Shortcut: one model for every task below (not the fallback) |
| `LLM_MODEL_JARGON` | `anthropic/claude-sonnet-5` | Writes the jargon |
| `LLM_MODEL_JUDGE` | `anthropic/claude-sonnet-5` | Scores guesses 0–100 |
| `LLM_MODEL_QUIP` | `anthropic/claude-sonnet-5` | Writes the one-line quip |
| `LLM_MODEL_MODERATION` | `anthropic/claude-haiku-4.5` | Checks sentences and guesses |
| `LLM_MODEL_FALLBACK` | `openai/gpt-4o-mini` | Used when a task's model fails |
| `LLM_TIMEOUT_SECONDS` | `15` | Hard limit per LLM attempt |
| `LLM_FAILURE_RETRY_SECONDS` | `30` | Wait before the single retry of an essential step |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/jargonator.db` | SQLite file (Docker sets this) |
| `POINTS_FIRST` | `10` | Points for 1st place |
| `POINTS_SECOND` | `5` | Points for 2nd place |
| `POINTS_THIRD` | `1` | Points for 3rd place |
| `WRITER_BONUS_POINTS` | `10` | Writer's "nobody cracked it" bonus |
| `WRITER_BONUS_THRESHOLD` | `50` | Bonus when the best score (0–100) is below this |
| `DEFAULT_GUESS_SECONDS` | `60` | Start-form default: guessing time |
| `DEFAULT_WRITER_SECONDS` | `90` | Start-form default: writing time |
| `DEFAULT_JOIN_WINDOW_SECONDS` | `120` | Start-form default: lobby auto-start (0 = host starts) |
| `WRITER_REMINDER_SECONDS` | `30` | Remind the writer this long before the deadline |
| `MAX_CONSECUTIVE_MISSES` | `3` | Missed turns in a row before a player goes inactive |
| `HOST_CLAIM_AFTER_SECONDS` | `300` | When **Claim host** appears |
| `IDLE_TIMEOUT_SECONDS` | `7200` | End a game after this long without activity |
| `MIN_PLAYERS` | `2` | Players needed to start or continue |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs prompts and replies |
| `HEALTH_PORT` | `8080` | Port for `GET /healthz` |

## Development

```bash
uv sync           # Python 3.12 + dev tools
make check        # ruff, format check, mypy --strict, pytest with coverage ≥ 85%
make test         # tests only (about 30 s, no network)
make run          # run locally (set DATABASE_URL=sqlite+aiosqlite:///./data/jargonator.db)
make try-llm      # try the real prompts against OpenRouter
```

To change the database schema, add a migration with
`uv run alembic revision -m "..."`. Never edit a shipped migration.

### Architecture

```
main.py      startup/shutdown, health server, Socket Mode
slack/       thin adapters: /jargonator, buttons, modals → engine; Block Kit builders
engine/      GameEngine (the only place state changes, one lock per game), TimerService
db/ llm/     repository (SQLite), OpenRouter client and game prompts, behind protocols
domain/      pure logic: state machine, turn order, scoring, standings, leakage check
```

Deadlines are stored in the database and timers are rebuilt from them on startup, so a
restart resumes every game where it left off. Tests use fakes for Slack, the LLM, the clock
and the scheduler (`tests/fakes/`), plus a real SQLite database.

### Acceptance criteria → tests

| # | Criterion (spec §15) | Tests |
|---|---|---|
| 1 | `/jargonator start` → modal → lobby → join → start | `tests/integration/test_full_game.py`, `tests/unit/engine/test_lobby.py` |
| 2 | Writer DMed; jargon DMed to guessers only, never in the channel first | `tests/integration/test_full_game.py`, `tests/unit/engine/test_generation.py` |
| 3 | One final guess; closes at the deadline or when all have guessed | `tests/unit/engine/test_guessing.py` |
| 4 | Results: original, jargon, 10/5/1, scores, quip, writer bonus, leaderboard | `tests/unit/engine/test_judging.py`, `tests/unit/slack/test_blocks_results.py` |
| 5 | Host-only controls, Claim host after 5 min, host transfer | `tests/unit/engine/test_timeouts_and_host.py`, `tests/integration/test_rules.py` |
| 6 | Writer timeouts skip; 3 in a row → inactive | `tests/unit/engine/test_timeouts_and_host.py`, `tests/integration/test_rules.py` |
| 7 | Bad sentences rejected privately; offensive guesses hidden | `tests/unit/engine/test_writer_phase.py`, `tests/unit/engine/test_judging.py` |
| 8 | Restart mid-round resumes correctly | `tests/integration/test_recovery.py` |
| 9 | Ends via End, admin, idle or LLM failure, with the final scoreboard | `tests/unit/engine/test_next_and_end.py`, `tests/integration/test_rules.py` |
| 10 | Quality gates pass | `tests/unit/test_docs.py` (plus `make check`) |

## Troubleshooting

- **"I can't post here yet"**: invite the bot to the channel (`/invite @Jargonator`).
- **DMs never arrive**: the app needs the `im:write` scope. Reinstall it after changing
  scopes.
- **`/healthz` returns 503**: `socket: disconnected` means `SLACK_APP_TOKEN` is wrong or
  lacks `connections:write`. `db: error` means the SQLite volume isn't writable.
- **"The AI service is having a hiccup"**, or the game ends with "the AI service isn't
  responding": check the OpenRouter key and credit, and the model ids (`LLM_MODEL_*`).
  `LOG_LEVEL=DEBUG` shows each call.
