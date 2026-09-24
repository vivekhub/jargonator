import random
from datetime import UTC, datetime, timedelta

import pytest

from jargonator.db.repo import DuplicateGuessError, GuessResult, Repo
from jargonator.domain.state import JargonLevel, RoundStatus
from jargonator.domain.turn_order import TurnOrder

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


async def new_game(repo: Repo, channel: str = "C1") -> str:
    game = await repo.create_game(
        channel_id=channel,
        host_user_id="U1",
        guess_seconds=60,
        writer_seconds=90,
        join_window_seconds=120,
        now=NOW,
    )
    return game.id


async def new_round(repo: Repo, game_id: str, number: int = 1, writer: str = "U1") -> str:
    rnd = await repo.create_round(
        game_id=game_id,
        number=number,
        writer_user_id=writer,
        writer_deadline=NOW + timedelta(seconds=90),
        writer_reminder_at=NOW + timedelta(seconds=60),
        now=NOW,
    )
    return rnd.id


# --- rounds -----------------------------------------------------------------------


async def test_create_round_defaults(repo: Repo) -> None:
    game_id = await new_game(repo)
    rnd = await repo.get_round(await new_round(repo, game_id))
    assert rnd is not None
    assert rnd.game_id == game_id
    assert rnd.number == 1
    assert rnd.writer_user_id == "U1"
    assert rnd.status is RoundStatus.AWAITING_SENTENCE
    assert rnd.writer_deadline == NOW + timedelta(seconds=90)
    assert rnd.writer_reminder_at == NOW + timedelta(seconds=60)
    assert rnd.started_at == NOW
    assert rnd.level is None and rnd.sentence is None and rnd.jargon is None
    assert rnd.writer_bonus_awarded is False


async def test_get_missing_round(repo: Repo) -> None:
    assert await repo.get_round("nope") is None


async def test_get_current_round_is_newest(repo: Repo) -> None:
    game_id = await new_game(repo)
    assert await repo.get_current_round(game_id) is None
    await new_round(repo, game_id, 1)
    second = await new_round(repo, game_id, 2, writer="U2")
    current = await repo.get_current_round(game_id)
    assert current is not None and current.id == second


async def test_list_rounds_in_order(repo: Repo) -> None:
    game_id = await new_game(repo)
    for n in (1, 2, 3):
        await new_round(repo, game_id, n)
    assert [r.number for r in await repo.list_rounds(game_id)] == [1, 2, 3]


async def test_duplicate_round_number_rejected(repo: Repo) -> None:
    game_id = await new_game(repo)
    await new_round(repo, game_id, 1)
    with pytest.raises(Exception, match="UNIQUE"):
        await new_round(repo, game_id, 1)


async def test_update_round(repo: Repo) -> None:
    game_id = await new_game(repo)
    round_id = await new_round(repo, game_id)
    updated = await repo.update_round(
        round_id,
        status=RoundStatus.GUESSING,
        level=JargonLevel.UNHINGED,
        sentence="I have two cats",
        jargon="I steward a dual-feline portfolio",
        guess_deadline=NOW + timedelta(minutes=3),
        status_message_ts="111.222",
        writer_bonus_awarded=True,
    )
    assert updated.status is RoundStatus.GUESSING
    assert updated.level is JargonLevel.UNHINGED
    assert updated.sentence == "I have two cats"
    assert updated.guess_deadline == NOW + timedelta(minutes=3)
    assert updated.status_message_ts == "111.222"
    assert updated.writer_bonus_awarded is True


async def test_update_round_rejects_unknown_fields(repo: Repo) -> None:
    game_id = await new_game(repo)
    round_id = await new_round(repo, game_id)
    with pytest.raises(ValueError, match="writer_user_id"):
        await repo.update_round(round_id, writer_user_id="U9")


# --- guessers -----------------------------------------------------------------------


async def test_round_guessers(repo: Repo) -> None:
    game_id = await new_game(repo)
    round_id = await new_round(repo, game_id)
    await repo.add_round_guessers(round_id, [("U2", "D2"), ("U3", "D3")])
    await repo.set_guesser_dm_ts(round_id, "U3", "999.1")
    guessers = await repo.get_round_guessers(round_id)
    assert [(g.user_id, g.dm_channel_id, g.dm_message_ts) for g in guessers] == [
        ("U2", "D2", None),
        ("U3", "D3", "999.1"),
    ]


