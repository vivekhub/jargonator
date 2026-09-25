import asyncio
import json
import time
from typing import Any

import httpx2
import openai
import pytest
import structlog
from openai.types.chat import ChatCompletion
from pydantic import BaseModel

from jargonator.config import Settings
from jargonator.llm.client import (
    LLMError,
    LLMTask,
    OpenRouterClient,
    build_openrouter_client,
    extract_json,
)
from jargonator.logging import configure_logging

REQUEST = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
MODELS = {
    LLMTask.JARGON: "primary/jargon",
    LLMTask.JUDGE: "primary/judge",
    LLMTask.QUIP: "primary/quip",
    LLMTask.MODERATION: "primary/moderation",
}


class Answer(BaseModel):
    answer: str


def completion(content: str | None) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    )


def ok(answer: str = "hi") -> ChatCompletion:
    return completion(json.dumps({"answer": answer}))


def status_error(code: int) -> openai.APIStatusError:
    cls = {400: openai.BadRequestError, 429: openai.RateLimitError, 503: openai.InternalServerError}
    return cls.get(code, openai.APIStatusError)(
        f"status {code}", response=httpx2.Response(code, request=REQUEST), body=None
    )


class StubCompletions:
    """Returns (or raises) scripted items in order and records each call's kwargs."""

    def __init__(self, *script: ChatCompletion | Exception) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]


class Sleeps:
    def __init__(self) -> None:
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


def make_client(stub: StubCompletions) -> tuple[OpenRouterClient, Sleeps]:
    sleeps = Sleeps()
    client = OpenRouterClient(
        stub, models=MODELS, fallback_model="fallback/model", timeout=15, sleep=sleeps
    )
    return client, sleeps


async def call(client: OpenRouterClient, task: LLMTask = LLMTask.JARGON) -> Answer:
    return await client.complete_json(
        task, system="You are a test.", user="SECRET user text", schema=Answer, temperature=0.3
    )


async def test_success_first_try() -> None:
    stub = StubCompletions(ok("hello"))
    client, sleeps = make_client(stub)
    assert (await call(client)).answer == "hello"
    assert sleeps.durations == []
    kwargs = stub.calls[0]
    assert kwargs["model"] == "primary/jargon"
    assert kwargs["messages"] == [
        {"role": "system", "content": "You are a test."},
        {"role": "user", "content": "SECRET user text"},
    ]
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["temperature"] == 0.3
    assert kwargs["timeout"] == 15


async def test_task_selects_model() -> None:
    stub = StubCompletions(ok())
    client, _ = make_client(stub)
    await call(client, LLMTask.JUDGE)
    assert stub.models == ["primary/judge"]


async def test_bad_json_then_good_retries_once() -> None:
    stub = StubCompletions(completion("not json"), ok("fixed"))
    client, sleeps = make_client(stub)
    assert (await call(client)).answer == "fixed"
    assert sleeps.durations == [0.5]
    assert stub.models == ["primary/jargon", "primary/jargon"]


@pytest.mark.parametrize(
    "failure",
    [
        completion('{"wrong": "shape"}'),
        completion(None),
        openai.APITimeoutError(request=REQUEST),
        openai.APIConnectionError(request=REQUEST),
        status_error(429),
        status_error(503),
    ],
    ids=["schema", "empty", "timeout", "connection", "429", "503"],
)
async def test_retryable_failures_are_retried(failure: ChatCompletion | Exception) -> None:
    stub = StubCompletions(failure, ok())
    client, sleeps = make_client(stub)
    await call(client)
    assert sleeps.durations == [0.5]


async def test_three_primary_failures_then_fallback_succeeds() -> None:
    stub = StubCompletions(status_error(503), status_error(503), status_error(503), ok("fb"))
    client, sleeps = make_client(stub)
    assert (await call(client)).answer == "fb"
    assert stub.models == ["primary/jargon"] * 3 + ["fallback/model"]
    assert sleeps.durations == [0.5, 1.5]


async def test_all_fail_raises_llm_error_with_cause() -> None:
    last = status_error(503)
    stub = StubCompletions(status_error(503), status_error(503), status_error(503), last)
    client, _ = make_client(stub)
    with pytest.raises(LLMError) as info:
        await call(client)
    assert info.value.__cause__ is last


async def test_non_retryable_4xx_skips_to_fallback() -> None:
    stub = StubCompletions(status_error(400), ok("fb"))
    client, sleeps = make_client(stub)
    assert (await call(client)).answer == "fb"
    assert stub.models == ["primary/jargon", "fallback/model"]
    assert sleeps.durations == []


