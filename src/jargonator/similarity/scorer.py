"""Similarity scoring behind a protocol, so tests can use a deterministic fake (spec.md §8).

Calibration note: the default writer-bonus threshold (0.5) assumes all-MiniLM-L6-v2, where
paraphrases score about 0.7-0.9, related sentences 0.4-0.6 and unrelated ones below 0.2.
Re-tune WRITER_BONUS_THRESHOLD if EMBEDDING_MODEL changes.
"""

import asyncio
import importlib
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

_WHITESPACE = re.compile(r"\s+")


class ScorerNotReadyError(RuntimeError):
    """``score`` was called before the model finished loading."""


class SimilarityScorer(Protocol):
    async def score(self, original: str, guesses: Sequence[str]) -> list[float]:
        """Return one similarity in [0, 1] per guess, in the same order."""
        ...

    def is_ready(self) -> bool: ...

    async def load(self) -> None: ...


class EncoderModel(Protocol):
    def encode(self, sentences: list[str], *, normalize_embeddings: bool) -> Any: ...


def normalize_text(text: str) -> str:
    """Trim and collapse whitespace. Case and punctuation are kept (the model uses them)."""
    return _WHITESPACE.sub(" ", text).strip()


def _load_sentence_transformer(model_name: str) -> EncoderModel:
    # Imported lazily: sentence-transformers (and torch) is an optional extra.
    module: Any = importlib.import_module("sentence_transformers")
    model: EncoderModel = module.SentenceTransformer(model_name)
    return model


class SentenceTransformerScorer:
    """Cosine similarity over normalised sentence embeddings, computed off the event loop."""

    def __init__(
        self,
        model_name: str,
        loader: Callable[[str], EncoderModel] = _load_sentence_transformer,
    ) -> None:
        self._model_name = model_name
        self._loader = loader
        self._model: EncoderModel | None = None
        # One worker: the model is not guaranteed to be thread-safe, and calls are cheap.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embeddings")

    def is_ready(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        if self._model is None:
            loop = asyncio.get_running_loop()
            self._model = await loop.run_in_executor(self._executor, self._loader, self._model_name)

    async def score(self, original: str, guesses: Sequence[str]) -> list[float]:
        if not guesses:
            return []
        model = self._model
        if model is None:
            raise ScorerNotReadyError("Embedding model is not loaded yet")
        texts = [normalize_text(original), *(normalize_text(g) for g in guesses)]
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(self._executor, self._encode, model, texts)
        reference, *rest = vectors
        return [round(max(0.0, _dot(reference, v)), 4) for v in rest]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _encode(model: EncoderModel, texts: list[str]) -> list[list[float]]:
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in vector] for vector in embeddings]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
