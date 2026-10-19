"""Offline-only signal-pool controlled-scope failure-closed regression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import (
    ACCEPTED_VERIFIED_OUTCOME,
    CONTROLLED_AUTHORITY_UNAVAILABLE,
    CONTROLLED_BINDING_PORT_MISMATCH,
    CONTROLLED_PENDING_ROOT,
    CONTROLLED_SCOPE_INVALID,
    CONTROLLED_SCOPE_MISMATCH,
    LEGACY_UNTRUSTED,
    AcceptanceReceipt,
    AttemptBinding,
    ControlledSignalScope,
    SignalPool,
    SignalPoolDraft,
    SignalPoolError,
    UTC_REQUIRED,
)

START = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
ROUTE = "QUANT_UPSTREAM_REPORT"


class FakePort:
    """A plausible but caller-owned read port, never an authority."""

    def __init__(self, binding: AttemptBinding) -> None:
        self.binding = binding
        self.calls = 0

    def binding_for(self, attempt_id: str) -> AttemptBinding | None:
        """Record access and return a matching-looking binding."""
        self.calls += 1
        return self.binding if attempt_id == self.binding.attempt_id else None


def _fixture() -> tuple[EvidenceStore, AgentIdentity, SignalPoolDraft,
                        ControlledSignalScope, AcceptanceReceipt, FakePort]:
    """Make frozen evidence, one sender, and exact acceptance-looking fields."""
    envelope = freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="owner1", market="SH", code="600000",
        source_membership_id="member1", source_revision="rev1", rule_id="r1",
        rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:1", raw_digest="a" * 64, source_id="src1",
        calendar_id="cal1", source_as_of=START, visible_at=START,
        detected_at=START, freeze_at=START, valid_until=END,
        facts=("observed",), hypotheses=("hypothesis",),
        supporting_refs=("raw:1",), contradicting_refs=(),
        confirmation_condition="condition", invalidation_condition="invalid",
        event_id="event1")
    store = EvidenceStore()
    store.freeze(envelope)
    sender = AgentIdentity("watch1", "WATCH", "registry:watch",
                           START - timedelta(days=1), END)
    draft = SignalPoolDraft("signal1", "ANOMALY", envelope.event_id,
                            envelope.envelope_hash, envelope.evidence_version)
    scope = ControlledSignalScope(
        draft.signal_id, sender.agent_id, draft.report_kind, draft.event_id,
        draft.evidence_hash, draft.evidence_version)
    receipt = AcceptanceReceipt(ROUTE, "attempt1", "ack1")
    binding = AttemptBinding(
        attempt_id=receipt.attempt_id,
        external_message_id=receipt.external_ref,
        outcome=ACCEPTED_VERIFIED_OUTCOME, route=ROUTE,
        event_id=draft.event_id, evidence_hash=draft.evidence_hash,
        evidence_version=draft.evidence_version)
    return store, sender, draft, scope, receipt, FakePort(binding)


def _claimed_pool(
        store: EvidenceStore, sender: AgentIdentity, draft: SignalPoolDraft,
        scope: ControlledSignalScope | None = None,
        port: FakePort | None = None) -> SignalPool:
    """Create a single claimed entry without touching external services."""
    pool = SignalPool(store, controlled_scope=scope, acceptance_binding=port)
    pool.append(draft, producer=sender, store=store, now=START)
    pool.claim(draft.signal_id, lease_until=END, now=START)
    return pool


def _snapshot(pool: SignalPool) -> tuple[object, object, object]:
    """Capture entry, lease and receipt before a rejected acceptance."""
    return (dict(pool._entries), dict(pool._leases), dict(pool._receipts))


def test_controlled_requires_scope_and_constructor_port_as_a_pair() -> None:
    store, _, _, scope, _, port = _fixture()
    with pytest.raises(SignalPoolError, match=f"^{CONTROLLED_SCOPE_INVALID}$"):
        SignalPool(store, controlled_scope=scope)
    with pytest.raises(SignalPoolError, match=f"^{CONTROLLED_SCOPE_INVALID}$"):
        SignalPool(store, acceptance_binding=port)
    with pytest.raises(SignalPoolError, match=f"^{CONTROLLED_SCOPE_INVALID}$"):
        SignalPool(store, controlled_scope="signal1", acceptance_binding=port)


def test_per_call_port_replacement_rejected_without_invoking_either_port() -> None:
    store, sender, draft, scope, receipt, fixed = _fixture()
    pool = _claimed_pool(store, sender, draft, scope, fixed)
    replacement = FakePort(fixed.binding)
    before = _snapshot(pool)
    for candidate in (draft.signal_id, "other-signal"):
        with pytest.raises(SignalPoolError,
                           match=f"^{CONTROLLED_BINDING_PORT_MISMATCH}$"):
            pool.mark_accepted(candidate, receipt=receipt,
                               binding_port=replacement, now=START)
    assert replacement.calls == fixed.calls == 0
    assert _snapshot(pool) == before
    assert pool.acceptance_mode == CONTROLLED_PENDING_ROOT
    assert pool.production_permitted is False


@pytest.mark.parametrize("provide_port", [False, True])
def test_even_fixed_plausible_port_cannot_accept(provide_port: bool) -> None:
    store, sender, draft, scope, receipt, fixed = _fixture()
    pool = _claimed_pool(store, sender, draft, scope, fixed)
    before = _snapshot(pool)
    with pytest.raises(SignalPoolError,
                       match=f"^{CONTROLLED_AUTHORITY_UNAVAILABLE}$"):
        pool.mark_accepted(draft.signal_id, receipt=receipt,
                           binding_port=fixed if provide_port else None,
                           now=START + timedelta(seconds=10))
    assert fixed.calls == 0
    assert _snapshot(pool) == before
    assert pool.receipt(draft.signal_id) is None


@pytest.mark.parametrize(
    ("candidate_now", "expected_error"),
    [
        (None, CONTROLLED_AUTHORITY_UNAVAILABLE),
        (START.replace(tzinfo=None), "NOW_INVALID"),
        (START.replace(tzinfo=timezone(timedelta(hours=8))), UTC_REQUIRED),
        (START - timedelta(microseconds=1), CONTROLLED_AUTHORITY_UNAVAILABLE),
        (END, CONTROLLED_AUTHORITY_UNAVAILABLE),
        (END + timedelta(microseconds=1), CONTROLLED_AUTHORITY_UNAVAILABLE),
    ],
    ids=("missing-now", "naive-now", "non-utc-now", "before-claim",
         "at-lease-end", "after-lease-end"),
)
def test_controlled_time_inputs_cannot_prove_acceptance(
        candidate_now: datetime | None, expected_error: str) -> None:
    """Caller time cannot certify a finish, even within an existing lease."""
    store, sender, draft, scope, receipt, fixed = _fixture()
    pool = _claimed_pool(store, sender, draft, scope, fixed)
    before = _snapshot(pool)
    with pytest.raises(SignalPoolError, match=f"^{expected_error}$"):
        pool.mark_accepted(draft.signal_id, receipt=receipt,
                           binding_port=fixed, now=candidate_now)
    assert fixed.calls == 0
    assert _snapshot(pool) == before
    assert pool.get(draft.signal_id) == before[0][draft.signal_id]
    assert pool.receipt(draft.signal_id) is None
    assert pool.acceptance_mode == CONTROLLED_PENDING_ROOT
    assert pool.production_permitted is False
    assert pool.notification_permitted is False
    assert pool.trading_permitted is False


@pytest.mark.parametrize("bad_receipt", [None, object(), "receipt"],
                         ids=("missing", "opaque-object", "text"))
def test_controlled_invalid_receipt_rejected_without_mutation(
        bad_receipt: object) -> None:
    """Malformed caller-owned receipts cannot bypass the pending root."""
    store, sender, draft, scope, _, fixed = _fixture()
    pool = _claimed_pool(store, sender, draft, scope, fixed)
    before = _snapshot(pool)
    with pytest.raises(SignalPoolError,
                       match=f"^{CONTROLLED_AUTHORITY_UNAVAILABLE}$"):
        pool.mark_accepted(draft.signal_id, receipt=bad_receipt,  # type: ignore[arg-type]
                           now=START + timedelta(seconds=1))
    assert fixed.calls == 0
    assert _snapshot(pool) == before
    assert pool.receipt(draft.signal_id) is None
    assert pool.production_permitted is False
    assert pool.notification_permitted is False
    assert pool.trading_permitted is False


def test_controlled_revoked_entry_cannot_be_resurrected() -> None:
    """Revocation clears lease before attempted acceptance; neither is restored."""
    store, sender, draft, scope, receipt, fixed = _fixture()
    pool = _claimed_pool(store, sender, draft, scope, fixed)
    revoked = pool.revoke(draft.signal_id, now=START + timedelta(seconds=1))
    assert revoked.status == "REVOKED"
    assert draft.signal_id not in pool._leases
    before = _snapshot(pool)
    with pytest.raises(SignalPoolError,
                       match=f"^{CONTROLLED_AUTHORITY_UNAVAILABLE}$"):
        pool.mark_accepted(draft.signal_id, receipt=receipt, binding_port=fixed,
                           now=START + timedelta(seconds=2))
    assert fixed.calls == 0
    assert _snapshot(pool) == before
    assert pool.receipt(draft.signal_id) is None
    assert pool.acceptance_mode == CONTROLLED_PENDING_ROOT
    assert pool.production_permitted is False
    assert pool.notification_permitted is False
    assert pool.trading_permitted is False


def test_other_signal_and_scope_mismatch_do_not_mutate() -> None:
    store, sender, draft, scope, receipt, fixed = _fixture()
    pool = _claimed_pool(store, sender, draft, scope, fixed)
    before = _snapshot(pool)
    with pytest.raises(SignalPoolError, match=f"^{CONTROLLED_SCOPE_MISMATCH}$"):
        pool.mark_accepted("other-signal", receipt=receipt, binding_port=fixed)
    assert _snapshot(pool) == before
    mismatched = ControlledSignalScope(
        scope.signal_id, "another-watch", scope.report_kind,
        scope.event_id, scope.evidence_hash, scope.evidence_version)
    wrong_pool = _claimed_pool(store, sender, draft, mismatched, fixed)
    wrong_before = _snapshot(wrong_pool)
    with pytest.raises(SignalPoolError, match=f"^{CONTROLLED_SCOPE_MISMATCH}$"):
        wrong_pool.mark_accepted(draft.signal_id, receipt=receipt)
    assert _snapshot(wrong_pool) == wrong_before
    assert fixed.calls == 0


def test_legacy_acceptance_unchanged_but_explicitly_untrusted() -> None:
    store, sender, draft, _, receipt, fake = _fixture()
    pool = _claimed_pool(store, sender, draft)
    assert pool.acceptance_mode == LEGACY_UNTRUSTED
    accepted = pool.mark_accepted(draft.signal_id, receipt=receipt,
                                  binding_port=fake)
    assert accepted.status == "ACCEPTED"
    assert pool.receipt(draft.signal_id) == receipt
    assert fake.calls == 1
    assert pool.production_permitted is False
    assert pool.notification_permitted is False
    assert pool.trading_permitted is False
