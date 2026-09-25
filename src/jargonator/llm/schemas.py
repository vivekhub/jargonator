"""Pydantic models for every structured LLM reply (spec.md §7.2–§7.5)."""

from typing import Annotated

from pydantic import BaseModel, Field


class SentenceModeration(BaseModel):
    ok: bool
    reason: str = ""


class GuessModeration(BaseModel):
    flagged: list[str] = Field(default_factory=list)


class JargonResult(BaseModel):
    jargon: Annotated[str, Field(max_length=600)]


class JudgeScore(BaseModel):
    id: str
    # strict: "85", 85.0 and true must be rejected, not coerced (spec §7.4)
    score: Annotated[int, Field(ge=0, le=100, strict=True)]


class JudgeResult(BaseModel):
    scores: list[JudgeScore]


class QuipResult(BaseModel):
    quip: Annotated[str, Field(max_length=500)]
