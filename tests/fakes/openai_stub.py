"""Scriptable stand-in for ``AsyncOpenAI().chat.completions`` (no network)."""

import json
from typing import Any

import httpx2
import openai
from openai.types.chat import ChatCompletion

REQUEST = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


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


def json_completion(payload: object) -> ChatCompletion:
    return completion(json.dumps(payload))


def status_error(code: int) -> openai.APIStatusError:
    classes: dict[int, type[openai.APIStatusError]] = {
        400: openai.BadRequestError,
        429: openai.RateLimitError,
        503: openai.InternalServerError,
    }
    return classes.get(code, openai.APIStatusError)(
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

    def system(self, index: int = 0) -> str:
        return str(self.calls[index]["messages"][0]["content"])

    def user(self, index: int = 0) -> str:
        return str(self.calls[index]["messages"][1]["content"])
