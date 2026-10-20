"""Offline adversarial coverage for the game signal-reference bridge.

Every case is synthetic and self-contained: no upstream ledger is mutated
(no SignalPool.append from the bridge, no inbox write from the bridge),
no I/O, no network. Where a gate demands authenticity, the fixtures build
REAL ``UpstreamInbox`` / ``SignalPool`` instances and derive receipts and
entries from them; the bridge itself is asserted to be side-effect free
against those real ledgers (state snapshots before/after must be equal).

Blocking-review coverage (game-bridge-review-135):
- first-epoch authenticity: a real first receipt (ledger_epoch == 0) is a
  legitimate positive; only type-invalid/negative epochs are refused;
- stale/revoked receipts fail closed via the mandatory trusted
  ``inbox_receipt_lookup`` callback bound to the real inbox;
- cross-object time order is enforced in both builders with future- and
  past-timestamp counterexamples;
- zero-side-effect proof compares full state of a real inbox and a real
  signal pool before/after building and verifying references.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.signal_ref_bridge import (
    ACCEPTANCE_REF_DIGEST_DOMAIN,
    ACK_RECEIVER_MISMATCH,
    ACK_REPORT_MISMATCH,
    ACK_TYPE_INVALID,
    BUSINESS_SIGNAL_REQUIRED,
    DIGEST_MISMATCH,
    ENVELOPE_MISMATCH,
    ENTRY_TYPE_INVALID,
    EPOCH_MISMATCH,
    INBOX_EPOCH_INVALID,
    INTENT_TYPE_INVALID,
    LEDGER_EPOCH_INVALID,
    MEMBERSHIP_MISSING,
    NOT_A_PERMISSION,
    POOL_IDENTITY_MISSING,
    POOL_VERSION_MISSING,
    PRODUCER_MISMATCH,
    RECEIPT_LOOKUP_MISSING,
    RECEIPT_MISMATCH,
    RECEIPT_NOT_ACTIVE,
    RECEIPT_REPORT_MISMATCH,
    RECEIPT_TYPE_INVALID,
    REF_FIELD_MISMATCH,
    REF_OBJECT_MISSING,
    REF_TIME_ORDER_INVALID,
    REF_TYPE_INVALID,
    REPORT_TYPE_INVALID,
    REVOKED_REFUSED,
    RULE_IDENTITY_MISSING,
    SCOPE_POOL_IDENTITY_UNAVAILABLE,
    SECURITY_IDENTITY_MISSING,
    SENDER_MISMATCH,
    SIGNAL_REF_DIGEST_DOMAIN,
    STATUS_REVOKED_SENTINEL,
    VERIFICATION_SOURCE_MISMATCH,
    VERIFICATION_SOURCE_UPSTREAM_INBOX_V1,
    AcceptanceRefV1,
    SignalRefBridgeError,
    SignalRefV1,
    build_acceptance_ref,
    build_signal_ref,
    verify_acceptance_ref,
    verify_signal_ref,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamAck,
    UpstreamInbox,
    UpstreamReceipt,
)
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.contracts import freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import (
    ControlledSignalScope,
    SignalPool,
    SignalPoolDraft,
    SignalPoolEntry,
    STATUS_REVOKED,
)
from quant_backtest.watch_dispatch.stock_pool_source import (
    OFFLINE_SHADOW,
    PoolMember,
    StockPoolSnapshot,
    StockPoolSource,
    WatchSignalIntent,
)

NOW = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
SIG_ID = "signal-1"
EVT_ID = "evidence-event-1"
HASH = "a" * 64
OTHER_HASH = "b" * 64
POOL_ID = "pool-core-1"
MEMBER_ID = "member-1"


def at(minutes: int) -> datetime:
    """Timeline helper for the real pool fixture (anchored at NOW)."""
    return NOW + timedelta(minutes=minutes)


def make_entry(**overrides: object) -> SignalPoolEntry:
    values: dict = dict(
        signal_id=SIG_ID, producer_agent_id="watch-agent",
        report_kind="ANOMALY", event_id=EVT_ID, evidence_hash=HASH,
        evidence_version=1, created_at=NOW)
    values.update(overrides)
    return SignalPoolEntry(**values)


def make_intent(**overrides: object) -> WatchSignalIntent:
    values: dict = dict(
        draft=SignalPoolDraft(
            signal_id=SIG_ID, report_kind="ANOMALY", event_id=EVT_ID,
            evidence_hash=HASH, evidence_version=1),
        pool_id=POOL_ID, pool_version=3, market="XSHZ", symbol="600519",
        membership_id=MEMBER_ID, rule_id="rule-breakout", rule_version=2,
        trigger_at=NOW, prepared_at=NOW, producer_agent_id="watch-agent",
        source_manifest=OTHER_HASH)
    values.update(overrides)
    return WatchSignalIntent(**values)


def make_report(**overrides: object) -> QuantUpstreamReport:
    values: dict = dict(
        report_id="report-1", report_kind="ANOMALY", event_id=EVT_ID,
        evidence_hash=HASH, evidence_version=1,
        sender_agent_id="watch-agent", receiver_agent_id="quant-agent",
        created_at=NOW)
    values.update(overrides)
    return QuantUpstreamReport(**values)


def make_receipt(report: QuantUpstreamReport, **overrides: object) -> UpstreamReceipt:
    ack_values: dict = dict(
        ack_id="ack-1", report_id=report.report_id,
        receiver_agent_id=report.receiver_agent_id, acked_at=NOW,
        verification_source=VERIFICATION_SOURCE_UPSTREAM_INBOX_V1)
    ack_values.update(overrides.pop("ack_overrides", {}))
    ack = UpstreamAck(**ack_values)
    values: dict = dict(
        receipt_id="receipt-1", report_id=report.report_id,
        envelope_hash=report.envelope_hash,
        sender_agent_id=report.sender_agent_id, accepted_at=NOW,
        ack=ack, ledger_epoch=3)
    values.update(overrides)
    return UpstreamReceipt(**values)


def make_binding(report: QuantUpstreamReport):
    """Synthetic receipt plus a self-consistent trusted-lookup binding.

    The lookup returns the same receipt as the current authoritative state,
    which lets gate-level unit tests isolate one gate at a time. Authentic
    end-to-end behaviour is covered by ``make_real_acceptance``.
    """
    receipt = make_receipt(report)
    return receipt, (lambda report_id: receipt)


def make_real_acceptance(**report_overrides: object):
    """A real UpstreamInbox with one really-accepted report.

    The first real receipt carries ``ledger_epoch == 0`` (the fresh
    inbox's initial epoch) — the authentic first-acceptance positive. The
    returned lookup is bound to the real inbox: it returns the current
    receipt only while the report is accepted and not revoked.
    """
    inbox = UpstreamInbox()
    report = make_report(**report_overrides)
    receipt = inbox.accept(report, now=NOW)

    def lookup(report_id: str) -> UpstreamReceipt | None:
        current = inbox.receipt_for_report(report_id)
        if current is None or inbox.is_revoked(report_id):
            return None
        return current

    return inbox, report, receipt, lookup


def _make_frozen_evidence():
    """A real frozen evidence envelope matching the real pool fixture."""
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        event_id=EVT_ID, pool="B", tenant_or_owner_id="owner-1",
        market="XSHZ", code="600519", source_membership_id=MEMBER_ID,
        source_revision="revision-1", rule_id="rule-breakout", rule_version=2,
        comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:1", raw_digest=OTHER_HASH, source_id="synthetic",
        calendar_id="synthetic", source_as_of=at(2),
        visible_at=at(2), detected_at=at(2), freeze_at=at(2),
        valid_until=at(15),
        facts=("synthetic",), hypotheses=(), supporting_refs=("raw:1",),
        contradicting_refs=(), confirmation_condition="synthetic confirmation",
        invalidation_condition="synthetic invalidation",
    )


def make_real_pool_context():
    """A real SignalPool with a really appended entry from the real adapter.

    Exercises the genuine production chain: publish snapshot -> select ->
    freeze evidence -> prepare_trigger -> SignalPool.append. The resulting
    intent satisfies the bridge's cross-object time order
    (trigger_at=at(3) == prepared_at=at(3) <= entry.created_at=at(4)).
    """
    snapshot = StockPoolSnapshot(
        POOL_ID, 1, "publisher-1",
        (PoolMember("XSHZ", "600519", MEMBER_ID),),
        at(0), at(1), at(20), OTHER_HASH, OFFLINE_SHADOW)
    source = StockPoolSource(frozenset({"publisher-1"}))
    source.publish(snapshot, now=at(0))
    watcher = AgentIdentity("watch-agent", "WATCH", "registry-1",
                            at(0), at(20))
    selection = source.select(POOL_ID, market="XSHZ", symbol="600519",
                              watcher=watcher, as_of=at(1))
    evidence = _make_frozen_evidence()
    store = EvidenceStore()
    store.freeze(evidence)
    pool = SignalPool(store)
    intent = source.prepare_trigger(
        selection, triggered=True, evidence=evidence, store=store,
        signal_id=SIG_ID, trigger_at=at(3), now=at(3))
    entry = pool.append(intent.draft, producer=watcher, store=store,
                        now=at(4))
    return pool, intent, entry


def set_frozen(obj: object, field: str, value: object) -> None:
    """In-place frozen-field tamper (used to corrupt ref fields/digest)."""
    object.__setattr__(obj, field, value)


# ----------------------------------------------------------------------
# SignalRefV1: construction, determinism, identity independence
# ----------------------------------------------------------------------

def test_signal_ref_binds_fields_and_is_deterministic():
    entry = make_entry()
    intent = make_intent()
    ref = build_signal_ref(intent, entry)
    again = build_signal_ref(intent, entry)
    assert ref == again
    assert ref.digest == again.digest
    assert len(ref.digest) == 64
    assert (ref.signal_id, ref.event_id, ref.producer_agent_id) == (
        SIG_ID, EVT_ID, "watch-agent")
    assert (ref.pool_id, ref.pool_version) == (POOL_ID, 3)
    assert (ref.market, ref.symbol, ref.membership_id) == ("XSHZ", "600519", MEMBER_ID)
    assert (ref.rule_id, ref.rule_version) == ("rule-breakout", 2)
    assert ref.trigger_at == NOW and ref.created_at == NOW
    assert verify_signal_ref(ref, intent, entry) is ref


def test_signal_id_and_event_id_are_independent_fields():
    entry = make_entry()
    intent = make_intent()
    ref = build_signal_ref(intent, entry)
    # Both are bound independently; neither is derived from the other.
    assert ref.signal_id == entry.signal_id
    assert ref.event_id == entry.event_id
    assert ref.signal_id != ref.event_id
    # Same event, different signal_id: only signal_id moves; event_id is
    # not re-derived from it.
    other_entry = make_entry(signal_id="signal-2")
    other_intent = make_intent(
        draft=SignalPoolDraft(signal_id="signal-2", report_kind="ANOMALY",
                              event_id=EVT_ID, evidence_hash=HASH,
                              evidence_version=1))
    other_ref = build_signal_ref(other_intent, other_entry)
    assert other_ref.signal_id == "signal-2"
    assert other_ref.event_id == EVT_ID
    assert other_ref.digest != ref.digest
    # Swapped identity pair never passes the field binding.
    with pytest.raises(SignalRefBridgeError, match=REF_FIELD_MISMATCH):
        build_signal_ref(other_intent, entry)


# ----------------------------------------------------------------------
# SignalRefV1: adversarial tamper matrix (fail-closed on every field)
# ----------------------------------------------------------------------

def test_signal_ref_tamper_matrix_rejects_every_field():
    ref = build_signal_ref(make_intent(), make_entry())
    # (label, tampered intent, expected code)
    intent_cases = [
        ("draft.signal_id", make_intent(draft=dataclasses.replace(
            make_intent().draft, signal_id="signal-x")), REF_FIELD_MISMATCH),
        ("draft.event_id", make_intent(draft=dataclasses.replace(
            make_intent().draft, event_id="event-x")), REF_FIELD_MISMATCH),
        ("draft.evidence_hash", make_intent(draft=dataclasses.replace(
            make_intent().draft, evidence_hash="c" * 64)), REF_FIELD_MISMATCH),
        ("draft.evidence_version", make_intent(draft=dataclasses.replace(
            make_intent().draft, evidence_version=2)), REF_FIELD_MISMATCH),
        ("draft.report_kind", make_intent(draft=dataclasses.replace(
            make_intent().draft, report_kind="HEALTH")), REF_FIELD_MISMATCH),
        ("producer_agent_id", make_intent(
            producer_agent_id="watch-x"), PRODUCER_MISMATCH),
        ("pool_id", make_intent(pool_id="pool-x"), REF_FIELD_MISMATCH),
        ("pool_version", make_intent(pool_version=4), REF_FIELD_MISMATCH),
        ("market", make_intent(market="XSHE"), REF_FIELD_MISMATCH),
        ("symbol", make_intent(symbol="000001"), REF_FIELD_MISMATCH),
        ("membership_id", make_intent(
            membership_id="member-x"), REF_FIELD_MISMATCH),
        ("rule_id", make_intent(rule_id="rule-x"), REF_FIELD_MISMATCH),
        ("rule_version", make_intent(rule_version=9), REF_FIELD_MISMATCH),
    ]
    for label, tampered_intent, code in intent_cases:
        with pytest.raises(SignalRefBridgeError, match=code) as excinfo:
            verify_signal_ref(ref, tampered_intent, make_entry())
        assert excinfo.value.args[0] == code, label
    # (label, tampered entry, expected code)
    entry_cases = [
        ("entry.signal_id", make_entry(signal_id="signal-x"), REF_FIELD_MISMATCH),
        ("entry.event_id", make_entry(event_id="event-x"), REF_FIELD_MISMATCH),
        ("entry.evidence_hash", make_entry(
            evidence_hash="c" * 64), REF_FIELD_MISMATCH),
        ("entry.evidence_version", make_entry(
            evidence_version=2), REF_FIELD_MISMATCH),
        ("entry.producer_agent_id", make_entry(
            producer_agent_id="watch-x"), PRODUCER_MISMATCH),
        # A later created_at still passes the time order (entry appended
        # after the intent) and is caught by field comparison.
        ("entry.created_at", make_entry(
            created_at=NOW + timedelta(minutes=1)), REF_FIELD_MISMATCH),
    ]
    for label, tampered_entry, code in entry_cases:
        with pytest.raises(SignalRefBridgeError, match=code) as excinfo:
            verify_signal_ref(ref, make_intent(), tampered_entry)
        assert excinfo.value.args[0] == code, label


def test_signal_ref_digest_recompute_mismatch_rejected():
    ref = build_signal_ref(make_intent(), make_entry())
    set_frozen(ref, "digest", "0" * 64)
    with pytest.raises(SignalRefBridgeError, match=DIGEST_MISMATCH):
        verify_signal_ref(ref, make_intent(), make_entry())


def test_pool_version_mismatch_rejected():
    ref = build_signal_ref(make_intent(), make_entry())
    with pytest.raises(SignalRefBridgeError, match=REF_FIELD_MISMATCH):
        verify_signal_ref(ref, make_intent(pool_version=4), make_entry())


def test_missing_membership_fails_closed():
    with pytest.raises(SignalRefBridgeError, match=MEMBERSHIP_MISSING):
        build_signal_ref(make_intent(membership_id=""), make_entry())
    with pytest.raises(SignalRefBridgeError, match=MEMBERSHIP_MISSING):
        build_signal_ref(make_intent(membership_id=None), make_entry())  # type: ignore[arg-type]


def test_missing_identity_fields_fail_closed():
    with pytest.raises(SignalRefBridgeError, match=POOL_IDENTITY_MISSING):
        build_signal_ref(make_intent(pool_id=""), make_entry())
    with pytest.raises(SignalRefBridgeError, match=POOL_VERSION_MISSING):
        build_signal_ref(make_intent(pool_version=0), make_entry())
    with pytest.raises(SignalRefBridgeError, match=SECURITY_IDENTITY_MISSING):
        build_signal_ref(make_intent(market=""), make_entry())
    with pytest.raises(SignalRefBridgeError, match=SECURITY_IDENTITY_MISSING):
        build_signal_ref(make_intent(symbol=""), make_entry())
    with pytest.raises(SignalRefBridgeError, match=RULE_IDENTITY_MISSING):
        build_signal_ref(make_intent(rule_id=""), make_entry())


def test_health_kind_refused_for_anomaly_signal_ref():
    health_entry = make_entry(report_kind="HEALTH")
    health_intent = make_intent(draft=SignalPoolDraft(
        signal_id=SIG_ID, report_kind="HEALTH", event_id=EVT_ID,
        evidence_hash=HASH, evidence_version=1))
    with pytest.raises(SignalRefBridgeError, match=BUSINESS_SIGNAL_REQUIRED):
        build_signal_ref(health_intent, health_entry)


def test_revoked_entry_refused():
    revoked = make_entry(status=STATUS_REVOKED, epoch=1)
    with pytest.raises(SignalRefBridgeError, match=REVOKED_REFUSED):
        build_signal_ref(make_intent(), revoked)


def test_scope_without_pool_identity_refused():
    scope = ControlledSignalScope(
        signal_id=SIG_ID, producer_agent_id="watch-agent",
        report_kind="ANOMALY", event_id=EVT_ID, evidence_hash=HASH,
        evidence_version=1)
    with pytest.raises(SignalRefBridgeError,
                       match=SCOPE_POOL_IDENTITY_UNAVAILABLE):
        build_signal_ref(scope, make_entry())  # type: ignore[arg-type]


def test_signal_ref_wrong_types_and_missing_objects_fail_closed():
    with pytest.raises(SignalRefBridgeError, match=INTENT_TYPE_INVALID):
        build_signal_ref("not-an-intent", make_entry())  # type: ignore[arg-type]
    with pytest.raises(SignalRefBridgeError, match=ENTRY_TYPE_INVALID):
        build_signal_ref(make_intent(), "not-an-entry")  # type: ignore[arg-type]
    ref = build_signal_ref(make_intent(), make_entry())
    with pytest.raises(SignalRefBridgeError, match=REF_OBJECT_MISSING):
        verify_signal_ref(ref, None, make_entry())  # type: ignore[arg-type]
    with pytest.raises(SignalRefBridgeError, match=REF_TYPE_INVALID):
        verify_signal_ref("not-a-ref", make_intent(), make_entry())  # type: ignore[arg-type]


def test_producer_mismatch_rejected():
    entry = make_entry(producer_agent_id="watch-other")
    intent = make_intent(producer_agent_id="watch-agent")
    with pytest.raises(SignalRefBridgeError, match=PRODUCER_MISMATCH):
        build_signal_ref(intent, entry)


# ----------------------------------------------------------------------
# SignalRefV1: cross-object time order (look-ahead / back-dated refusal)
# ----------------------------------------------------------------------

def test_signal_ref_trigger_after_prepared_refused():
    # A trigger instant later than the preparation instant is a fabricated
    # timeline: the adapter always prepares on/after the trigger.
    with pytest.raises(SignalRefBridgeError, match=REF_TIME_ORDER_INVALID):
        build_signal_ref(
            make_intent(trigger_at=NOW + timedelta(minutes=1)), make_entry())


def test_signal_ref_prepared_after_entry_created_refused():
    # The pool entry cannot exist before the intent was prepared.
    with pytest.raises(SignalRefBridgeError, match=REF_TIME_ORDER_INVALID):
        build_signal_ref(
            make_intent(prepared_at=NOW + timedelta(minutes=1)),
            make_entry())


def test_signal_ref_entry_created_before_trigger_refused():
    # The pool entry cannot predate the asserted trigger instant.
    with pytest.raises(SignalRefBridgeError, match=REF_TIME_ORDER_INVALID):
        build_signal_ref(
            make_intent(), make_entry(created_at=NOW - timedelta(minutes=1)))


# ----------------------------------------------------------------------
# digest is never a permission
# ----------------------------------------------------------------------

def test_digest_is_never_a_permission():
    entry = make_entry()
    intent = make_intent()
    signal_ref = build_signal_ref(intent, entry)
    inbox, report, receipt, lookup = make_real_acceptance()
    acceptance_ref = build_acceptance_ref(
        report, receipt, inbox_epoch=inbox.ledger_epoch,
        inbox_receipt_lookup=lookup)
    for ref in (signal_ref, acceptance_ref):
        assert ref.is_permission is False
        assert ref.production_permitted is False
        assert ref.notification_permitted is False
        assert ref.trading_permitted is False
        with pytest.raises(SignalRefBridgeError, match=NOT_A_PERMISSION):
            ref.permission_token()


# ----------------------------------------------------------------------
# AcceptanceRefV1: construction, determinism, authentic first epoch
# ----------------------------------------------------------------------

def test_first_acceptance_epoch_zero_from_real_inbox_is_authentic():
    """A real first receipt (epoch 0) is a legitimate positive.

    The previous fixture hard-wired epoch=3 and the bridge demanded
    epoch > 0, masking the absence of an authentic first-acceptance
    positive. The real UpstreamInbox starts at epoch 0 and its first
    accepted receipt keeps epoch 0.
    """
    inbox, report, receipt, lookup = make_real_acceptance()
    assert inbox.ledger_epoch == 0
    assert receipt.ledger_epoch == 0
    ref = build_acceptance_ref(
        report, receipt, inbox_epoch=inbox.ledger_epoch,
        inbox_receipt_lookup=lookup)
    assert ref.ledger_epoch == 0
    assert ref.accepted_at == NOW
    assert (ref.report_id, ref.receipt_id) == (report.report_id,
                                               receipt.receipt_id)
    assert (ref.receiver_agent_id, ref.sender_agent_id) == (
        "quant-agent", "watch-agent")
    assert ref.verification_source == VERIFICATION_SOURCE_UPSTREAM_INBOX_V1
    assert verify_acceptance_ref(
        ref, report, receipt, inbox_epoch=inbox.ledger_epoch,
        inbox_receipt_lookup=lookup) is ref


def test_acceptance_ref_binds_fields_and_is_deterministic():
    report = make_report()
    receipt, lookup = make_binding(report)
    ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                               inbox_receipt_lookup=lookup)
    again = build_acceptance_ref(report, receipt, inbox_epoch=3,
                                 inbox_receipt_lookup=lookup)
    assert ref == again
    assert ref.digest == again.digest
    assert len(ref.digest) == 64
    assert (ref.report_id, ref.receipt_id) == ("report-1", "receipt-1")
    assert (ref.receiver_agent_id, ref.ack_id) == ("quant-agent", "ack-1")
    assert ref.verification_source == VERIFICATION_SOURCE_UPSTREAM_INBOX_V1
    assert ref.ledger_epoch == 3 and ref.accepted_at == NOW
    assert verify_acceptance_ref(ref, report, receipt, inbox_epoch=3,
                                 inbox_receipt_lookup=lookup) is ref


def test_acceptance_epoch_binding_rejects_epoch_mismatch():
    report = make_report()
    receipt, lookup = make_binding(report)
    ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                               inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=EPOCH_MISMATCH):
        verify_acceptance_ref(ref, report, receipt, inbox_epoch=4,
                              inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=EPOCH_MISMATCH):
        build_acceptance_ref(report, receipt, inbox_epoch=2,
                             inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=INBOX_EPOCH_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch="3",  # type: ignore[arg-type]
                             inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=INBOX_EPOCH_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch=-1,
                             inbox_receipt_lookup=lookup)


def test_acceptance_negative_or_type_bad_ledger_epoch_rejected():
    """Only type-invalid or negative receipt epochs are refused.

    The former "epoch must be > 0" rule was reversed by review: epoch 0 is
    the authentic first-acceptance epoch of a fresh real inbox (see
    ``test_first_acceptance_epoch_zero_from_real_inbox_is_authentic``).
    Bad values are injected on a frozen receipt because the real inbox
    type itself refuses to construct them.
    """
    report = make_report()
    receipt, lookup = make_binding(report)
    set_frozen(receipt, "ledger_epoch", -1)
    with pytest.raises(SignalRefBridgeError, match=LEDGER_EPOCH_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch=0,
                             inbox_receipt_lookup=lookup)
    set_frozen(receipt, "ledger_epoch", "0")  # type: ignore[arg-type]
    with pytest.raises(SignalRefBridgeError, match=LEDGER_EPOCH_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch=0,
                             inbox_receipt_lookup=lookup)


def test_acceptance_tamper_matrix_rejects_every_field():
    report = make_report()
    receipt, lookup = make_binding(report)
    # (label, tampered receipt, expected code); each case builds a fresh
    # reference so no tampering leaks across cases. The lookup reports the
    # ORIGINAL receipt as the trusted current state; tampered receipts are
    # caught by the earlier binding gates before the state comparison.
    receipt_cases = [
        ("receipt.report_id",
         make_receipt(report, report_id="report-x",
                      ack_overrides={"report_id": "report-x"}),
         RECEIPT_REPORT_MISMATCH),
        ("receipt.envelope_hash",
         make_receipt(report, envelope_hash="c" * 64), ENVELOPE_MISMATCH),
        ("report evidence (envelope drift)",
         make_receipt(make_report(event_id="event-x")), ENVELOPE_MISMATCH),
        ("receipt.sender_agent_id",
         make_receipt(report, sender_agent_id="watch-x"), SENDER_MISMATCH),
        ("ack.receiver_agent_id",
         make_receipt(report,
                      ack_overrides={"receiver_agent_id": "quant-x"}),
         ACK_RECEIVER_MISMATCH),
        ("ack.verification_source",
         make_receipt(report,
                      ack_overrides={"verification_source": "self-claimed"}),
         VERIFICATION_SOURCE_MISMATCH),
    ]
    for label, tampered_receipt, code in receipt_cases:
        fresh_ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                                         inbox_receipt_lookup=lookup)
        with pytest.raises(SignalRefBridgeError, match=code) as excinfo:
            verify_acceptance_ref(fresh_ref, report, tampered_receipt,
                                  inbox_epoch=3, inbox_receipt_lookup=lookup)
        assert excinfo.value.args[0] == code, label
    # Tampering the ACK message binding (report_id inside the ack slot) is
    # caught by the bridge even though the real inbox type would have
    # refused to construct such a receipt in the first place.
    fresh_ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                                     inbox_receipt_lookup=lookup)
    tampered_receipt = make_receipt(report)
    set_frozen(tampered_receipt, "ack",
               dataclasses.replace(tampered_receipt.ack, report_id="report-x"))
    with pytest.raises(SignalRefBridgeError, match=ACK_REPORT_MISMATCH):
        verify_acceptance_ref(fresh_ref, report, tampered_receipt,
                              inbox_epoch=3, inbox_receipt_lookup=lookup)
    # Tampering the reference's own fields (including a per-field check on
    # the bound receiver) is caught by the field-by-field comparison.
    tampered_ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                                        inbox_receipt_lookup=lookup)
    set_frozen(tampered_ref, "report_id", "report-x")
    with pytest.raises(SignalRefBridgeError, match=REF_FIELD_MISMATCH):
        verify_acceptance_ref(tampered_ref, report, receipt, inbox_epoch=3,
                              inbox_receipt_lookup=lookup)


def test_acceptance_digest_recompute_mismatch_rejected():
    report = make_report()
    receipt, lookup = make_binding(report)
    ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                               inbox_receipt_lookup=lookup)
    set_frozen(ref, "digest", "f" * 64)
    with pytest.raises(SignalRefBridgeError, match=DIGEST_MISMATCH):
        verify_acceptance_ref(ref, report, receipt, inbox_epoch=3,
                              inbox_receipt_lookup=lookup)


def test_acceptance_wrong_types_and_missing_objects_fail_closed():
    report = make_report()
    receipt, lookup = make_binding(report)
    with pytest.raises(SignalRefBridgeError, match=REPORT_TYPE_INVALID):
        build_acceptance_ref("not-a-report", receipt, inbox_epoch=3,  # type: ignore[arg-type]
                             inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_TYPE_INVALID):
        build_acceptance_ref(report, "not-a-receipt", inbox_epoch=3,  # type: ignore[arg-type]
                             inbox_receipt_lookup=lookup)
    ref = build_acceptance_ref(report, receipt, inbox_epoch=3,
                               inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=REF_OBJECT_MISSING):
        verify_acceptance_ref(ref, None, receipt, inbox_epoch=3,  # type: ignore[arg-type]
                              inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=REF_TYPE_INVALID):
        verify_acceptance_ref("not-a-ref", report, receipt, inbox_epoch=3,  # type: ignore[arg-type]
                              inbox_receipt_lookup=lookup)


def test_acceptance_ref_ack_type_gate():
    # A receipt whose ack slot is not a real UpstreamAck is refused.
    report = make_report()
    receipt, lookup = make_binding(report)
    set_frozen(receipt, "ack", "not-an-ack")
    with pytest.raises(SignalRefBridgeError, match=ACK_TYPE_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=lookup)


def test_acceptance_ref_health_report_is_bindable():
    # HEALTH is an upstream report kind, not a business signal; acceptance
    # binding (unlike the anomaly signal ref) admits it — from a REAL inbox.
    inbox, health_report, receipt, lookup = make_real_acceptance(
        report_kind="HEALTH")
    ref = build_acceptance_ref(
        health_report, receipt, inbox_epoch=inbox.ledger_epoch,
        inbox_receipt_lookup=lookup)
    assert ref.report_kind == "HEALTH"
    assert verify_acceptance_ref(
        ref, health_report, receipt, inbox_epoch=inbox.ledger_epoch,
        inbox_receipt_lookup=lookup) is ref


# ----------------------------------------------------------------------
# AcceptanceRefV1: trusted receipt-state gate (stale/revoked fail-closed)
# ----------------------------------------------------------------------

def test_revoked_receipt_fails_closed_after_real_revoke():
    """A receipt revoked in the real inbox can never be referenced again.

    The receipt's frozen ledger_epoch never changes on revoke, so epoch
    consistency alone cannot detect staleness; the trusted lookup bound to
    the real inbox reports the report as no longer active.
    """
    inbox, report, receipt, lookup = make_real_acceptance()
    ref = build_acceptance_ref(report, receipt, inbox_epoch=0,
                               inbox_receipt_lookup=lookup)
    inbox.revoke(receipt.receipt_id, now=NOW)
    assert inbox.ledger_epoch == 1
    assert receipt.ledger_epoch == 0  # frozen forever; staleness is invisible
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_NOT_ACTIVE):
        build_acceptance_ref(report, receipt, inbox_epoch=0,
                             inbox_receipt_lookup=lookup)
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_NOT_ACTIVE):
        verify_acceptance_ref(ref, report, receipt, inbox_epoch=0,
                              inbox_receipt_lookup=lookup)
    # Forwarding the advanced epoch cannot sneak the stale receipt through.
    with pytest.raises(SignalRefBridgeError, match=EPOCH_MISMATCH):
        build_acceptance_ref(report, receipt, inbox_epoch=inbox.ledger_epoch,
                             inbox_receipt_lookup=lookup)


def test_missing_or_broken_receipt_lookup_fails_closed():
    """A missing dependency must never silently allow a reference."""
    report = make_report()
    receipt = make_receipt(report)
    # The signature itself refuses an absent callback (fail-closed at the
    # Python layer before any bridge gate runs).
    with pytest.raises(TypeError):
        build_acceptance_ref(report, receipt, inbox_epoch=3)  # type: ignore[call-arg]
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_LOOKUP_MISSING):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=None)  # type: ignore[arg-type]
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_LOOKUP_MISSING):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup="not-callable")  # type: ignore[arg-type]

    def boom(report_id: str) -> UpstreamReceipt:
        raise RuntimeError("lookup backend down")

    with pytest.raises(SignalRefBridgeError, match=RECEIPT_NOT_ACTIVE):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=boom)
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_NOT_ACTIVE):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=lambda report_id: None)
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_MISMATCH):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=lambda report_id: "not-a-receipt")  # type: ignore[arg-type,return-value]


def test_lookup_state_disagreement_fails_closed():
    """The passed receipt must match the inbox's authoritative receipt."""
    inbox, report, receipt, lookup = make_real_acceptance()
    # A forged receipt binding the same report but with a different
    # receipt identity/timestamps passes every binding gate, then diverges
    # from the trusted current state.
    forged = make_receipt(report)
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_MISMATCH):
        build_acceptance_ref(report, forged, inbox_epoch=3,
                             inbox_receipt_lookup=lookup)
    # A lookup bound to a different report cannot vouch for this one.
    other_inbox, other_report, other_receipt, _ = make_real_acceptance(
        report_id="report-2")
    assert other_receipt != receipt
    with pytest.raises(SignalRefBridgeError, match=RECEIPT_MISMATCH):
        build_acceptance_ref(
            report, receipt, inbox_epoch=0,
            inbox_receipt_lookup=lambda report_id: other_receipt)


