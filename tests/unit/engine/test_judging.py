from datetime import timedelta

from jargonator.domain.state import GameState, RoundStatus
from jargonator.engine.scheduler import TimerKind
from jargonator.llm.client import LLMError
from jargonator.slack import ids
from jargonator.slack.gateway import MessageRef
from tests.engine_harness import Harness

USERS = ("U1", "U2", "U3", "U4", "U5")


async def play_round(
    harness: Harness, guesses: dict[int, str], users: tuple[str, ...] = USERS
) -> list[str]:
    """Start, write, and have guessers[i] submit guesses[i] (in index order). Returns guessers."""
    await harness.started(*users)
    await harness.write("I have two cats")
    guessers = sorted(await harness.guessers())
    rnd = await harness.round()
    for index in sorted(guesses):
        harness.clock.advance(1)
        await harness.engine.submit_guess(guessers[index], rnd.id, guesses[index])
    if (await harness.round()).status is RoundStatus.GUESSING:
        await harness.engine.on_guess_deadline((await harness.game()).id, rnd.id)
    return guessers


async def results_message(harness: Harness) -> str:
    rnd = await harness.round()
    assert rnd.results_message_ts is not None
    return str(harness.slack.current(MessageRef("C1", rnd.results_message_ts)).blocks)


async def test_points_persisted_and_shown(harness: Harness) -> None:
    harness.llm.judge_scores = {"kitties": 95, "a cat": 80, "pets": 55, "trains": 5}
    guessers = await play_round(harness, {0: "trains", 1: "pets", 2: "a cat", 3: "kitties"})
    scores = {p.user_id: p.score for p in await harness.players()}
    assert scores[guessers[3]] == 10 and scores[guessers[2]] == 5 and scores[guessers[1]] == 1
    assert scores[guessers[0]] == 0
    assert (await harness.player(guessers[3])).round_wins == 1

    rnd = await harness.round()
    assert rnd.status is RoundStatus.COMPLETED and rnd.writer_bonus_awarded is False
    assert (await harness.game()).state is GameState.AWAITING_NEXT
    shown = await results_message(harness)
    assert "95/100" in shown and "I have two cats" in shown and "Leaderboard" in shown
    # "kitties" won but was the 4th guess in; "trains" came in 1st.
    assert "95/100 · 📥 4th in" in shown and "5/100 · 📥 1st in" in shown
    assert ids.NEXT in shown
    guesses = {g.text: g for g in await harness.repo.get_guesses(rnd.id)}
    assert (guesses["kitties"].score, guesses["kitties"].rank, guesses["kitties"].points) == (
        95,
        1,
        10,
    )


async def test_writer_bonus_when_nobody_cracks_it(harness: Harness) -> None:
    harness.llm.judge_scores = {"trains": 20, "boats": 10}
    await play_round(harness, {0: "trains", 1: "boats"})
    rnd = await harness.round()
    assert rnd.writer_bonus_awarded is True
    assert (await harness.player(rnd.writer_user_id)).score == 10
    assert "Nobody cracked it" in await results_message(harness)


async def test_moderated_guess_hidden_and_not_judged(harness: Harness) -> None:
    harness.llm.flagged_texts = {"rude words"}
    harness.llm.judge_scores = {"kitties": 95}
    guessers = await play_round(harness, {0: "rude words", 1: "kitties"})
    judged = harness.llm.calls_to("judge")[0]["guesses"]
    assert "rude words" not in judged.values()
    assert (await harness.player(guessers[0])).score == 0
    shown = await results_message(harness)
    assert "rude words" not in shown and "hidden by moderation" in shown


async def test_equal_scores_earlier_submission_wins(harness: Harness) -> None:
    harness.llm.judge_scores = {"first": 70, "second": 70}
    guessers = await play_round(harness, {0: "first", 1: "second"})
    assert (await harness.player(guessers[0])).score == 10
    assert (await harness.player(guessers[1])).score == 5


async def test_quip_failure_still_posts_results(harness: Harness) -> None:
    harness.llm.quip_text = None
    await play_round(harness, {0: "kitties"})
    assert (await harness.round()).quip is None
    assert "Leaderboard" in await results_message(harness)


async def test_overflow_guesses_go_to_a_thread(harness: Harness) -> None:
    users = tuple(f"U{i}" for i in range(1, 11))  # 9 guessers
    await play_round(harness, {i: f"guess number {i}" for i in range(9)}, users)
    rnd = await harness.round()
    replies = [m for m in harness.slack.messages_in("C1") if m.thread_ts == rnd.results_message_ts]
    assert len(replies) == 1


async def test_awaiting_next_with_host_claim_timer(harness: Harness) -> None:
    await play_round(harness, {0: "kitties"})
    game = await harness.game()
    rnd = await harness.round()
    assert game.state is GameState.AWAITING_NEXT
    assert rnd.host_claim_at == harness.clock.now() + timedelta(seconds=300)
    assert harness.recorder.when(game.id, TimerKind.HOST_CLAIM) == rnd.host_claim_at


async def test_guesser_dms_point_to_results(harness: Harness) -> None:
    guessers = await play_round(harness, {0: "kitties"})
    assert "<#C1>" in harness.slack.dms_to(guessers[0])[-1].text


async def test_no_guesses_round(harness: Harness) -> None:
    await play_round(harness, {})
    assert harness.llm.calls_to("judge") == []
    rnd = await harness.round()
    assert rnd.status is RoundStatus.COMPLETED and rnd.writer_bonus_awarded is False
    assert "No guesses this round" in await results_message(harness)


async def test_judge_fails_once_then_retry_succeeds(harness: Harness) -> None:
    harness.llm.fail("judge", LLMError("down"), times=1)
    harness.llm.judge_scores = {"kitties": 95}
    guessers = await play_round(harness, {0: "kitties"})
    game = await harness.game()
    rnd = await harness.round()
    assert game.state is GameState.JUDGING
    assert harness.recorder.when(game.id, TimerKind.LLM_RETRY) == rnd.llm_retry_at
    await harness.engine.on_llm_retry(game.id, rnd.id)
    assert (await harness.game()).state is GameState.AWAITING_NEXT
    assert (await harness.player(guessers[0])).score == 10
    assert (await harness.round()).llm_retry_at is None


async def test_judge_fails_twice_ends_game_without_points(harness: Harness) -> None:
    harness.llm.fail("judge", LLMError("down"), times=2)
    guessers = await play_round(harness, {0: "kitties", 1: "dogs"})
    game = await harness.game()
    await harness.engine.on_llm_retry(game.id, (await harness.round()).id)
    game = await harness.game()
    assert game.state is GameState.ENDED and game.end_reason == "llm_failure"
    assert all(p.score == 0 for p in await harness.players())
    texts = harness.channel_texts()
    assert any("I have two cats" in t for t in texts)  # reveal posted
    assert any("can't continue" in t for t in texts)
    assert guessers  # silence unused


async def test_duplicate_judge_runs_award_once(harness: Harness) -> None:
    harness.llm.judge_scores = {"kitties": 95}
    guessers = await play_round(harness, {0: "kitties"})
    rnd = await harness.round()
    game = await harness.game()
    await harness.engine.on_llm_retry(game.id, rnd.id)  # stale: round already completed
    assert (await harness.player(guessers[0])).score == 10
