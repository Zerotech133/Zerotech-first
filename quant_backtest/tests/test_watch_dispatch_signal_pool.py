"""XuChu watch camp signal pool tests. Fully offline, memory-only.

Covers the frozen-evidence binding, producer attribution, per-producer
dedupe with co-sign, lease claiming with lazy expiry recycling, the
OPEN -> CLAIMED -> ACCEPTED loop, irreversible revoke with monotonic
epoch, and naive-datetime rejection. No network, no filesystem, no
execution semantics anywhere.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch import EvidenceStore, freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.signal_pool import (
    ACCEPTED_VERIFIED_OUTCOME, AcceptanceReceipt, AttemptBinding, SignalPool, SignalPoolDraft,
    SignalPoolEntry, SignalPoolError, STATUS_ACCEPTED, STATUS_CLAIMED,
    STATUS_OPEN, STATUS_REVOKED,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
LATER = T + timedelta(minutes=30)
H = "a" * 64

FIELDS = dict(
    schema_version=1, evidence_version=1, previous_version_hash=None,
    pool="B", tenant_or_owner_id="owner1", market="SH", code="600000",
    source_membership_id="member1", source_revision="rev1", rule_id="r1",
    rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
    window="5m", observed_value="1.10", baseline_value="1.00",
    raw_ref="raw:1", raw_digest=H, source_id="src1", calendar_id="cal1",
    source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
    valid_until=LATER, facts=("observed",), hypotheses=("c",),
    supporting_refs=("raw:1",), contradicting_refs=(),
    confirmation_condition="c", invalidation_condition="i",
)


def envelope_v1(event_id: str = "ev-1", **changes):
    fields = dict(FIELDS, event_id=event_id)
    fields.update(changes)
    return freeze_offline(**fields)


def envelope_v2(previous, event_id: str = "ev-1"):
    """Chain a second frozen version (different hash, same event_id)."""
    fields = dict(FIELDS)
    fields.pop("previous_version_hash")
    fields.update(
        event_id=event_id, evidence_version=2,
        freeze_at=T + timedelta(minutes=1),
        valid_until=LATER + timedelta(minutes=1),
        observed_value="1.20", _previous=previous)
    return freeze_offline(**fields)


def store_with(*envelopes) -> EvidenceStore:
    store = EvidenceStore()
    for env in envelopes:
        store.freeze(env)
    return store


def watch_agent(agent_id: str = "watch-agent-1", **changes) -> AgentIdentity:
    fields = dict(role="WATCH", registry_ref="registry:watch:1",
                  registered_at=T - timedelta(days=1), valid_until=LATER)
    fields.update(changes)
    return AgentIdentity(agent_id=agent_id, **fields)


def draft(signal_id: str, env, kind: str = "ANOMALY") -> SignalPoolDraft:
    return SignalPoolDraft(signal_id=signal_id, report_kind=kind,
                           event_id=env.event_id,
                           evidence_hash=env.envelope_hash,
                           evidence_version=env.evidence_version)


def receipt(attempt_id: str = "att-1", **changes) -> AcceptanceReceipt:
    fields = dict(route="QUANT_UPSTREAM_REPORT", attempt_id=attempt_id,
                  external_ref="ack-1", outcome=ACCEPTED_VERIFIED_OUTCOME)
    fields.update(changes)
    return AcceptanceReceipt(**fields)


class StubBindingPort:
    """Explicit test-only settled-attempt proof for state-machine cases."""

    def __init__(self, env, accepted_receipt=None, binding=None):
        accepted_receipt = accepted_receipt or receipt()
        self.binding = binding if binding is not None else AttemptBinding(
            attempt_id=accepted_receipt.attempt_id,
            external_message_id=accepted_receipt.external_ref,
            outcome=ACCEPTED_VERIFIED_OUTCOME,
            route=accepted_receipt.route,
            event_id=env.event_id,
            evidence_hash=env.envelope_hash,
            evidence_version=env.evidence_version,
        )

    def binding_for(self, attempt_id: str) -> AttemptBinding | None:
        return self.binding if self.binding.attempt_id == attempt_id else None


def fresh_pool(env) -> tuple[SignalPool, EvidenceStore]:
    store = store_with(env)
    return SignalPool(store), store


def test_append_rejects_unfrozen_evidence():
    pool, store = fresh_pool(envelope_v1())
    ghost = SignalPoolDraft(signal_id="sig-x", report_kind="ANOMALY",
                            event_id="ev-ghost", evidence_hash="f" * 64,
                            evidence_version=1)
    with pytest.raises(SignalPoolError, match="EVIDENCE_NOT_FROZEN"):
        pool.append(ghost, producer=watch_agent(), store=store, now=T)
    assert pool.get("sig-x") is None
    assert pool.list_open(T) == ()


def test_append_rejects_non_watch_producer():
    pool, store = fresh_pool(envelope_v1())
    sector = watch_agent(role="QUANT_SECTOR")
    with pytest.raises(SignalPoolError, match="PRODUCER_ROLE_INVALID"):
        pool.append(draft("sig-1", envelope_v1()), producer=sector,
                    store=store, now=T)


def test_append_rejects_expired_producer_identity():
    pool, store = fresh_pool(envelope_v1())
    stale = watch_agent(valid_until=T)
    with pytest.raises(SignalPoolError, match="AGENT_NOT_CURRENT"):
        pool.append(draft("sig-1", envelope_v1()), producer=stale,
                    store=store, now=T)


def test_append_rejects_unknown_report_kind():
    pool, store = fresh_pool(envelope_v1())
    with pytest.raises(SignalPoolError, match="REPORT_KIND_INVALID"):
        pool.append(draft("sig-1", envelope_v1(), kind="DIRECT_FEISHU"),
                    producer=watch_agent(), store=store, now=T)
    with pytest.raises(SignalPoolError, match="REPORT_KIND_INVALID"):
        pool.append(draft("sig-2", envelope_v1(), kind="HEALTH_OK"),
                    producer=watch_agent(), store=store, now=T)


def test_append_is_idempotent_for_same_event_hash_producer():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    producer = watch_agent()
    first = pool.append(draft("sig-1", env), producer=producer, store=store, now=T)
    again = pool.append(draft("sig-1", env), producer=producer, store=store,
                        now=T + timedelta(seconds=1))
    assert again is first
    # Different signal_id but identical (event, hash, producer) still dedupes.
    renamed = pool.append(draft("sig-again", env), producer=producer,
                          store=store, now=T + timedelta(seconds=2))
    assert renamed is first
    assert pool.list_open(T + timedelta(seconds=2)) == (first,)


def test_append_conflict_same_event_different_hash_same_producer():
    v1, v2 = envelope_v1(), envelope_v2(envelope_v1())
    store = store_with(v1, v2)
    pool = SignalPool(store)
    producer = watch_agent()
    pool.append(draft("sig-1", v1), producer=producer, store=store, now=T)
    with pytest.raises(SignalPoolError, match="SIGNAL_CONFLICT"):
        pool.append(draft("sig-2", v2), producer=producer, store=store, now=T)
    assert pool.get("sig-2") is None
    assert pool.get("sig-1").evidence_hash == v1.envelope_hash


def test_cosign_two_producers_keeps_both_entries():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    first = pool.append(draft("sig-1", env), producer=watch_agent("watch-agent-1"),
                        store=store, now=T)
    second = pool.append(draft("sig-2", env),
                         producer=watch_agent("watch-agent-2"),
                         store=store, now=T + timedelta(seconds=1))
    assert first is not second
    assert first.signal_id != second.signal_id
    assert first.co_signed is None
    assert second.co_signed == "sig-1"
    assert second.producer_agent_id == "watch-agent-2"
    assert first.producer_agent_id == "watch-agent-1"
    assert pool.list_open(T + timedelta(seconds=1)) == (first, second)


def test_append_rejects_naive_now():
    pool, store = fresh_pool(envelope_v1())
    naive = datetime(2026, 10, 19, 2, 0)
    with pytest.raises(SignalPoolError, match="NOW_INVALID"):
        pool.append(draft("sig-1", envelope_v1()), producer=watch_agent(),
                    store=store, now=naive)
    with pytest.raises(SignalPoolError, match="NOW_INVALID"):
        pool.list_open(naive)
    with pytest.raises(SignalPoolError, match="NOW_INVALID"):
        pool.revoke("sig-1", now=naive)


def test_claim_moves_open_to_claimed():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    entry = pool.append(draft("sig-1", env), producer=watch_agent(),
                        store=store, now=T)
    claimed = pool.claim("sig-1", lease_until=T + timedelta(minutes=10),
                         now=T + timedelta(seconds=1))
    assert claimed.status == STATUS_CLAIMED
    assert claimed.epoch == 0
    assert claimed.signal_id == entry.signal_id
    assert pool.get("sig-1", now=T + timedelta(seconds=2)).status == STATUS_CLAIMED


def test_double_claim_rejected_with_claim_held():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.claim("sig-1", lease_until=T + timedelta(minutes=10), now=T)
    with pytest.raises(SignalPoolError, match="CLAIM_HELD"):
        pool.claim("sig-1", lease_until=T + timedelta(minutes=10),
                   now=T + timedelta(seconds=1))


def test_expired_lease_recycled_back_to_open():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.claim("sig-1", lease_until=T + timedelta(minutes=10), now=T)
    at_expiry = T + timedelta(minutes=20)
    now_open = pool.list_open(at_expiry)
    assert now_open == (pool.get("sig-1"),)
    assert now_open[0].status == STATUS_OPEN
    assert now_open[0].epoch == 0
    # Recycled entry can be claimed again under a fresh lease.
    reclaimed = pool.claim("sig-1", lease_until=at_expiry + timedelta(minutes=10),
                           now=at_expiry)
    assert reclaimed.status == STATUS_CLAIMED


def test_mark_accepted_closes_claimed_loop():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.claim("sig-1", lease_until=LATER, now=T)
    accepted = pool.mark_accepted("sig-1", receipt=receipt(), binding_port=StubBindingPort(env))
    assert accepted.status == STATUS_ACCEPTED
    assert accepted.epoch == 0
    assert pool.receipt("sig-1") == receipt()
    assert pool.list_open(T + timedelta(seconds=1)) == ()
    with pytest.raises(SignalPoolError, match="CLAIM_STATE_INVALID"):
        pool.claim("sig-1", lease_until=LATER, now=T + timedelta(seconds=1))


def test_mark_accepted_rejects_wrong_receipt_outcome():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.claim("sig-1", lease_until=LATER, now=T)
    with pytest.raises(SignalPoolError, match="RECEIPT_OUTCOME_INVALID"):
        pool.mark_accepted("sig-1", receipt=receipt(outcome="UNKNOWN"),
                           binding_port=StubBindingPort(env))
    assert pool.get("sig-1").status == STATUS_CLAIMED


def _claimed_pool(env=None):
    env = env or envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.claim("sig-1", lease_until=LATER, now=T)
    return pool, env


def _assert_claim_unchanged(pool):
    assert pool.get("sig-1").status == STATUS_CLAIMED
    assert pool.receipt("sig-1") is None
    assert pool._leases["sig-1"] == LATER


def test_binding_port_is_mandatory_b1():
    # WO-QT-20261020-080: 764d631f relaxed binding_port to optional (controlled
    # mode requires an absent binding_port to be valid), so a missing argument
    # now follows the explicit always-fails-closed path instead of raising TypeError.
    # The mandatory-binding contract is asserted via RECEIPT_VERIFIER_REQUIRED.
    pool, env = _claimed_pool()
    with pytest.raises(SignalPoolError, match="RECEIPT_VERIFIER_REQUIRED"):
        pool.mark_accepted("sig-1", receipt=receipt())
    with pytest.raises(SignalPoolError, match="RECEIPT_VERIFIER_REQUIRED"):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=None)
    _assert_claim_unchanged(pool)


def test_binding_missing_attempt_b2():
    pool, env = _claimed_pool()
    port = StubBindingPort(env, binding=replace(StubBindingPort(env).binding,
                                                attempt_id="another"))
    with pytest.raises(SignalPoolError, match="RECEIPT_ATTEMPT_UNKNOWN"):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=port)
    _assert_claim_unchanged(pool)


@pytest.mark.parametrize("field,value,error", [
    ("external_message_id", "wrong-ref", "RECEIPT_REF_MISMATCH"),
    ("outcome", "UNKNOWN", "RECEIPT_OUTCOME_MISMATCH"),
    ("route", "FEISHU", "RECEIPT_ROUTE_MISMATCH"),
    ("event_id", "other-event", "RECEIPT_IDENTITY_MISMATCH"),
    ("evidence_hash", "b" * 64, "RECEIPT_IDENTITY_MISMATCH"),
    ("evidence_version", 2, "RECEIPT_IDENTITY_MISMATCH"),
])
def test_binding_field_mismatch_fails_closed_b3_to_b6(field, value, error):
    pool, env = _claimed_pool()
    port = StubBindingPort(env, binding=replace(StubBindingPort(env).binding,
                                                **{field: value}))
    with pytest.raises(SignalPoolError, match=error):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=port)
    _assert_claim_unchanged(pool)


def test_cross_signal_real_attempt_cannot_be_reused_b5():
    first, env = _claimed_pool()
    second, other = _claimed_pool(envelope_v1("ev-other"))
    true_binding = StubBindingPort(env)
    assert first.mark_accepted("sig-1", receipt=receipt(),
                               binding_port=true_binding).status == STATUS_ACCEPTED
    with pytest.raises(SignalPoolError, match="RECEIPT_IDENTITY_MISMATCH"):
        second.mark_accepted("sig-1", receipt=receipt(), binding_port=true_binding)
    _assert_claim_unchanged(second)


def test_port_exception_fails_closed_and_expired_lease_recycles_b9():
    pool, env = _claimed_pool()

    class BrokenPort:
        def binding_for(self, attempt_id: str):
            raise RuntimeError("down")

    with pytest.raises(SignalPoolError, match="RECEIPT_ATTEMPT_UNKNOWN"):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=BrokenPort())
    _assert_claim_unchanged(pool)
    assert pool.get("sig-1", now=LATER + timedelta(seconds=1)).status == STATUS_OPEN
    pool.claim("sig-1", lease_until=LATER + timedelta(minutes=10),
               now=LATER + timedelta(seconds=2))
    assert pool.mark_accepted("sig-1", receipt=receipt(),
                              binding_port=StubBindingPort(env)).status == STATUS_ACCEPTED


def test_mark_accepted_on_open_is_rejected():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    with pytest.raises(SignalPoolError, match="ACCEPT_STATE_INVALID"):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=StubBindingPort(env))
    assert pool.get("sig-1").status == STATUS_OPEN


def test_mark_accepted_on_revoked_is_rejected():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.claim("sig-1", lease_until=LATER, now=T)
    pool.revoke("sig-1", now=T + timedelta(seconds=1))
    with pytest.raises(SignalPoolError, match="ACCEPT_STATE_INVALID"):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=StubBindingPort(env))


def test_revoke_blocks_every_further_transition():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    revoked = pool.revoke("sig-1", now=T + timedelta(seconds=1))
    assert revoked.status == STATUS_REVOKED
    assert revoked.epoch == 1
    with pytest.raises(SignalPoolError, match="CLAIM_STATE_INVALID"):
        pool.claim("sig-1", lease_until=LATER, now=T + timedelta(seconds=2))
    with pytest.raises(SignalPoolError, match="ACCEPT_STATE_INVALID"):
        pool.mark_accepted("sig-1", receipt=receipt(), binding_port=StubBindingPort(env))
    assert pool.get("sig-1", now=T + timedelta(seconds=2)).status == STATUS_REVOKED
    assert pool.list_open(T + timedelta(seconds=2)) == ()


def test_second_revoke_rejected_and_epoch_never_regresses():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    pool.revoke("sig-1", now=T)
    with pytest.raises(SignalPoolError, match="REVOKE_FINAL"):
        pool.revoke("sig-1", now=T + timedelta(seconds=1))
    final = pool.get("sig-1")
    assert final.status == STATUS_REVOKED
    assert final.epoch == 1


def test_claim_rejects_past_or_unknown_targets():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    with pytest.raises(SignalPoolError, match="CLAIM_LEASE_INVALID"):
        pool.claim("sig-1", lease_until=T, now=T)
    with pytest.raises(SignalPoolError, match="SIGNAL_UNKNOWN"):
        pool.claim("sig-missing", lease_until=LATER, now=T)
    assert pool.get("sig-1").status == STATUS_OPEN


# ---------------------------------------------------------------------------
# Non-zero-offset aware datetimes: every entry point must raise
# SignalPoolError (a ValueError subclass), never a bare ValueError leaking
# from canonical.utc_text.
# ---------------------------------------------------------------------------
OFFSET_TZ = timezone(timedelta(hours=8))


def _assert_utc_required(excinfo):
    assert isinstance(excinfo.value, SignalPoolError)
    assert isinstance(excinfo.value, ValueError)
    assert "UTC_REQUIRED" in str(excinfo.value)


def test_nonzero_offset_now_rejected_at_append():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    with pytest.raises(SignalPoolError) as excinfo:
        pool.append(draft("sig-1", env), producer=watch_agent(),
                    store=store, now=T.astimezone(OFFSET_TZ))
    _assert_utc_required(excinfo)
    assert pool.get("sig-1") is None


def test_nonzero_offset_now_rejected_at_claim():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    with pytest.raises(SignalPoolError) as excinfo:
        pool.claim("sig-1", lease_until=LATER, now=T.astimezone(OFFSET_TZ))
    _assert_utc_required(excinfo)
    with pytest.raises(SignalPoolError) as lease_excinfo:
        pool.claim("sig-1", lease_until=LATER.astimezone(OFFSET_TZ), now=T)
    _assert_utc_required(lease_excinfo)
    assert pool.get("sig-1").status == STATUS_OPEN


def test_nonzero_offset_now_rejected_at_list_open():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    with pytest.raises(SignalPoolError) as excinfo:
        pool.list_open(T.astimezone(OFFSET_TZ))
    _assert_utc_required(excinfo)
    assert len(pool.list_open(T)) == 1


def test_nonzero_offset_now_rejected_at_revoke():
    env = envelope_v1()
    pool, store = fresh_pool(env)
    pool.append(draft("sig-1", env), producer=watch_agent(), store=store, now=T)
    with pytest.raises(SignalPoolError) as excinfo:
        pool.revoke("sig-1", now=T.astimezone(OFFSET_TZ))
    _assert_utc_required(excinfo)
    assert pool.get("sig-1").status == STATUS_OPEN
    # State untouched: a proper UTC revoke still succeeds afterwards.
    assert pool.revoke("sig-1", now=T).epoch == 1


def test_append_rejects_mismatched_store_instance():
    pool, store = fresh_pool(envelope_v1())
    other = store_with(envelope_v1())
    with pytest.raises(SignalPoolError, match="STORE_MISMATCH"):
        pool.append(draft("sig-1", envelope_v1()), producer=watch_agent(),
                    store=other, now=T)


def test_n1_and_n_gt1_producers_share_identical_api_semantics():
    """N=1 and N>1 flows run through the same API with the same guarantees."""
    env = envelope_v1()
    store = store_with(env)

    # N=1: one producer, idempotent append, full lifecycle.
    solo = SignalPool(store)
    solo_producer = watch_agent("watch-agent-1")
    solo.append(draft("sig-a", env), producer=solo_producer, store=store, now=T)
    solo.append(draft("sig-a", env), producer=solo_producer, store=store, now=T)
    solo.claim("sig-a", lease_until=LATER, now=T)
    solo_accepted = solo.mark_accepted("sig-a", receipt=receipt(attempt_id="att-a"),
                                       binding_port=StubBindingPort(env, receipt(attempt_id="att-a")))
    assert solo_accepted.status == STATUS_ACCEPTED
    assert solo_accepted.epoch == 0
    assert solo_accepted.co_signed is None
    assert len(solo.list_open(T)) == 0

    # N>1: two producers, co-sign, both entries retained independently.
    duo = SignalPool(store)
    p1, p2 = watch_agent("watch-agent-1"), watch_agent("watch-agent-2")
    # Dedupe is producer-scoped: p1 seeds sig-b1; p2's append of the same
    # event+hash is a NEW entry co-signed to the first reporter's signal_id.
    duo.append(draft("sig-b1", env), producer=p1, store=store, now=T)
    b2 = duo.append(draft("sig-b2", env), producer=p2, store=store, now=T)
    assert b2.co_signed == "sig-b1"
    duo_entries = duo.list_open(T)
    assert len(duo_entries) == 2
    assert {e.producer_agent_id for e in duo_entries} == {
        "watch-agent-1", "watch-agent-2"}
    assert {e.signal_id for e in duo_entries} == {"sig-b1", "sig-b2"}
    # Idempotency remains producer-scoped under the same API.
    again = duo.append(draft("sig-b2", env), producer=p2, store=store, now=T)
    assert again is b2
    assert len(duo.list_open(T)) == 2
    # Same lifecycle reachable for either entry, identical to the N=1 flow.
    duo.claim("sig-b2", lease_until=LATER, now=T)
    duo_accepted = duo.mark_accepted("sig-b2", receipt=receipt(attempt_id="att-b"),
                                     binding_port=StubBindingPort(env, receipt(attempt_id="att-b")))
    assert duo_accepted.status == STATUS_ACCEPTED
    assert duo_accepted.epoch == 0