# ----------------------------------------------------------------------
# AcceptanceRefV1: cross-object time order (look-ahead refusal)
# ----------------------------------------------------------------------

def test_acceptance_time_order_acceptance_before_report_refused():
    # An acceptance instant preceding the report's creation is a look-ahead.
    # Injected on a frozen receipt: the real inbox type refuses to build it.
    report = make_report()
    receipt = make_receipt(report)
    set_frozen(receipt, "accepted_at", NOW - timedelta(minutes=1))
    with pytest.raises(SignalRefBridgeError, match=REF_TIME_ORDER_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=lambda report_id: receipt)


def test_acceptance_time_order_ack_before_acceptance_refused():
    # The ACK instant can never precede the acceptance instant.
    report = make_report()
    receipt = make_receipt(report)
    set_frozen(receipt, "accepted_at", NOW + timedelta(minutes=1))
    with pytest.raises(SignalRefBridgeError, match=REF_TIME_ORDER_INVALID):
        build_acceptance_ref(report, receipt, inbox_epoch=3,
                             inbox_receipt_lookup=lambda report_id: receipt)


def test_acceptance_time_order_from_real_inbox_holds():
    # Positive control: a real acceptance satisfies both temporal gates
    # (accepted_at == acked_at == report.created_at here) without error.
    inbox, report, receipt, lookup = make_real_acceptance()
    ref = build_acceptance_ref(report, receipt, inbox_epoch=0,
                               inbox_receipt_lookup=lookup)
    assert ref.accepted_at >= report.created_at
    assert receipt.ack.acked_at >= receipt.accepted_at


