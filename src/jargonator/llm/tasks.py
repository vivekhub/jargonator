"""Game-specific LLM operations behind the ``LLMTasks`` protocol (spec.md §7.2–§7.5).

Failure policy (spec §7.2, §7.5, §3.11):
- moderate_sentence fails closed (the writer is asked to resubmit);
- moderate_guesses fails open (all guesses accepted);
- generate_jargon and judge raise ``LLMError`` (the engine applies the essential-LLM rule);
- quip returns ``None``.
Player ids never reach the LLM: guesses are relabelled g1, g2, ... and mapped back.
The judge gets the guesses shuffled, so neither ids nor list order reveal who guessed
first (LLMs can favour items by position).
"""

import random
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Protocol

import structlog

from jargonator.domain.state import JargonLevel
from jargonator.llm import prompts
from jargonator.llm.client import LLMError, LLMTask, OpenRouterClient
from jargonator.llm.schemas import (
    GuessModeration,
    JargonResult,
    JudgeResult,
    QuipResult,
    SentenceModeration,
)

SENTENCE_CHECK_UNAVAILABLE = "We couldn't check your sentence right now, please try again."
QUIP_MAX_WORDS = 25
_QUOTES = "\"'“”‘’"

log = structlog.get_logger()


class LLMTasks(Protocol):
    async def moderate_sentence(self, sentence: str) -> SentenceModeration: ...

    async def moderate_guesses(self, guesses: Mapping[str, str]) -> set[str]: ...

    async def generate_jargon(
        self, sentence: str, level: JargonLevel, avoid_words: Collection[str] | None = None
    ) -> str: ...

    async def judge(
        self, original: str, jargon: str, guesses: Mapping[str, str]
    ) -> dict[str, int]: ...

    async def quip(
        self, original: str, jargon: str, top_guesses: Sequence[str], writer_bonus: bool
    ) -> str | None: ...


def _relabel(guesses: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({short_id: text}, {short_id: caller_id})."""
    short = {f"g{i}": text for i, text in enumerate(guesses.values(), start=1)}
    back = {f"g{i}": gid for i, gid in enumerate(guesses, start=1)}
    return short, back


def truncate_words(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]) + "…"


class OpenRouterTasks:
    def __init__(
        self, client: OpenRouterClient, shuffle: Callable[[list[str]], None] = random.shuffle
    ) -> None:
        self._client = client
        self._shuffle = shuffle

    async def moderate_sentence(self, sentence: str) -> SentenceModeration:
        system, user = prompts.sentence_moderation_prompt(sentence)
        try:
            return await self._client.complete_json(
                LLMTask.MODERATION,
                system=system,
                user=user,
                schema=SentenceModeration,
                temperature=0,
            )
        except LLMError:
            log.warning("sentence_moderation_unavailable")
            return SentenceModeration(ok=False, reason=SENTENCE_CHECK_UNAVAILABLE)

    async def moderate_guesses(self, guesses: Mapping[str, str]) -> set[str]:
        if not guesses:
            return set()
        short, back = _relabel(guesses)
        system, user = prompts.guess_moderation_prompt(short)
        try:
            result = await self._client.complete_json(
                LLMTask.MODERATION, system=system, user=user, schema=GuessModeration, temperature=0
            )
        except LLMError:
            log.warning("guess_moderation_unavailable")
            return set()
        return {back[sid] for sid in result.flagged if sid in back}

    async def generate_jargon(
        self, sentence: str, level: JargonLevel, avoid_words: Collection[str] | None = None
    ) -> str:
        system, user = prompts.jargon_prompt(sentence, level, avoid_words)

        def not_blank(result: JargonResult) -> None:
            if not result.jargon.strip(_QUOTES + " \n\t"):
                raise ValueError("blank jargon")

        result = await self._client.complete_json(
            LLMTask.JARGON,
            system=system,
            user=user,
            schema=JargonResult,
            temperature=0.9,
            validate=not_blank,
        )
        return result.jargon.strip().strip(_QUOTES).strip()

    async def judge(self, original: str, jargon: str, guesses: Mapping[str, str]) -> dict[str, int]:
        if not guesses:
            return {}
        order = list(guesses)
        self._shuffle(order)
        short, back = _relabel({gid: guesses[gid] for gid in order})
        system, user = prompts.judge_prompt(original, jargon, short)

        def one_score_per_guess(result: JudgeResult) -> None:
            ids = [s.id for s in result.scores]
            if len(ids) != len(set(ids)) or set(ids) != set(short):
                raise ValueError(f"expected ids {sorted(short)}, got {ids}")

        result = await self._client.complete_json(
            LLMTask.JUDGE,
            system=system,
            user=user,
            schema=JudgeResult,
            temperature=0,
            validate=one_score_per_guess,
        )
        return {back[s.id]: s.score for s in result.scores}

    async def quip(
        self, original: str, jargon: str, top_guesses: Sequence[str], writer_bonus: bool
    ) -> str | None:
        system, user = prompts.quip_prompt(original, jargon, top_guesses, writer_bonus)
        try:
            result = await self._client.complete_json(
                LLMTask.QUIP, system=system, user=user, schema=QuipResult, temperature=1.0
            )
        except LLMError:
            log.warning("quip_unavailable")
            return None
        quip = result.quip.strip()
        return truncate_words(quip, QUIP_MAX_WORDS) if quip else None
