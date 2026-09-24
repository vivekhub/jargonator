"""Deterministic SimilarityScorer for engine tests."""

import re
from collections.abc import Mapping, Sequence

_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 0.0
    return round(len(ta & tb) / len(ta | tb), 4)


class FakeScorer:
    """Returns scripted similarities by guess text, otherwise a token-Jaccard score."""

    def __init__(
        self, scripted: Mapping[str, float] | None = None, error: Exception | None = None
    ) -> None:
        self.scripted = dict(scripted or {})
        self.error = error
        self.ready = False
        self.calls: list[tuple[str, list[str]]] = []

    async def load(self) -> None:
        self.ready = True

    def is_ready(self) -> bool:
        return self.ready

    async def score(self, original: str, guesses: Sequence[str]) -> list[float]:
        self.calls.append((original, list(guesses)))
        if self.error is not None:
            raise self.error
        return [self.scripted.get(g, jaccard(original, g)) for g in guesses]
