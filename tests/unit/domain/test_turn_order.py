import itertools
import random
from collections import Counter

import pytest

from jargonator.domain.turn_order import TurnOrder


def rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


def play_cycle(order: TurnOrder, active: set[str], r: random.Random) -> list[str]:
    return [w for w in (order.next_writer(active, r) for _ in range(len(active))) if w]


def test_new_contains_every_player_once() -> None:
    order = TurnOrder.new(["a", "b", "c", "d"], rng())
    assert order.cycle_no == 1
    assert sorted(order.queue) == ["a", "b", "c", "d"]
    assert order.last_writer is None


def test_new_is_deterministic_for_a_seed_regardless_of_input_order() -> None:
    first = TurnOrder.new(["a", "b", "c"], rng(1))
    second = TurnOrder.new(["c", "a", "b"], rng(1))
    assert first.queue == second.queue


def test_everyone_writes_exactly_once_per_cycle() -> None:
    r = rng()
    players = {"a", "b", "c", "d", "e"}
    order = TurnOrder.new(sorted(players), r)
    for cycle in range(1, 21):
        writers = play_cycle(order, players, r)
        assert Counter(writers) == Counter(players)
        assert order.cycle_no == cycle


@pytest.mark.parametrize("n", [2, 3, 5])
def test_no_immediate_repeat_across_cycle_boundaries(n: int) -> None:
    r = rng(n)
    players = {f"u{i}" for i in range(n)}
    order = TurnOrder.new(sorted(players), r)
    writers = [order.next_writer(players, r) for _ in range(200 * n)]
    assert all(a != b for a, b in itertools.pairwise(writers))


def test_inactive_players_are_skipped_and_return_next_cycle() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b", "c"], r)
    queue = list(order.queue)
    first = order.next_writer({"a", "b", "c"}, r)
    assert first == queue[0]
    away = queue[1]
    rest = {"a", "b", "c"} - {away}
    assert order.next_writer(rest, r) == queue[2]  # `away` skipped
    # New cycle: `away` is active again and is included.
    next_cycle = play_cycle(order, {"a", "b", "c"}, r)
    assert sorted(next_cycle) == ["a", "b", "c"]
    assert order.cycle_no == 2


def test_joiner_appended_mid_cycle_writes_before_cycle_ends() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b"], r)
    order.next_writer({"a", "b"}, r)
    order.append("z")
    active = {"a", "b", "z"}
    assert [order.next_writer(active, r), order.next_writer(active, r)][-1] == "z"
    assert order.cycle_no == 1


def test_append_is_noop_if_queued_or_already_written_this_cycle() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b", "c"], r)
    wrote = order.next_writer({"a", "b", "c"}, r)
    assert wrote is not None
    before = list(order.queue)
    order.append(before[0])
    order.append(wrote)  # rejoining after writing waits for the next cycle
    assert order.queue == before


def test_requeue_front_makes_user_next_writer() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b", "c"], r)
    voided = order.next_writer({"a", "b", "c"}, r)
    assert voided is not None
    order.requeue_front(voided)
    assert order.next_writer({"a", "b", "c"}, r) == voided


def test_requeue_front_removes_duplicates() -> None:
    order = TurnOrder.new(["a", "b", "c"], rng())
    target = order.queue[2]
    order.requeue_front(target)
    assert order.queue[0] == target
    assert order.queue.count(target) == 1


def test_requeued_writer_still_writes_once_this_cycle() -> None:
    r = rng()
    players = {"a", "b", "c"}
    order = TurnOrder.new(sorted(players), r)
    voided = order.next_writer(players, r)
    assert voided is not None
    order.requeue_front(voided)
    assert Counter(play_cycle(order, players, r)) == Counter(players)


def test_zero_active_players_returns_none() -> None:
    order = TurnOrder.new(["a", "b"], rng())
    assert order.next_writer(set(), rng()) is None
    assert order.cycle_no == 1


def test_single_active_player_keeps_writing() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b"], r)
    assert [order.next_writer({"a"}, r) for _ in range(4)] == ["a"] * 4


def test_rows_round_trip() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b", "c", "d"], r)
    order.next_writer({"a", "b", "c", "d"}, r)
    order.append("e")
    order.requeue_front(order.queue[-2])
    restored = TurnOrder.from_rows(order.to_rows(), order.last_writer)
    assert restored == order


def test_skipped_player_rejoining_mid_cycle_round_trips() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b", "c"], r)
    skipped = order.queue[1]
    order.next_writer({"a", "b", "c"} - {skipped}, r)  # first writer
    order.next_writer({"a", "b", "c"} - {skipped}, r)  # skips `skipped`, pops the third
    order.append(skipped)  # comes back: not written this cycle, so re-queued
    assert order.queue == [skipped]
    assert TurnOrder.from_rows(order.to_rows(), order.last_writer) == order


def test_rows_format() -> None:
    r = rng()
    order = TurnOrder.new(["a", "b"], r)
    first = order.next_writer({"a", "b"}, r)
    rows = order.to_rows()
    assert rows[0] == (1, 0, first, True)
    assert [row[1] for row in rows] == list(range(len(rows)))
    assert all(row[0] == 1 for row in rows)


def test_from_empty_rows() -> None:
    order = TurnOrder.from_rows([], None)
    assert order == TurnOrder(cycle_no=1, queue=[], written=[], last_writer=None)
