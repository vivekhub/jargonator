import pytest

from jargonator.domain.state import GameState, PlayerStatus, RoundStatus
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import TimerKind
from jargonator.slack import ids
from jargonator.slack.gateway import MessageRef
from tests.engine_harness import Harness


async def time_out_writer(harness: Harness) -> str:
    rnd = await harness.round()
    harness.clock.advance(90)
    await harness.engine.on_writer_timeout((await harness.game()).id, rnd.id)
    return rnd.writer_user_id


async def test_reminder_only_while_awaiting(harness: Harness) -> None:
    rnd = await harness.started("U1", "U2")
    game_id = (await harness.game()).id
    harness.clock.advance(60)
    await harness.engine.on_writer_reminder(game_id, rnd.id)
    assert "30 seconds left" in harness.slack.dms_to(rnd.writer_user_id)[-1].text
    await harness.write()
    dms = len(harness.slack.dms_to(rnd.writer_user_id))
    await harness.engine.on_writer_reminder(game_id, rnd.id)  # stale
    assert len(harness.slack.dms_to(rnd.writer_user_id)) == dms


async def test_timeout_skips_round_and_allows_next(harness: Harness) -> None:
    await harness.started("U1", "U2", "U3")
    writer = await time_out_writer(harness)
    rnd = await harness.round()
    game = await harness.game()
    assert rnd.status is RoundStatus.SKIPPED
    assert game.state is GameState.AWAITING_NEXT
    assert (await harness.player(writer)).consecutive_misses == 1
    assert any("didn't submit in time" in t for t in harness.channel_texts())
    assert rnd.results_message_ts is not None
    controls = harness.slack.current(MessageRef("C1", rnd.results_message_ts))
    assert ids.NEXT in str(controls.blocks)
    assert harness.recorder.when(game.id, TimerKind.HOST_CLAIM) is not None
    await harness.engine.next_round("C1", game.host_user_id)
    assert (await harness.round()).number == 2
    assert ids.NEXT not in str(
        harness.slack.current(MessageRef("C1", rnd.results_message_ts)).blocks
    )


async def test_stale_timeout_is_ignored(harness: Harness) -> None:
    rnd = await harness.started("U1", "U2")
    await harness.write()
    await harness.engine.on_writer_timeout((await harness.game()).id, rnd.id)
    assert (await harness.round()).status is not RoundStatus.SKIPPED


async def test_three_misses_make_writer_inactive(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2", "U3")
    await harness.engine.start_game("C1", "U1")
    victim = (await harness.round()).writer_user_id
    await harness.repo.update_player(game.id, victim, consecutive_misses=2)
    await time_out_writer(harness)
    player = await harness.player(victim)
    assert player.status is PlayerStatus.INACTIVE
    assert any("inactive" in m.text for m in harness.slack.dms_to(victim))
    # never picked again
    host = (await harness.game()).host_user_id
    for _ in range(4):
        await harness.engine.next_round("C1", host)
        assert (await harness.round()).writer_user_id != victim
        await time_out_writer(harness)
        host = (await harness.game()).host_user_id


async def test_successful_submission_resets_misses(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2")
    await harness.engine.start_game("C1", "U1")
    writer = (await harness.round()).writer_user_id
    await harness.repo.update_player(game.id, writer, consecutive_misses=2)
    await harness.write()
    assert (await harness.player(writer)).consecutive_misses == 0


async def test_inactive_host_transfers(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2", "U3")
    await harness.engine.start_game("C1", "U1")
    writer = (await harness.round()).writer_user_id
    await harness.repo.update_game(game.id, host_user_id=writer)
    await harness.repo.update_player(game.id, writer, consecutive_misses=2)
    await time_out_writer(harness)
    assert (await harness.game()).host_user_id != writer


async def test_inactive_player_rejoins(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2", "U3")
    await harness.engine.start_game("C1", "U1")
    await harness.repo.update_player(game.id, "U3", status=PlayerStatus.INACTIVE)
    await harness.engine.join("C1", "U3")
    assert (await harness.player("U3")).status is PlayerStatus.ACTIVE
    order = await harness.repo.load_turn_order(game.id)
    assert order is not None and ("U3" in order.queue or "U3" in order.written)


async def test_writer_leaving_skips_round_without_a_miss(harness: Harness) -> None:
    rnd = await harness.started("U1", "U2", "U3")
    await harness.engine.leave("C1", rnd.writer_user_id)
    assert (await harness.round()).status is RoundStatus.SKIPPED
    assert (await harness.game()).state is GameState.AWAITING_NEXT
    assert (await harness.player(rnd.writer_user_id)).consecutive_misses == 0


# --- claim host ------------------------------------------------------------------------------


async def finished_round(harness: Harness) -> str:
    await harness.started("U1", "U2", "U3")
    await time_out_writer(harness)
    return (await harness.round()).id


async def test_claim_host_before_timeout_refused(harness: Harness) -> None:
    round_id = await finished_round(harness)
    with pytest.raises(UserFacingError, match="5 minutes"):
        await harness.engine.claim_host("C1", "U2", round_id)


async def test_claim_host_after_timeout(harness: Harness) -> None:
    round_id = await finished_round(harness)
    game = await harness.game()
    harness.clock.advance(300)
    await harness.engine.on_host_claim_available(game.id, round_id)
    rnd = await harness.round()
    assert rnd.results_message_ts is not None
    ref = MessageRef("C1", rnd.results_message_ts)
    assert ids.CLAIM_HOST in str(harness.slack.current(ref).blocks)

    claimant = next(p.user_id for p in await harness.players() if p.user_id != game.host_user_id)
    await harness.engine.claim_host("C1", claimant, round_id)
    assert (await harness.game()).host_user_id == claimant
    assert ids.CLAIM_HOST not in str(harness.slack.current(ref).blocks)
    assert any(f"👑 <@{claimant}> is now the host" in t for t in harness.channel_texts())


async def test_claim_host_requires_active_player(harness: Harness) -> None:
    round_id = await finished_round(harness)
    harness.clock.advance(300)
    with pytest.raises(UserFacingError, match="players"):
        await harness.engine.claim_host("C1", "U9", round_id)


# --- kick ------------------------------------------------------------------------------------


async def test_kick_requires_host(harness: Harness) -> None:
    await harness.lobby_with("U1", "U2", "U3")
    with pytest.raises(UserFacingError, match="Only the host"):
        await harness.engine.kick("C1", "U2", "U3")
    with pytest.raises(UserFacingError, match="yourself"):
        await harness.engine.kick("C1", "U1", "U1")
    with pytest.raises(UserFacingError, match="isn't in this game"):
        await harness.engine.kick("C1", "U1", "U9")


async def test_kick_removes_player(harness: Harness) -> None:
    await harness.lobby_with("U1", "U2", "U3")
    await harness.engine.kick("C1", "U1", "U3")
    assert (await harness.player("U3")).status is PlayerStatus.LEFT
    assert any("<@U3> was removed" in t for t in harness.channel_texts())


async def test_kicking_last_pending_guesser_closes_early(harness: Harness) -> None:
    await harness.started("U1", "U2", "U3", "U4")
    await harness.write()
    rnd = await harness.round()
    guessers = sorted(await harness.guessers())
    host = (await harness.game()).host_user_id
    pending = [g for g in guessers if g != host]
    for user in guessers:
        if user != pending[-1]:
            await harness.engine.submit_guess(user, rnd.id, "I own kitties")
    await harness.engine.kick("C1", host, pending[-1])
    assert (await harness.round()).status is RoundStatus.COMPLETED
