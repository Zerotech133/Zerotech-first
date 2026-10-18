"""Adversarial offline tests: fake producer completeness never establishes coverage."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone

import pytest

from quant_backtest.strategy_pool import (
    BatchStatus, CoverageApproval, CoverageScope, DecisionKind, EventKind,
    Evidence, Hit, PoolPolicy, PoolState, RunnerObservation, ScanBatch,
    SecurityId, StrategyId, project_security, receipt_is_valid, reduce_batch,
    set_manual_hold, verify_runner_batch,
)

KEY = b"runner-only-test-key-not-from-producer!!"
BASE = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
A = SecurityId("CN.SH", "600000")
B = SecurityId("CN.SZ", "600000")
STRATEGY = StrategyId("A", "strong", "v1")


def make_batch(day=14, hits=(A,), strategy=STRATEGY, batch_id=None,
               scope=CoverageScope.FULL, status=BatchStatus.COMPLETE,
               observed=None):
    at = BASE + timedelta(days=day - 14)
    evidence = Evidence("direct", "signal:1", observed or at, ("cross:parent",))
    return ScanBatch(batch_id or f"run-{day}", strategy, date(2026, 10, day),
                     at, status, scope, tuple(Hit(item, evidence) for item in hits))


def attest(batch, universe=frozenset({A}), visited=None, selected=None,
           completed=True):
    observation = RunnerObservation(batch.batch_id, batch.strategy, batch.trade_date,
                                    batch.produced_at + timedelta(minutes=1), completed,
                                    universe if visited is None else frozenset(visited),
                                    frozenset(hit.security for hit in batch.hits)
                                    if selected is None else frozenset(selected))
    approval = CoverageApproval(batch.strategy, batch.trade_date, batch.scope, universe)
    return verify_runner_batch(batch, observation, approval, KEY)


def apply(state, batch, receipt=None, policy=PoolPolicy()):
    return reduce_batch(state, batch, receipt, KEY,
                        batch.produced_at + timedelta(hours=1), policy)


def test_producer_complete_and_empty_are_unverified_without_independent_receipt():
    batch = make_batch(hits=())
    state = apply(PoolState(), batch)
    assert state.members == () and state.events == ()
    assert state.decisions[-1].kind is DecisionKind.UNVERIFIED
    assert apply(state, make_batch(day=15, hits=(), batch_id="other")).accepted == ()
    assert apply(PoolState(), make_batch()).decisions[-1].unverified_hits


def test_full_and_scope_require_independently_approved_visited_universe():
    batch = make_batch(hits=())
    with pytest.raises(ValueError, match="every independently visited"):
        attest(batch, frozenset({A, B}), visited={A}, selected=set())
    with pytest.raises(ValueError, match="exceed approved"):
        attest(batch, frozenset({A}), visited={A, B}, selected=set())
    scoped = make_batch(hits=(), scope=CoverageScope.SCOPED)
    receipt = attest(scoped, frozenset({A, B}), visited={A}, selected=set())
    state = apply(PoolState(), scoped, receipt)
    assert state.decisions[-1].kind is DecisionKind.ACCEPTED
    assert receipt.covered == frozenset({A})
    with pytest.raises(ValueError, match="selections"):
        attest(make_batch(), selected=set())
    with pytest.raises(ValueError, match="complete"):
        attest(batch, completed=False)
    with pytest.raises(ValueError, match="complete"):
        attest(replace(batch, status=BatchStatus.PARTIAL))


def test_authenticated_receipt_is_bound_to_payload_and_asof():
    batch = make_batch()
    receipt = attest(batch)
    assert receipt_is_valid(batch, receipt, KEY, receipt.finished_at)
    assert not receipt_is_valid(batch, receipt, b"different-runner-key-which-is-long!",
                                receipt.finished_at)
    assert not receipt_is_valid(batch, receipt, KEY, batch.produced_at)
    modified = replace(batch, hits=(Hit(A, Evidence("direct", "changed", BASE)),))
    assert apply(PoolState(), modified, receipt).decisions[-1].kind is DecisionKind.UNVERIFIED
    assert not receipt_is_valid(batch, replace(receipt, approved_universe=frozenset({A, B})),
                                KEY, receipt.finished_at)
    with pytest.raises(FrozenInstanceError):
        receipt.signature = "changed"


def test_absences_only_new_verified_day_and_market_and_no_default_exit():
    initial = make_batch()
    state = apply(PoolState(), initial, attest(initial))
    empty = make_batch(day=15, hits=())
    state = apply(state, empty)  # Producer claims no hit but no coverage receipt.
    assert state.members[0].consecutive_absences == 0
    state = apply(state, empty, attest(empty))
    assert state.members[0].consecutive_absences == 1
    assert apply(state, empty, attest(empty)) == state
    rerun = make_batch(day=15, hits=(A,), batch_id="revision")
    state = apply(state, rerun, attest(rerun))
    assert state.decisions[-1].kind is DecisionKind.CONFLICT
    assert state.members[0].consecutive_absences == 1
    older = make_batch(day=14, hits=(), batch_id="older")
    assert apply(state, older, attest(older)).decisions[-1].kind is DecisionKind.STALE
    for day in (16, 17, 18):
        newer = make_batch(day=day, hits=())
        state = apply(state, newer, attest(newer))
    assert state.members[0].active and state.members[0].consecutive_absences == 4


def test_scoped_excludes_unvisited_markets_and_preserves_other_pools():
    first = make_batch(hits=(A, B))
    state = apply(PoolState(), first, attest(first, frozenset({A, B})))
    scoped = make_batch(day=15, hits=(), scope=CoverageScope.SCOPED)
    state = apply(state, scoped, attest(scoped, frozenset({A, B}), visited={A}, selected=set()))
    counts = {member.security: member.consecutive_absences for member in state.members}
    assert counts == {A: 1, B: 0}
    pool_b = StrategyId("B", "strong", "v1")
    other = make_batch(day=15, hits=(A,), strategy=pool_b)
    state = apply(state, other, attest(other))
    assert len(state.members) == 3
    view = project_security(A, (state,))
    assert len(view.independent_strategies) == 2  # Parent id is not another strategy.
    assert project_security(B, (state,)).independent_strategies == (STRATEGY,)
    unverified = make_batch(day=16, hits=(A,), strategy=StrategyId("A", "cross", "v1"))
    view = project_security(A, (state,), (unverified,))
    assert len(view.independent_strategies) == 2 and len(view.unverified_hits) == 1


def test_exit_reentry_episode_and_manual_hold_only_in_isolated_policy():
    enabled = PoolPolicy(auto_exit_enabled=True, absence_threshold=2)
    first = make_batch()
    state = apply(PoolState(), first, attest(first), enabled)
    state = set_manual_hold(state, STRATEGY, A, BASE + timedelta(minutes=2))
    for day in (15, 16):
        empty = make_batch(day=day, hits=())
        state = apply(state, empty, attest(empty), enabled)
    assert state.members[0].active and state.members[0].held
    assert state.events[-1].kind is EventKind.ABSENT
    with pytest.raises(ValueError, match="not found"):
        set_manual_hold(state, STRATEGY, B, BASE)
    # Independent, explicitly enabled episode without a hold can exit and reenter.
    state = apply(PoolState(), first, attest(first), enabled)
    for day in (15, 16):
        empty = make_batch(day=day, hits=())
        state = apply(state, empty, attest(empty), enabled)
    assert not state.members[0].active
    assert state.events[-1].kind is EventKind.EXIT
    back = make_batch(day=17)
    state = apply(state, back, attest(back), enabled)
    assert state.members[0].episode == 2 and state.members[0].active
    assert [event.kind for event in state.events] == [
        EventKind.ENTER, EventKind.ABSENT, EventKind.EXIT, EventKind.REENTER]


def test_future_and_invalid_time_and_batch_id_conflicts_fail_closed():
    with pytest.raises(ValueError, match="future evidence"):
        make_batch(observed=BASE + timedelta(days=1))
    future = replace(make_batch(), trade_date=date(2026, 10, 13))
    with pytest.raises(ValueError, match="future trading day"):
        attest(future)
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        Evidence("direct", "ref", datetime(2026, 10, 14))
    first = make_batch()
    state = apply(PoolState(), first, attest(first))
    reused = make_batch(hits=(), batch_id=first.batch_id)
    assert apply(state, reused).decisions[-1].kind is DecisionKind.CONFLICT
    day_future = make_batch(day=15)
    assert reduce_batch(PoolState(), day_future, attest(day_future), KEY, BASE).decisions[-1].kind is DecisionKind.UNVERIFIED
