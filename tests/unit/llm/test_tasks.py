import random
from collections.abc import Callable

import pytest

from jargonator.domain.state import JargonLevel
from jargonator.llm.client import LLMError, LLMTask, OpenRouterClient
from jargonator.llm.tasks import SENTENCE_CHECK_UNAVAILABLE, LLMTasks, OpenRouterTasks
from tests.fakes.llm import FakeLLM
from tests.fakes.openai_stub import StubCompletions, completion, json_completion, status_error

MODELS = {task: f"primary/{task}" for task in LLMTask}


async def no_sleep(_: float) -> None:
    return None


def keep_order(_: list[str]) -> None:
    return None


def make(
    *script: object, shuffle: Callable[[list[str]], None] = keep_order
) -> tuple[OpenRouterTasks, StubCompletions]:
    stub = StubCompletions(*script)  # type: ignore[arg-type]
    client = OpenRouterClient(
        stub, models=MODELS, fallback_model="fallback/model", timeout=5, sleep=no_sleep
    )
    return OpenRouterTasks(client, shuffle=shuffle), stub


def failures(n: int = 4) -> list[object]:
    return [status_error(503) for _ in range(n)]


# --- moderation -------------------------------------------------------------------------


async def test_moderate_sentence_ok() -> None:
    tasks, stub = make(json_completion({"ok": True, "reason": ""}))
    result = await tasks.moderate_sentence("I have two cats")
    assert result.ok is True
    assert stub.models == ["primary/moderation"]
    assert stub.calls[0]["temperature"] == 0


async def test_moderate_sentence_rejected_reason() -> None:
    tasks, _ = make(json_completion({"ok": False, "reason": "Please keep it about you."}))
    result = await tasks.moderate_sentence("Bob is lazy")
    assert (result.ok, result.reason) == (False, "Please keep it about you.")


async def test_moderate_sentence_fails_closed() -> None:
    tasks, _ = make(*failures())
    result = await tasks.moderate_sentence("I have two cats")
    assert (result.ok, result.reason) == (False, SENTENCE_CHECK_UNAVAILABLE)


async def test_moderate_guesses_maps_short_ids_back() -> None:
    tasks, stub = make(json_completion({"flagged": ["g2", "g9"]}))
    flagged = await tasks.moderate_guesses({"uuid-a": "cats", "uuid-b": "rude words"})
    assert flagged == {"uuid-b"}  # g9 is unknown: ignored
    assert "uuid-a" not in stub.user() and '<guess id="g1">cats</guess>' in stub.user()


async def test_moderate_guesses_fails_open() -> None:
    tasks, _ = make(*failures())
    assert await tasks.moderate_guesses({"a": "x"}) == set()


async def test_moderate_no_guesses_skips_llm() -> None:
    tasks, stub = make()
    assert await tasks.moderate_guesses({}) == set()
    assert stub.calls == []


# --- jargon -------------------------------------------------------------------------------


async def test_generate_jargon_strips_quotes() -> None:
    tasks, stub = make(json_completion({"jargon": '  "I steward a feline portfolio."  '}))
    jargon = await tasks.generate_jargon("I have two cats", JargonLevel.SPICY)
    assert jargon == "I steward a feline portfolio."
    assert stub.calls[0]["temperature"] == 0.9
    assert stub.models == ["primary/jargon"]


async def test_generate_jargon_passes_avoid_words() -> None:
    tasks, stub = make(json_completion({"jargon": "x"}))
    await tasks.generate_jargon("I have two cats", JargonLevel.MILD, avoid_words={"cats"})
    assert "reused these words: cats" in stub.user()


async def test_generate_jargon_raises_after_all_attempts() -> None:
    tasks, _ = make(*failures())
    with pytest.raises(LLMError):
        await tasks.generate_jargon("I have two cats", JargonLevel.MILD)


async def test_generate_jargon_rejects_blank() -> None:
    tasks, _ = make(*(json_completion({"jargon": "  "}) for _ in range(4)))
    with pytest.raises(LLMError):
        await tasks.generate_jargon("I have two cats", JargonLevel.MILD)


# --- judge --------------------------------------------------------------------------------

GUESSES = {"uuid-a": "I own kitties", "uuid-b": "I have two dogs"}


async def test_judge_maps_scores_back_to_caller_ids() -> None:
    tasks, stub = make(
        json_completion({"scores": [{"id": "g1", "score": 95}, {"id": "g2", "score": 20}]})
    )
    assert await tasks.judge("I have two cats", "feline portfolio", GUESSES) == {
        "uuid-a": 95,
        "uuid-b": 20,
    }
    assert stub.models == ["primary/judge"]
    assert stub.calls[0]["temperature"] == 0
    assert "90–100" in stub.system()


