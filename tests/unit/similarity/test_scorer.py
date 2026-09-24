import math
import threading
from collections.abc import Sequence
from typing import ClassVar

import pytest

from jargonator.similarity.scorer import (
    ScorerNotReadyError,
    SentenceTransformerScorer,
    SimilarityScorer,
    normalize_text,
)
from tests.fakes.scorer import FakeScorer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  I have   two cats ", "I have two cats"),
        ("line\nbreak\ttab", "line break tab"),
        ("Keep Case, and punctuation!", "Keep Case, and punctuation!"),
        ("   ", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


# --- SentenceTransformerScorer with a stand-in model ---------------------------------


def unit(*v: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


class StubModel:
    """Maps known sentences to fixed (already normalised) vectors."""

    VECTORS: ClassVar[dict[str, list[float]]] = {
        "I have two cats": unit(1, 0, 0),
        "I own a pair of kitties": unit(0.9, 0.1, 0),
        "I like skiing": unit(0, 1, 0),
        "opposite": unit(-1, 0, 0),
    }

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []
        self.threads: set[str] = set()

    def encode(self, sentences: list[str], *, normalize_embeddings: bool) -> list[list[float]]:
        self.calls.append((sentences, normalize_embeddings))
        self.threads.add(threading.current_thread().name)
        return [self.VECTORS[s] for s in sentences]


def make_scorer() -> tuple[SentenceTransformerScorer, StubModel]:
    model = StubModel()
    loaded: list[str] = []

    def loader(name: str) -> StubModel:
        loaded.append(name)
        return model

    return SentenceTransformerScorer("stub-model", loader=loader), model


async def test_not_ready_before_load() -> None:
    scorer, _ = make_scorer()
    assert scorer.is_ready() is False
    with pytest.raises(ScorerNotReadyError):
        await scorer.score("I have two cats", ["I like skiing"])


async def test_empty_guesses_skip_the_model() -> None:
    scorer, model = make_scorer()
    assert await scorer.score("I have two cats", []) == []  # works even before load()
    assert model.calls == []


async def test_scores_are_clamped_rounded_and_ordered() -> None:
    scorer, model = make_scorer()
    await scorer.load()
    assert scorer.is_ready()
    scores = await scorer.score(
        "  I have two   cats ", ["I own a pair of kitties", "I like skiing", "opposite"]
    )
    assert scores == [round(0.9 / math.sqrt(0.82), 4), 0.0, 0.0]
    # One batched call, text normalised, normalize_embeddings requested.
    assert model.calls == [
        (["I have two cats", "I own a pair of kitties", "I like skiing", "opposite"], True)
    ]


async def test_encoding_runs_off_the_event_loop_thread() -> None:
    scorer, model = make_scorer()
    await scorer.load()
    await scorer.score("I have two cats", ["I like skiing"])
    assert threading.current_thread().name not in model.threads
    scorer.close()


async def test_load_is_idempotent() -> None:
    calls: list[str] = []

    def loader(name: str) -> StubModel:
        calls.append(name)
        return StubModel()

    scorer = SentenceTransformerScorer("stub-model", loader=loader)
    await scorer.load()
    await scorer.load()
    assert calls == ["stub-model"]


# --- FakeScorer ------------------------------------------------------------------------


async def test_fake_scorer_scripted_and_jaccard_default() -> None:
    fake = FakeScorer(scripted={"exact match": 0.95})
    await fake.load()
    scores = await fake.score("I have two cats", ["exact match", "i have two cats", "dogs"])
    assert scores[0] == 0.95
    assert scores[1] == 1.0  # same tokens, case-insensitive
    assert scores[2] == 0.0
    assert fake.calls == [("I have two cats", ["exact match", "i have two cats", "dogs"])]


async def test_fake_scorer_partial_overlap() -> None:
    # {i, have, two, cats} vs {i, have, cats}: 3 shared / 4 total
    assert await FakeScorer().score("I have two cats", ["I have cats"]) == [0.75]


async def test_fake_scorer_error_injection() -> None:
    fake = FakeScorer(error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await fake.score("x", ["y"])


def test_both_satisfy_protocol() -> None:
    scorers: Sequence[SimilarityScorer] = [make_scorer()[0], FakeScorer()]
    assert len(scorers) == 2


# --- the real model (opt-in: `make test-slow`) -------------------------------------------


@pytest.mark.slow
async def test_real_model_ranks_paraphrase_above_unrelated() -> None:
    pytest.importorskip("sentence_transformers")
    scorer = SentenceTransformerScorer("sentence-transformers/all-MiniLM-L6-v2")
    await scorer.load()
    try:
        paraphrase, unrelated = await scorer.score(
            "I have two cats", ["I own a pair of kitties", "I like skiing in winter"]
        )
    finally:
        scorer.close()
    assert paraphrase > unrelated
    assert all(0.0 <= s <= 1.0 for s in (paraphrase, unrelated))
