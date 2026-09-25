"""Scriptable LLMTasks for engine tests (no network)."""

import asyncio
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from jargonator.domain.state import JargonLevel
from jargonator.llm.schemas import SentenceModeration

if TYPE_CHECKING:
    from jargonator.llm.tasks import LLMTasks

_TOKEN = re.compile(r"[a-z0-9']+")


def overlap_score(a: str, b: str) -> int:
    """Word-overlap stand-in for the judge: 100 = same words, 0 = none shared."""
    ta, tb = set(_TOKEN.findall(a.lower())), set(_TOKEN.findall(b.lower()))
    return round(100 * len(ta & tb) / len(ta | tb)) if ta | tb else 0


class FakeLLM:
    """Canned answers, optional scripted failures, and a call log.

    - moderation: ``rejection`` (a reason string) rejects every sentence; ``flagged_texts``
      flags guesses by exact text.
    - jargon: ``jargon`` is a list consumed in order (the last one repeats).
    - judge: ``judge_scores`` maps guess text → score, otherwise ``overlap_score``.
    - ``fail(method, exc, times)`` raises ``exc`` on the next ``times`` calls to ``method``.
    """

    def __init__(
        self,
        *,
        rejection: str | None = None,
        flagged_texts: Iterable[str] = (),
        jargon: Sequence[str] = ("I leverage cross-functional personal-life synergies.",),
        judge_scores: Mapping[str, int] | None = None,
        quip: str | None = "Synergy achieved.",
    ) -> None:
        self.rejection = rejection
        self.flagged_texts = set(flagged_texts)
        self.jargon = list(jargon)
        self.judge_scores = dict(judge_scores or {})
        self.quip_text = quip
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._failures: dict[str, list[Exception]] = {}
        self.gates: dict[str, asyncio.Event] = {}
        """``gates[method]``: calls to that method wait until the event is set."""
        self.entered: dict[str, asyncio.Event] = {}
        """``entered[method]`` is set as soon as that method is called."""

    def hold(self, method: str) -> tuple[asyncio.Event, asyncio.Event]:
        """Make ``method`` block until released. Returns (entered, release)."""
        self.gates[method] = asyncio.Event()
        self.entered[method] = asyncio.Event()
        return self.entered[method], self.gates[method]

    async def _gate(self, method: str) -> None:
        if method in self.entered:
            self.entered[method].set()
        if method in self.gates:
            await self.gates[method].wait()

    def fail(self, method: str, exc: Exception, times: int = 1) -> None:
        self._failures.setdefault(method, []).extend([exc] * times)

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        pending = self._failures.get(method)
        if pending:
            raise pending.pop(0)

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]

    async def moderate_sentence(self, sentence: str) -> SentenceModeration:
        self._record("moderate_sentence", sentence=sentence)
        if self.rejection is not None:
            return SentenceModeration(ok=False, reason=self.rejection)
        return SentenceModeration(ok=True)

    async def moderate_guesses(self, guesses: Mapping[str, str]) -> set[str]:
        self._record("moderate_guesses", guesses=dict(guesses))
        return {gid for gid, text in guesses.items() if text in self.flagged_texts}

    async def generate_jargon(
        self, sentence: str, level: JargonLevel, avoid_words: Collection[str] | None = None
    ) -> str:
        self._record(
            "generate_jargon",
            sentence=sentence,
            level=level,
            avoid_words=set(avoid_words) if avoid_words else None,
        )
        await self._gate("generate_jargon")
        return self.jargon.pop(0) if len(self.jargon) > 1 else self.jargon[0]

    async def judge(self, original: str, jargon: str, guesses: Mapping[str, str]) -> dict[str, int]:
        self._record("judge", original=original, jargon=jargon, guesses=dict(guesses))
        await self._gate("judge")
        return {
            gid: self.judge_scores.get(text, overlap_score(original, text))
            for gid, text in guesses.items()
        }

    async def quip(
        self, original: str, jargon: str, top_guesses: Sequence[str], writer_bonus: bool
    ) -> str | None:
        self._record("quip", top_guesses=list(top_guesses), writer_bonus=writer_bonus)
        return self.quip_text


if TYPE_CHECKING:
    _protocol_check: LLMTasks = FakeLLM()