async def test_unexpected_exception_skips_to_fallback() -> None:
    stub = StubCompletions(RuntimeError("weird"), ok("fb"))
    client, _ = make_client(stub)
    assert (await call(client)).answer == "fb"


async def test_fallback_failure_is_not_retried() -> None:
    stub = StubCompletions(status_error(400), status_error(503))
    client, _ = make_client(stub)
    with pytest.raises(LLMError):
        await call(client)
    assert len(stub.calls) == 2


class HangingCompletions:
    """Never answers, like a server trickling keep-alive bytes (review finding 2)."""

    def __init__(self, then: ChatCompletion | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.then = then

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if self.then is not None and kwargs["model"] == "fallback/model":
            return self.then
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


async def test_total_timeout_bounds_each_attempt() -> None:
    stub = HangingCompletions(then=ok("fb"))
    sleeps = Sleeps()
    client = OpenRouterClient(
        stub, models=MODELS, fallback_model="fallback/model", timeout=0.05, sleep=sleeps
    )
    started = time.perf_counter()
    assert (await call(client)).answer == "fb"
    assert time.perf_counter() - started < 2
    assert [c["model"] for c in stub.calls] == ["primary/jargon"] * 3 + ["fallback/model"]
    assert sleeps.durations == [0.5, 1.5]  # timeouts are retryable


async def test_validate_callback_failure_is_retried() -> None:
    """Review finding 5: per-call semantic checks must count as failed attempts."""
    stub = StubCompletions(ok("wrong"), ok("right"))
    client, sleeps = make_client(stub)

    def must_be_right(result: Answer) -> None:
        if result.answer != "right":
            raise ValueError("not right")

    result = await client.complete_json(
        LLMTask.JUDGE, system="s", user="u", schema=Answer, temperature=0, validate=must_be_right
    )
    assert result.answer == "right"
    assert sleeps.durations == [0.5]


async def test_validate_failure_everywhere_raises_llm_error() -> None:
    stub = StubCompletions(*(ok("wrong") for _ in range(4)))
    client, _ = make_client(stub)

    def never(_: Answer) -> None:
        raise ValueError("no")

    with pytest.raises(LLMError):
        await client.complete_json(
            LLMTask.JUDGE, system="s", user="u", schema=Answer, temperature=0, validate=never
        )
    assert len(stub.calls) == 4


async def test_code_fenced_json_is_accepted() -> None:
    stub = StubCompletions(completion('```json\n{"answer": "fenced"}\n```'))
    client, sleeps = make_client(stub)
    assert (await call(client)).answer == "fenced"
    assert sleeps.durations == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('  ```json\n{"a": 1}\n```  ', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
    ],
)
def test_extract_json(raw: str, expected: str) -> None:
    assert extract_json(raw) == expected


async def test_logs_metadata_but_never_prompt_content(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    stub = StubCompletions(completion("not json"), ok("SECRET answer"))
    client, _ = make_client(stub)
    await call(client)
    out = capsys.readouterr().out
    structlog.reset_defaults()
    records = [json.loads(line) for line in out.splitlines() if line.strip()]
    calls = [r for r in records if r["event"] == "llm_call"]
    assert [c["outcome"] for c in calls] == ["parse_error", "ok"]
    assert calls[-1]["task"] == "jargon"
    assert calls[-1]["model"] == "primary/jargon"
    assert calls[-1]["attempt"] == 2
    assert calls[-1]["prompt_tokens"] == 11 and calls[-1]["completion_tokens"] == 7
    assert "latency_ms" in calls[-1]
    assert "SECRET" not in out


async def test_debug_logs_include_content(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG")
    client, _ = make_client(StubCompletions(ok("SECRET answer")))
    await call(client)
    out = capsys.readouterr().out
    structlog.reset_defaults()
    assert "SECRET user text" in out


def test_build_openrouter_client(required_env: dict[str, str]) -> None:
    client = build_openrouter_client(Settings(_env_file=None))  # type: ignore[call-arg]
    raw = client.sdk_client
    assert raw is not None
    assert str(raw.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert raw.max_retries == 0  # our client owns retries
    assert raw.default_headers["X-Title"] == "Jargonator"
    assert client.models[LLMTask.JUDGE] == "anthropic/claude-sonnet-5"
    assert client.models[LLMTask.MODERATION] == "anthropic/claude-haiku-4.5"
    assert client.fallback_model == "openai/gpt-4o-mini"