@pytest.mark.parametrize(
    "bad",
    [
        {"scores": [{"id": "g1", "score": 95}]},  # missing g2
        {"scores": [{"id": "g1", "score": 95}, {"id": "g2", "score": 5}, {"id": "g3", "score": 1}]},
        {"scores": [{"id": "g1", "score": 95}, {"id": "g1", "score": 5}]},  # duplicate
        {"scores": [{"id": "g1", "score": 101}, {"id": "g2", "score": 5}]},
        {"scores": [{"id": "g1", "score": -1}, {"id": "g2", "score": 5}]},
        {"scores": [{"id": "g1", "score": "85"}, {"id": "g2", "score": 5}]},
        {"scores": [{"id": "g1", "score": 85.0}, {"id": "g2", "score": 5}]},
        {"scores": [{"id": "g1", "score": True}, {"id": "g2", "score": 5}]},
    ],
    ids=["missing", "extra", "duplicate", "over", "under", "string", "float", "bool"],
)
async def test_invalid_judge_output_is_retried(bad: dict[str, object]) -> None:
    good = json_completion({"scores": [{"id": "g1", "score": 90}, {"id": "g2", "score": 10}]})
    tasks, stub = make(json_completion(bad), good)
    assert await tasks.judge("s", "j", GUESSES) == {"uuid-a": 90, "uuid-b": 10}
    assert len(stub.calls) == 2


async def test_judge_raises_when_always_invalid() -> None:
    bad = json_completion({"scores": [{"id": "g1", "score": 90}]})
    tasks, _ = make(bad, bad, bad, bad)
    with pytest.raises(LLMError):
        await tasks.judge("s", "j", GUESSES)


async def test_judge_sees_guesses_in_shuffled_order() -> None:
    """The judge must not learn the submission order from the ids or the list order."""
    tasks, stub = make(
        json_completion({"scores": [{"id": "g1", "score": 20}, {"id": "g2", "score": 95}]}),
        shuffle=lambda ids: ids.reverse(),
    )
    scores = await tasks.judge("I have two cats", "feline portfolio", GUESSES)
    first, second = GUESSES  # submission order
    assert scores == {second: 20, first: 95}
    user = stub.user()
    assert user.index(f'"g1">{GUESSES[second]}') < user.index(f'"g2">{GUESSES[first]}')


def test_judge_shuffles_randomly_by_default() -> None:
    client = OpenRouterClient(StubCompletions(), models=MODELS, fallback_model="f", timeout=5)
    assert OpenRouterTasks(client)._shuffle == random.shuffle


async def test_judge_empty_input_skips_llm() -> None:
    tasks, stub = make()
    assert await tasks.judge("s", "j", {}) == {}
    assert stub.calls == []


# --- quip ---------------------------------------------------------------------------------


async def test_quip_truncated_to_25_words() -> None:
    long = " ".join(f"w{i}" for i in range(40))
    tasks, stub = make(json_completion({"quip": long}))
    quip = await tasks.quip("s", "j", ["a"], writer_bonus=True)
    assert quip is not None and len(quip.split()) == 25 and quip.endswith("…")
    assert stub.calls[0]["temperature"] == 1.0


async def test_quip_none_on_failure() -> None:
    tasks, _ = make(*failures())
    assert await tasks.quip("s", "j", [], writer_bonus=False) is None


async def test_quip_blank_is_none() -> None:
    tasks, _ = make(json_completion({"quip": "   "}))
    assert await tasks.quip("s", "j", [], writer_bonus=False) is None


async def test_non_json_quip_counts_as_failure() -> None:
    tasks, _ = make(*(completion("haha") for _ in range(4)))
    assert await tasks.quip("s", "j", [], writer_bonus=False) is None


# --- FakeLLM ------------------------------------------------------------------------------


async def test_fake_llm_defaults_and_call_log() -> None:
    fake = FakeLLM()
    assert (await fake.moderate_sentence("x")).ok
    assert await fake.moderate_guesses({"a": "x"}) == set()
    assert await fake.generate_jargon("I have two cats", JargonLevel.MILD)
    assert await fake.judge("I have two cats", "j", {"a": "I have two cats", "b": "zzz"}) == {
        "a": 100,
        "b": 0,
    }
    assert await fake.quip("s", "j", [], writer_bonus=False) == "Synergy achieved."
    assert [c[0] for c in fake.calls] == [
        "moderate_sentence",
        "moderate_guesses",
        "generate_jargon",
        "judge",
        "quip",
    ]


async def test_fake_llm_scripting_and_errors() -> None:
    fake = FakeLLM(
        judge_scores={"kitties": 95},
        flagged_texts={"rude"},
        jargon=["first", "second"],
    )
    fake.fail("judge", LLMError("down"), times=1)
    with pytest.raises(LLMError):
        await fake.judge("s", "j", {"a": "kitties"})
    assert await fake.judge("s", "j", {"a": "kitties"}) == {"a": 95}
    assert await fake.moderate_guesses({"a": "rude", "b": "fine"}) == {"a"}
    assert await fake.generate_jargon("s", JargonLevel.MILD) == "first"
    assert await fake.generate_jargon("s", JargonLevel.MILD) == "second"


def test_fake_llm_satisfies_protocol() -> None:
    tasks: LLMTasks = FakeLLM()  # also checked by mypy (tests/fakes is type-checked)
    assert tasks is not None
