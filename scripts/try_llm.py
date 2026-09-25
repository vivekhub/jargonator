"""Try the real LLM prompts against OpenRouter (developer tool, not part of the bot).

Needs OPENROUTER_API_KEY in the environment or in .env. Slack tokens are not needed.

    uv run python scripts/try_llm.py
    uv run python scripts/try_llm.py "I ran a marathon last year" "I jog" "I like cake"
    uv run python scripts/try_llm.py --level unhinged "I have two cats" "I own kitties"
    uv run python scripts/try_llm.py --model nvidia/nemotron-3-super-120b-a12b

Each run makes about 6 small LLM calls (a fraction of a cent with the default models).
"""

import argparse
import asyncio
import time

from jargonator.config import Settings
from jargonator.domain.state import JargonLevel
from jargonator.domain.text import is_leaky, leaked_words
from jargonator.llm.client import LLMError, build_openrouter_client
from jargonator.llm.tasks import OpenRouterTasks
from jargonator.logging import configure_logging

DEFAULT_SENTENCE = "I have two cats"
DEFAULT_GUESSES = [
    "I own a pair of kitties",
    "I have two dogs",
    "I have a cat",
    "I own pets",
    "I like skiing",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("sentence", nargs="?", default=DEFAULT_SENTENCE)
    parser.add_argument("guesses", nargs="*", default=None)
    parser.add_argument(
        "--level",
        choices=[lvl.value for lvl in JargonLevel],
        help="only generate this jargon level (default: all three)",
    )
    parser.add_argument(
        "--model",
        help="use this OpenRouter model for every task (overrides .env), "
        "e.g. nvidia/nemotron-3-super-120b-a12b",
    )
    parser.add_argument("--fallback", help="use this model as the fallback (overrides .env)")
    parser.add_argument("--verbose", action="store_true", help="show one log line per LLM attempt")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    guesses = args.guesses or DEFAULT_GUESSES
    configure_logging("INFO" if args.verbose else "ERROR")
    # Slack tokens are required by Settings but unused here.
    overrides: dict[str, str] = {"slack_bot_token": "unused", "slack_app_token": "unused"}
    if args.model:  # beats both LLM_MODEL and the per-task settings from .env
        for task in ("jargon", "judge", "quip", "moderation"):
            overrides[f"llm_model_{task}"] = args.model
    if args.fallback:
        overrides["llm_model_fallback"] = args.fallback
    settings = Settings(**overrides)  # type: ignore[arg-type]
    client = build_openrouter_client(settings)
    tasks = OpenRouterTasks(client)

    def timed(label: str, started: float) -> None:
        print(f"   ({label}: {time.perf_counter() - started:.1f}s)")

    try:
        print(f"\nSentence: {args.sentence!r}\n")

        print(f"1. Sentence check  [{settings.llm_model_moderation}]")
        t = time.perf_counter()
        verdict = await tasks.moderate_sentence(args.sentence)
        print(f"   ok={verdict.ok}" + (f"  reason: {verdict.reason}" if not verdict.ok else ""))
        timed("moderation", t)

        levels = [JargonLevel(args.level)] if args.level else list(JargonLevel)
        print(f"\n2. Jargon  [{settings.llm_model_jargon}]")
        jargon = ""
        for level in levels:
            t = time.perf_counter()
            try:
                jargon = await tasks.generate_jargon(args.sentence, level)
            except LLMError as exc:
                print(f"   {level.value:>8}: FAILED ({exc.__cause__!r})")
                continue
            leak = leaked_words(args.sentence, jargon)
            flag = (
                f"  ⚠ reuses: {', '.join(sorted(leak))}" if is_leaky(args.sentence, jargon) else ""
            )
            print(f"   {level.value:>8}: {jargon}{flag}")
            timed(level.value, t)
        if not jargon:
            print("   No jargon generated, so stopping here.")
            return

        ids = {f"guess{i}": g for i, g in enumerate(guesses, start=1)}
        print(f"\n3. Guess check  [{settings.llm_model_moderation}]")
        t = time.perf_counter()
        flagged = await tasks.moderate_guesses(ids)
        print(f"   flagged: {[ids[f] for f in flagged] or 'none'}")
        timed("moderation", t)

        print(f"\n4. Judge  [{settings.llm_model_judge}]  (against the last jargon above)")
        t = time.perf_counter()
        try:
            scores = await tasks.judge(args.sentence, jargon, ids)
        except LLMError as exc:
            print(f"   FAILED ({exc.__cause__!r})")
            return
        for gid, score in sorted(scores.items(), key=lambda kv: -kv[1]):
            print(f"   {score:>3}  {ids[gid]}")
        timed("judge", t)
        best = max(scores.values())
        bonus = best < settings.writer_bonus_threshold
        threshold = settings.writer_bonus_threshold
        print(f"   writer bonus: {'YES' if bonus else 'no'} (best {best}, threshold {threshold})")

        print(f"\n5. Quip  [{settings.llm_model_quip}]")
        top = [ids[g] for g, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:3]]
        t = time.perf_counter()
        print(f"   {await tasks.quip(args.sentence, jargon, top, bonus)}")
        timed("quip", t)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