# --- guesses ------------------------------------------------------------------------


async def test_add_and_get_guesses(repo: Repo) -> None:
    game_id = await new_game(repo)
    round_id = await new_round(repo, game_id)
    first = await repo.add_guess(round_id, "U2", "I own cats", NOW + timedelta(seconds=5))
    await repo.add_guess(round_id, "U3", "I like dogs", NOW + timedelta(seconds=3))
    guesses = await repo.get_guesses(round_id)
    assert [g.user_id for g in guesses] == ["U3", "U2"]  # submission order
    assert first.text == "I own cats"
    assert first.submitted_at == NOW + timedelta(seconds=5)
    assert first.similarity is None and first.rank is None and first.points == 0


async def test_duplicate_guess_raises(repo: Repo) -> None:
    game_id = await new_game(repo)
    round_id = await new_round(repo, game_id)
    await repo.add_guess(round_id, "U2", "first", NOW)
    with pytest.raises(DuplicateGuessError):
        await repo.add_guess(round_id, "U2", "second", NOW)
    assert [g.text for g in await repo.get_guesses(round_id)] == ["first"]


async def test_save_guess_results(repo: Repo) -> None:
    game_id = await new_game(repo)
    round_id = await new_round(repo, game_id)
    a = await repo.add_guess(round_id, "U2", "a", NOW)
    b = await repo.add_guess(round_id, "U3", "b", NOW)
    await repo.save_guess_results(
        round_id,
        [
            GuessResult(a.id, similarity=0.81, rank=1, points=10, moderated_out=False),
            GuessResult(b.id, similarity=0.0, rank=None, points=0, moderated_out=True),
        ],
    )
    by_user = {g.user_id: g for g in await repo.get_guesses(round_id)}
    assert (by_user["U2"].similarity, by_user["U2"].rank, by_user["U2"].points) == (0.81, 1, 10)
    assert by_user["U2"].moderated_out is False
    assert (by_user["U3"].rank, by_user["U3"].moderated_out) == (None, True)


async def test_save_guess_results_ignores_other_rounds(repo: Repo) -> None:
    game_id = await new_game(repo)
    r1 = await new_round(repo, game_id, 1)
    r2 = await new_round(repo, game_id, 2)
    g2 = await repo.add_guess(r2, "U2", "x", NOW)
    await repo.save_guess_results(r1, [GuessResult(g2.id, 0.5, 1, 10, False)])
    assert (await repo.get_guesses(r2))[0].points == 0


# --- turn order ---------------------------------------------------------------------


async def test_load_turn_order_before_start(repo: Repo) -> None:
    assert await repo.load_turn_order(await new_game(repo)) is None


async def test_turn_order_round_trip_after_append_and_requeue(repo: Repo) -> None:
    game_id = await new_game(repo)
    rng = random.Random(3)
    order = TurnOrder.new(["U1", "U2", "U3", "U4"], rng)
    await repo.save_turn_order(game_id, order)
    assert await repo.load_turn_order(game_id) == order

    writer = order.next_writer({"U1", "U2", "U3", "U4"}, rng)
    assert writer is not None
    order.append("U5")
    order.requeue_front(writer)
    order.next_writer({"U1", "U2", "U3", "U4", "U5"}, rng)
    await repo.save_turn_order(game_id, order)  # replaces the previous rows
    assert await repo.load_turn_order(game_id) == order


async def test_turn_order_new_cycle_replaces_rows(repo: Repo) -> None:
    game_id = await new_game(repo)
    rng = random.Random(1)
    order = TurnOrder.new(["U1", "U2", "U3"], rng)
    for _ in range(4):  # into cycle 2
        order.next_writer({"U1", "U2"}, rng)
    await repo.save_turn_order(game_id, order)
    loaded = await repo.load_turn_order(game_id)
    assert loaded == order
    assert loaded is not None and loaded.cycle_no == 2
