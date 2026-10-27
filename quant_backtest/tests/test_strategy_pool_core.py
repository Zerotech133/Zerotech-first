"""Adversarial offline tests: fake producer completeness never establishes coverage."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from quant_backtest.strategy_pool import (
    BatchStatus, CoverageApproval, CoverageScope, DecisionKind, EventKind,
    Evidence, Hit, PoolPolicy, PoolState, RunnerObservation, ScanBatch,
    SecurityId, StrategyId, measure_artifact, project_security, receipt_is_valid, reduce_batch,
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


def artifact_bytes(batch):
    """Test-only deterministic raw artifact; not a production source parser."""
    return json.dumps([{"market": hit.security.market, "code": hit.security.code,
                        "source": hit.evidence.source,
                        "reference": hit.evidence.reference,
                        "observed_at": hit.evidence.observed_at.isoformat(),
                        "parent_ids": list(hit.evidence.parent_ids)}
                       for hit in batch.hits], separators=(",", ":")).encode("utf-8")


def trusted_inputs(batch, universe=frozenset({A}), visited=None, selected=None,
                   completed=True, raw=None, parsed=None, exit_code=0,
                   rule_hash=None, calendar="CN-trading-v1", version="2026-10"):
    raw = artifact_bytes(batch) if raw is None else raw
    parsed = batch.hits if parsed is None else parsed
    rule_hash = rule_hash or hashlib.sha256(b"test-runner-rule-v1").hexdigest()
    seal = measure_artifact(raw, parsed, f"sealed/{batch.batch_id}")
    observation = RunnerObservation(batch.batch_id, batch.strategy, batch.trade_date,
                                    batch.produced_at + timedelta(minutes=1), completed,
                                    universe if visited is None else frozenset(visited),
                                    frozenset(hit.security for hit in batch.hits)
                                    if selected is None else frozenset(selected),
                                    f"runner/{batch.batch_id}", rule_hash, calendar,
                                    exit_code, seal)
    approval = CoverageApproval(batch.strategy, batch.trade_date, batch.scope,
                                universe, "runner-approved-universe", version,
                                rule_hash, calendar)
    return raw, parsed, observation, approval


def attest(batch, universe=frozenset({A}), visited=None, selected=None,
           completed=True):
    raw, parsed, observation, approval = trusted_inputs(
        batch, universe, visited, selected, completed)
    return verify_runner_batch(batch, observation, approval, KEY, raw, parsed)


def apply(state, batch, receipt=None, policy=PoolPolicy(), raw=None, parsed=None,
          observation=None, approval=None):
    if receipt is not None and observation is None and approval is None:
        expected_raw, expected_parsed, observation, approval = trusted_inputs(
            batch, receipt.approved_universe, receipt.covered)
        raw = expected_raw if raw is None else raw
        parsed = expected_parsed if parsed is None else parsed
    return reduce_batch(state, batch, receipt, KEY,
                        batch.produced_at + timedelta(hours=1), policy,
                        raw_bytes=raw, parsed_hits=parsed,
                        observation=observation, approval=approval)


def test_producer_complete_and_empty_are_unverified_without_independent_receipt():
    batch = make_batch(hits=())
    state = apply(PoolState(), batch)
    assert state.members == () and state.events == ()
    assert state.decisions[-1].kind is DecisionKind.UNVERIFIED
    assert apply(state, make_batch(day=15, hits=(), batch_id="other")).accepted == ()
    assert apply(PoolState(), make_batch()).decisions[-1].unverified_hits
    assert apply(PoolState(), batch, attest(batch), raw=b"[] ").decisions[-1].kind is DecisionKind.UNVERIFIED


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
    raw, parsed, observation, approval = trusted_inputs(batch)
    assert receipt_is_valid(batch, receipt, KEY, receipt.finished_at,
                            raw, parsed, observation, approval)
    assert not receipt_is_valid(batch, receipt, b"different-runner-key-which-is-long!",
                                receipt.finished_at, raw, parsed, observation, approval)
    assert not receipt_is_valid(batch, receipt, KEY, batch.produced_at,
                                raw, parsed, observation, approval)
    modified = replace(batch, hits=(Hit(A, Evidence("direct", "changed", BASE)),))
    assert apply(PoolState(), modified, receipt).decisions[-1].kind is DecisionKind.UNVERIFIED
    assert not receipt_is_valid(batch, replace(receipt, approved_universe=frozenset({A, B})),
                                KEY, receipt.finished_at, raw, parsed, observation, approval)
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


def test_raw_artifact_byte_replacement_never_reuses_old_receipt():
    batch = make_batch()
    raw, parsed, observation, approval = trusted_inputs(batch)
    receipt = verify_runner_batch(batch, observation, approval, KEY, raw, parsed)
    first = apply(PoolState(), batch, receipt, raw=raw, parsed=parsed,
                  observation=observation, approval=approval)
    assert first.decisions[-1].kind is DecisionKind.ACCEPTED
    tampered = b"no item selected"  # Same DTO and old receipt, different final source bytes.
    second = apply(PoolState(), batch, receipt, raw=tampered, parsed=parsed,
                   observation=observation, approval=approval)
    assert second.decisions[-1].kind is DecisionKind.UNVERIFIED
    assert second.members == () and second.accepted == ()
    # A previously accepted batch cannot silently short-circuit byte remeasurement.
    replay = apply(first, batch, receipt, raw=tampered, parsed=parsed,
                   observation=observation, approval=approval)
    assert replay.decisions[-1].kind is DecisionKind.UNVERIFIED
    empty = make_batch(day=15, hits=())
    eraw, eparsed, eobs, eapproval = trusted_inputs(empty)
    er = verify_runner_batch(empty, eobs, eapproval, KEY, eraw, eparsed)
    frozen = apply(first, empty, er, raw=b"[ ]", parsed=eparsed,
                   observation=eobs, approval=eapproval)
    assert frozen.members[0].consecutive_absences == 0


def test_identical_invalid_replay_is_fully_idempotent_even_after_acceptance():
    batch = make_batch()
    raw, parsed, observation, approval = trusted_inputs(batch)
    receipt = verify_runner_batch(batch, observation, approval, KEY, raw, parsed)
    valid = apply(PoolState(), batch, receipt, raw=raw, parsed=parsed,
                  observation=observation, approval=approval)
    bad = apply(valid, batch, receipt, raw=raw + b" ", parsed=parsed,
                observation=observation, approval=approval)
    assert bad.decisions[-1].kind is DecisionKind.UNVERIFIED
    for _ in range(3):
        replay = apply(bad, batch, receipt, raw=raw + b" ", parsed=parsed,
                       observation=observation, approval=approval)
        assert replay is bad
        bad = replay
    assert bad.members == valid.members and bad.events == valid.events
    assert bad.accepted == valid.accepted and len(bad.decisions) == len(valid.decisions) + 1
    # An invalid replay must never short-circuit back to the earlier accepted state.
    assert bad != valid


def test_rejection_identity_includes_full_batch_payload_and_reason():
    batch = make_batch()
    rejected = apply(PoolState(), batch)
    assert apply(rejected, batch) is rejected
    revised = replace(batch, hits=())  # Same run ID, different producer payload.
    changed = apply(rejected, revised)
    assert changed.decisions[-1].batch_fingerprint != rejected.decisions[-1].batch_fingerprint
    assert len(changed.decisions) == 2
    assert apply(changed, revised) is changed
    # A trusted batch can be promoted after an earlier unverified display.
    raw, parsed, observation, approval = trusted_inputs(batch)
    receipt = verify_runner_batch(batch, observation, approval, KEY, raw, parsed)
    promoted = apply(rejected, batch, receipt, raw=raw, parsed=parsed,
                     observation=observation, approval=approval)
    assert promoted.decisions[-1].kind is DecisionKind.ACCEPTED
    assert len(promoted.members) == 1 and len(promoted.events) == 1
    # A later conflicting run ID is audited once, not conflated with prior absence.
    conflict = apply(promoted, revised)
    assert conflict.decisions[-1].kind is DecisionKind.CONFLICT
    assert conflict.decisions[-1].batch_fingerprint != promoted.decisions[-1].batch_fingerprint
    assert apply(conflict, revised) is conflict
    further_change = replace(revised, status=BatchStatus.PARTIAL)
    assert len(apply(conflict, further_change).decisions) == len(conflict.decisions) + 1


def test_rejection_reason_changes_are_distinct_but_each_is_idempotent():
    batch = make_batch()
    rejected = apply(PoolState(), batch)
    early = reduce_batch(rejected, batch, None, KEY, BASE - timedelta(hours=1))
    assert early.decisions[-1].reason != rejected.decisions[-1].reason
    assert len(early.decisions) == 2
    again = reduce_batch(early, batch, None, KEY, BASE - timedelta(hours=1))
    assert again is early


def test_seal_row_count_size_and_original_parse_mismatch_fail_closed():
    batch = make_batch()
    raw, parsed, observation, approval = trusted_inputs(batch)
    for invalid in (replace(observation.artifact, size=observation.artifact.size + 1),
                    replace(observation.artifact, row_count=0),
                    replace(observation.artifact, sha256="0" * 64)):
        with pytest.raises(ValueError, match="artifact seal"):
            verify_runner_batch(batch, replace(observation, artifact=invalid),
                                approval, KEY, raw, parsed)
    with pytest.raises(ValueError, match="parsed artifact hits"):
        verify_runner_batch(batch, observation, approval, KEY, raw, ())
    with pytest.raises(ValueError, match="parsed artifact hits"):
        measure_artifact(b"[]", parsed, observation.artifact.identity)
    with pytest.raises(ValueError, match="duplicate artifact JSON key"):
        measure_artifact(b'[{"market":"CN.SH","market":"CN.SZ"}]', (), "sealed/duplicate")
    with pytest.raises(ValueError, match="artifact row fields"):
        measure_artifact(b'[{"market":"CN.SH"}]', (), "sealed/incomplete")
    receipt = verify_runner_batch(batch, observation, approval, KEY, raw, parsed)
    assert not receipt_is_valid(batch, receipt, KEY, receipt.finished_at)
    assert not receipt_is_valid(batch, receipt, KEY, receipt.finished_at,
                                raw, (), observation, approval)
    assert not receipt_is_valid(batch, replace(receipt, artifact=replace(
        receipt.artifact, size=receipt.artifact.size + 1)), KEY,
        receipt.finished_at, raw, parsed, observation, approval)
    assert apply(PoolState(), batch, receipt, observation=observation,
                 approval=approval).decisions[-1].kind is DecisionKind.UNVERIFIED


def test_runner_exit_rule_calendar_and_universe_version_drift_fail_closed():
    batch = make_batch()
    raw, parsed, observation, approval = trusted_inputs(batch)
    receipt = verify_runner_batch(batch, observation, approval, KEY, raw, parsed)
    with pytest.raises(ValueError, match="exit_code=0"):
        verify_runner_batch(batch, replace(observation, exit_code=1),
                            approval, KEY, raw, parsed)
    for changed in (replace(observation, rule_sha256="f" * 64),
                    replace(observation, calendar_identity="other-calendar")):
        with pytest.raises(ValueError, match="rule hash or calendar"):
            verify_runner_batch(batch, changed, approval, KEY, raw, parsed)
    for changed_observation, changed_approval in (
        (replace(observation, exit_code=1), approval),
        (replace(observation, run_id="other-run"), approval),
        (replace(observation, rule_sha256="f" * 64), approval),
        (replace(observation, calendar_identity="other-calendar"), approval),
        (observation, replace(approval, version="new-version")),
        (observation, replace(approval, identity="other-universe"))):
        assert not receipt_is_valid(batch, receipt, KEY, receipt.finished_at,
                                    raw, parsed, changed_observation, changed_approval)
    assert not receipt_is_valid(batch, receipt, KEY, receipt.finished_at,
                                raw, parsed, observation, None)
    assert apply(PoolState(), batch, receipt, observation=observation,
                 approval=replace(approval, version="new-version"), raw=raw,
                 parsed=parsed).decisions[-1].kind is DecisionKind.UNVERIFIED


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