# ----------------------------------------------------------------------
# Pure offline: zero side effects on REAL ledgers
# ----------------------------------------------------------------------

def test_bridge_has_zero_side_effects_on_real_ledgers():
    """Build/verify must leave a real inbox and a real pool untouched.

    The former zero-side-effect test only inspected an empty inbox with no
    functions passed in; this proof snapshots the full state of a real
    UpstreamInbox (epoch, receipt list, per-receipt lookup) and a real
    SignalPool (open entries, bound entry, receipts, leases) before and
    after building and verifying both reference kinds.
    """
    # Real upstream inbox: full state snapshot before/after.
    inbox, report, receipt, lookup = make_real_acceptance()

    def inbox_state():
        return (inbox.ledger_epoch, inbox.list_accepted(),
                inbox.get(receipt.receipt_id))

    inbox_before = inbox_state()
    ref = build_acceptance_ref(report, receipt, inbox_epoch=inbox.ledger_epoch,
                               inbox_receipt_lookup=lookup)
    verify_acceptance_ref(ref, report, receipt,
                          inbox_epoch=inbox.ledger_epoch,
                          inbox_receipt_lookup=lookup)
    assert inbox_state() == inbox_before
    assert inbox.ledger_epoch == 0
    assert len(inbox.list_accepted()) == 1

    # Real signal pool with a really appended entry: state before/after.
    pool, intent, entry = make_real_pool_context()

    def pool_state():
        return (pool.list_open(at(5)), pool.get(SIG_ID), pool.receipt(SIG_ID),
                dict(pool._receipts), dict(pool._leases))

    pool_before = pool_state()
    signal_ref = build_signal_ref(intent, entry)
    verify_signal_ref(signal_ref, intent, entry)
    assert pool_state() == pool_before
    assert pool.get(SIG_ID) is entry
    assert entry.status == "OPEN" and entry.epoch == 0


# ----------------------------------------------------------------------
# Constant sentinels and digest domains
# ----------------------------------------------------------------------

def test_digest_domains_are_distinct():
    assert SIGNAL_REF_DIGEST_DOMAIN != ACCEPTANCE_REF_DIGEST_DOMAIN
    ref = build_signal_ref(make_intent(), make_entry())
    inbox, report, receipt, lookup = make_real_acceptance()
    acceptance = build_acceptance_ref(
        report, receipt, inbox_epoch=inbox.ledger_epoch,
        inbox_receipt_lookup=lookup)
    # Same evidence identity, different domains: digests cannot collide by
    # domain reuse (structural distinctness of the canonical payloads).
    assert ref.digest != acceptance.digest


def test_status_revoked_sentinel_matches_pool_constant():
    # Guard against silent drift of the literal revocation constant.
    assert STATUS_REVOKED_SENTINEL == STATUS_REVOKED
    assert STATUS_REVOKED_SENTINEL == "REVOKED"
