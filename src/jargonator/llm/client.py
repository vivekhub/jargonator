"""Generic JSON-completion client for OpenRouter with retries and a fallback model
(spec.md §7.1). Game-specific prompts live in ``llm/prompts.py`` and ``llm/tasks.py``.

Per call: up to 3 attempts on the task's primary model (backoff 0.5 s, then 1.5 s) for
retryable failures, then one attempt on the fallback model. A non-retryable failure
(e.g. a 4xx other than 429) skips straight to the fallback.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

import openai
import structlog
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError

from jargonator.config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRIMARY_BACKOFF_SECONDS = (0.5, 1.5)
"""Delays before primary attempts 2 and 3."""

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

log = structlog.get_logger()


class LLMTask(StrEnum):
    JARGON = "jargon"
    JUDGE = "judge"
    QUIP = "quip"
    MODERATION = "moderation"


class LLMError(Exception):
    """Every attempt (primary retries and the fallback) failed."""


class _EmptyResponseError(Exception):
    """The model returned no content."""


class _InvalidResultError(Exception):
    """The reply parsed, but the caller's ``validate`` check rejected it."""


class CompletionsAPI(Protocol):
    """The subset of ``AsyncOpenAI().chat.completions`` this client uses.

    Typed loosely because the SDK's ``create`` is heavily overloaded (streaming or not).
    We never stream, so the result is always a ``ChatCompletion``.
    """

    @property
    def create(self) -> Callable[..., Awaitable[Any]]: ...


def extract_json(content: str) -> str:
    """Strip whitespace and a Markdown code fence, which some models add despite JSON mode."""
    text = content.strip()
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _is_retryable(exc: Exception) -> bool:
    if isinstance(
        exc,
        ValidationError
        | _EmptyResponseError
        | _InvalidResultError
        | TimeoutError
        | openai.APIConnectionError,  # includes openai.APITimeoutError
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _outcome(exc: Exception) -> str:
    if isinstance(exc, ValidationError | _EmptyResponseError):
        return "parse_error"
    if isinstance(exc, _InvalidResultError):
        return "invalid_result"
    if isinstance(exc, TimeoutError | openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "connection_error"
    if isinstance(exc, openai.APIStatusError):
        return f"http_{exc.status_code}"
    return "error"


class OpenRouterClient:
    def __init__(
        self,
        completions: CompletionsAPI,
        *,
        models: Mapping[LLMTask, str],
        fallback_model: str,
        timeout: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        sdk_client: AsyncOpenAI | None = None,
    ) -> None:
        self._completions = completions
        self.models = dict(models)
        self.fallback_model = fallback_model
        self._timeout = timeout
        self._sleep = sleep
        self.sdk_client = sdk_client
        """The underlying SDK client, kept so it can be closed on shutdown."""

    async def complete_json[T: BaseModel](
        self,
        task: LLMTask,
        *,
        system: str,
        user: str,
        schema: type[T],
        temperature: float,
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """Return the model's JSON reply parsed into ``schema``, or raise ``LLMError``.

        ``validate`` runs per-call checks that depend on the input (e.g. "one score per
        guess id"). If it raises, the attempt counts as failed and is retried.
        """
        primary = self.models[task]
        plan = [(primary, 0.0), *((primary, delay) for delay in PRIMARY_BACKOFF_SECONDS)]
        attempt = 0
        for model, delay in plan:
            attempt += 1
            if delay:
                await self._sleep(delay)
            try:
                return await self._attempt(
                    task, model, attempt, system, user, schema, temperature, validate
                )
            except Exception as exc:
                if not _is_retryable(exc):
                    break
        try:
            return await self._attempt(
                task, self.fallback_model, attempt + 1, system, user, schema, temperature, validate
            )
        except Exception as exc:
            # Each failed attempt was already logged. Chain the final (fallback) failure.
            raise LLMError(f"{task} failed on {primary} and {self.fallback_model}") from exc

    async def aclose(self) -> None:
        if self.sdk_client is not None:
            await self.sdk_client.close()

    async def _attempt[T: BaseModel](
        self,
        task: LLMTask,
        model: str,
        attempt: int,
        system: str,
        user: str,
        schema: type[T],
        temperature: float,
        validate: Callable[[T], None] | None,
    ) -> T:
        started = time.perf_counter()
        usage: dict[str, int | None] = {"prompt_tokens": None, "completion_tokens": None}
        try:
            # The SDK timeout is per read/write step, so a server trickling bytes could
            # hold a call open indefinitely. asyncio.timeout bounds the whole attempt.
            async with asyncio.timeout(self._timeout):
                response: ChatCompletion = await self._completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    timeout=self._timeout,
                )
            if response.usage is not None:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                }
            content = response.choices[0].message.content if response.choices else None
            log.debug("llm_io", task=task, model=model, system=system, user=user, response=content)
            if not content:
                raise _EmptyResponseError("Empty response")
            result = schema.model_validate_json(extract_json(content))
            if validate is not None:
                try:
                    validate(result)
                except Exception as exc:
                    raise _InvalidResultError(str(exc)) from exc
        except Exception as exc:
            self._log(task, model, attempt, started, usage, _outcome(exc))
            raise
        self._log(task, model, attempt, started, usage, "ok")
        return result

    @staticmethod
    def _log(
        task: LLMTask,
        model: str,
        attempt: int,
        started: float,
        usage: Mapping[str, int | None],
        outcome: str,
    ) -> None:
        # Metadata only: prompt and response content are logged at DEBUG in _attempt.
        log.info(
            "llm_call",
            task=str(task),
            model=model,
            attempt=attempt,
            latency_ms=round((time.perf_counter() - started) * 1000),
            outcome=outcome,
            **usage,
        )


def build_openrouter_client(settings: Settings) -> OpenRouterClient:
    sdk = AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=settings.openrouter_api_key.get_secret_value(),
        max_retries=0,  # OpenRouterClient owns retries; SDK retries would multiply them
        default_headers={"HTTP-Referer": "https://github.com/jargonator", "X-Title": "Jargonator"},
    )
    return OpenRouterClient(
        sdk.chat.completions,
        models={
            LLMTask.JARGON: settings.llm_model_jargon,
            LLMTask.JUDGE: settings.llm_model_judge,
            LLMTask.QUIP: settings.llm_model_quip,
            LLMTask.MODERATION: settings.llm_model_moderation,
        },
        fallback_model=settings.llm_model_fallback,
        timeout=settings.llm_timeout_seconds,
        sdk_client=sdk,
    )
