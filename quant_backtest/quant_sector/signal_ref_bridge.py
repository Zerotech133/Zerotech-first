"""Provenance bridge binding game-slice references to real upstream objects.

This module creates frozen, audit-only reference records (``SignalRefV1`` /
``AcceptanceRefV1``) that tie a G1 real signal-pool entry to its stock-pool
provenance, and a quant upstream report to its real inbox receipt. It is a
pure offline derivation layer: no I/O, no network, no mutation of any
upstream ledger (never calls ``SignalPool.append`` and never writes an
inbox), and identical inputs always yield identical outputs.

Fail-closed contract (error-code style mirrors
``quant_sector.internal_signal_fanout.InternalFanoutError``):

- Every builder only accepts the real upstream types. A reference is
  constructed exclusively from a real ``WatchSignalIntent`` plus its
  matching real ``SignalPoolEntry`` (or refuses a bare
  ``ControlledSignalScope``, which carries no pool snapshot identity), and
  from a real ``QuantUpstreamReport`` plus its real ``UpstreamReceipt``
  with the observed inbox epoch. Missing pool version, membership, security
  identity, or any mismatch refuses with an uppercase error code.
- Cross-object time order is enforced inside both builders: an intent is
  never prepared before its asserted trigger, and a pool entry is never
  created before the intent was prepared/triggered; an acceptance never
  precedes its report, and an ACK never precedes its acceptance. Every
  violation fails closed with ``REF_TIME_ORDER_INVALID``.
- ``build_acceptance_ref`` requires a mandatory trusted
  ``inbox_receipt_lookup(report_id)`` callback bound to the real
  ``UpstreamInbox``. A missing/non-callable callback, an unknown, revoked
  or inactive receipt, or a looked-up receipt that disagrees with the
  caller-supplied one fails closed (``RECEIPT_LOOKUP_MISSING`` /
  ``RECEIPT_NOT_ACTIVE`` / ``RECEIPT_MISMATCH``), so a stale or revoked
  receipt can never pass. Epoch 0 is the legitimate first-acceptance epoch
  of a fresh real inbox and is accepted when consistent with the trusted
  receipt state; only type-invalid or negative epochs are refused.
- Every verifier recomputes the digest from the bound objects and compares
  every field; any mismatch or missing object fails closed. Nothing is
  guessed, inferred, or degraded.

Digest semantics (binding): each reference carries a SHA-256 digest over a
canonical field string. It is an AUDIT REFERENCE ONLY — it is NOT a
signature, NOT a credential, NOT a permission token, and grants no
authority of any kind. ``is_permission`` is therefore always ``False`` and
``permission_token()`` always refuses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Callable

from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamAck,
    UpstreamReceipt,
    digest,
    utc_text,
)
from quant_backtest.watch_dispatch.signal_pool import (
    STATUS_REVOKED,
    ControlledSignalScope,
    SignalPoolEntry,
)
from quant_backtest.watch_dispatch.stock_pool_source import WatchSignalIntent

__all__ = [
    "ACK_RECEIVER_MISMATCH",
    "ACK_REPORT_MISMATCH",
    "ACK_TYPE_INVALID",
    "BUSINESS_SIGNAL_REQUIRED",
    "DIGEST_MISMATCH",
    "ENVELOPE_MISMATCH",
    "ENTRY_TYPE_INVALID",
    "EPOCH_MISMATCH",
    "EXECUTION_FORBIDDEN",
    "INBOX_EPOCH_INVALID",
    "INTENT_TYPE_INVALID",
    "LEDGER_EPOCH_INVALID",
    "MEMBERSHIP_MISSING",
    "NOT_A_PERMISSION",
    "POOL_IDENTITY_MISSING",
    "POOL_VERSION_MISSING",
    "PRODUCER_MISMATCH",
    "RECEIPT_LOOKUP_MISSING",
    "RECEIPT_MISMATCH",
    "RECEIPT_NOT_ACTIVE",
    "RECEIPT_REPORT_MISMATCH",
    "RECEIPT_TYPE_INVALID",
    "REF_FIELD_MISMATCH",
    "REF_OBJECT_MISSING",
    "REF_TIME_ORDER_INVALID",
    "REF_TYPE_INVALID",
    "REPORT_TYPE_INVALID",
    "REVOKED_REFUSED",
    "RULE_IDENTITY_MISSING",
    "SCOPE_POOL_IDENTITY_UNAVAILABLE",
    "SECURITY_IDENTITY_MISSING",
    "SENDER_MISMATCH",
    "SIGNAL_REF_DIGEST_DOMAIN",
    "ACCEPTANCE_REF_DIGEST_DOMAIN",
    "STATUS_REVOKED_SENTINEL",
    "VERIFICATION_SOURCE_MISMATCH",
    "VERIFICATION_SOURCE_UPSTREAM_INBOX_V1",
    "AcceptanceRefV1",
    "SignalRefBridgeError",
    "SignalRefV1",
    "build_acceptance_ref",
    "build_signal_ref",
    "verify_acceptance_ref",
    "verify_signal_ref",
]

# Fail-closed error codes (uppercase constants; message carries no details
# beyond the code, mirroring the internal fanout discipline).
ACK_RECEIVER_MISMATCH = "ACK_RECEIVER_MISMATCH"
ACK_REPORT_MISMATCH = "ACK_REPORT_MISMATCH"
ACK_TYPE_INVALID = "ACK_TYPE_INVALID"
BUSINESS_SIGNAL_REQUIRED = "BUSINESS_SIGNAL_REQUIRED"
DIGEST_MISMATCH = "DIGEST_MISMATCH"
ENVELOPE_MISMATCH = "ENVELOPE_MISMATCH"
ENTRY_TYPE_INVALID = "ENTRY_TYPE_INVALID"
EPOCH_MISMATCH = "EPOCH_MISMATCH"
EXECUTION_FORBIDDEN = "EXECUTION_FORBIDDEN"
INBOX_EPOCH_INVALID = "INBOX_EPOCH_INVALID"
INTENT_TYPE_INVALID = "INTENT_TYPE_INVALID"
LEDGER_EPOCH_INVALID = "LEDGER_EPOCH_INVALID"
MEMBERSHIP_MISSING = "MEMBERSHIP_MISSING"
NOT_A_PERMISSION = "NOT_A_PERMISSION"
POOL_IDENTITY_MISSING = "POOL_IDENTITY_MISSING"
POOL_VERSION_MISSING = "POOL_VERSION_MISSING"
PRODUCER_MISMATCH = "PRODUCER_MISMATCH"
RECEIPT_LOOKUP_MISSING = "RECEIPT_LOOKUP_MISSING"
RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
RECEIPT_NOT_ACTIVE = "RECEIPT_NOT_ACTIVE"
RECEIPT_REPORT_MISMATCH = "RECEIPT_REPORT_MISMATCH"
RECEIPT_TYPE_INVALID = "RECEIPT_TYPE_INVALID"
REF_FIELD_MISMATCH = "REF_FIELD_MISMATCH"
REF_OBJECT_MISSING = "REF_OBJECT_MISSING"
REF_TIME_ORDER_INVALID = "REF_TIME_ORDER_INVALID"
REF_TYPE_INVALID = "REF_TYPE_INVALID"
REPORT_TYPE_INVALID = "REPORT_TYPE_INVALID"
REVOKED_REFUSED = "REVOKED_REFUSED"
RULE_IDENTITY_MISSING = "RULE_IDENTITY_MISSING"
SCOPE_POOL_IDENTITY_UNAVAILABLE = "SCOPE_POOL_IDENTITY_UNAVAILABLE"
SECURITY_IDENTITY_MISSING = "SECURITY_IDENTITY_MISSING"
SENDER_MISMATCH = "SENDER_MISMATCH"
VERIFICATION_SOURCE_MISMATCH = "VERIFICATION_SOURCE_MISMATCH"

# The literal revocation status this bridge refuses to reference. Stated
# as a literal (not imported) so a silent upstream constant drift is caught
# by the drift-guard test comparing it to signal_pool.STATUS_REVOKED.
STATUS_REVOKED_SENTINEL = "REVOKED"

# Digest domains: stable literal prefixes distinguishing the two reference
# kinds inside the canonical digest payload.
SIGNAL_REF_DIGEST_DOMAIN = "game-signal-ref-v1"
ACCEPTANCE_REF_DIGEST_DOMAIN = "game-acceptance-ref-v1"

# The only ACK verification source this bridge accepts (literal mirror of
# quant_sector.upstream_inbox; deliberately re-stated, not imported as a
# private symbol).
VERIFICATION_SOURCE_UPSTREAM_INBOX_V1 = "upstream-inbox-v1"

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MARKET = re.compile(r"[A-Z][A-Z0-9_]{0,15}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,31}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class SignalRefBridgeError(ValueError):
    """Fail-closed signal-reference bridge violation; message is a code."""


def _label(value: object, field: str, code: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise SignalRefBridgeError(code)


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise SignalRefBridgeError(field + "_INVALID")
    try:
        utc_text(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise SignalRefBridgeError(field + "_INVALID") from exc


def _positive_int(value: object, code: str) -> None:
    if type(value) is not int or value < 1:
        raise SignalRefBridgeError(code)


def _sha_hex(value: object, code: str) -> None:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise SignalRefBridgeError(code)


@dataclass(frozen=True)
class SignalRefV1:
    """Frozen provenance reference for one G1 signal-pool entry.

    Binds the entry's independent ``signal_id`` and ``event_id`` (the two
    are separate, independently validated fields; neither is ever derived
    from the other) to the producer, evidence envelope identity, and the
    exact stock-pool snapshot identity (pool id/version, membership,
    market/symbol, rule identity) it was drafted against. ``digest`` is a
    SHA-256 audit reference over the canonical field string — NOT a
    signature, NOT a credential, NOT a permission token.
    """

    signal_id: str
    event_id: str
    producer_agent_id: str
    report_kind: str
    evidence_hash: str
    evidence_version: int
    pool_id: str
    pool_version: int
    membership_id: str
    market: str
    symbol: str
    rule_id: str
    rule_version: int
    trigger_at: datetime
    created_at: datetime
    digest: str = ""

    @property
    def is_permission(self) -> bool:
        """Always False: the digest and this object authorize nothing."""
        return False

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def permission_token(self) -> str:
        """Explicit refusal: this reference can never become a permission."""
        raise SignalRefBridgeError(NOT_A_PERMISSION)

    def __post_init__(self) -> None:
        _label(self.signal_id, "SIGNAL_ID", "SIGNAL_ID_INVALID")
        _label(self.event_id, "EVENT_ID", "EVENT_ID_INVALID")
        _label(self.producer_agent_id, "PRODUCER_AGENT_ID",
               "PRODUCER_AGENT_ID_INVALID")
        if self.report_kind != "ANOMALY" or type(self.report_kind) is not str:
            raise SignalRefBridgeError(BUSINESS_SIGNAL_REQUIRED)
        _sha_hex(self.evidence_hash, "EVIDENCE_HASH_INVALID")
        _positive_int(self.evidence_version, "EVIDENCE_VERSION_INVALID")
        _label(self.pool_id, "POOL_ID", "POOL_ID_INVALID")
        _positive_int(self.pool_version, "POOL_VERSION_INVALID")
        _label(self.membership_id, "MEMBERSHIP_ID", "MEMBERSHIP_ID_INVALID")
        if type(self.market) is not str or _MARKET.fullmatch(self.market) is None:
            raise SignalRefBridgeError("MARKET_INVALID")
        if type(self.symbol) is not str or _SYMBOL.fullmatch(self.symbol) is None:
            raise SignalRefBridgeError("SYMBOL_INVALID")
        _label(self.rule_id, "RULE_ID", "RULE_ID_INVALID")
        _positive_int(self.rule_version, "RULE_VERSION_INVALID")
        _utc(self.trigger_at, "TRIGGER_AT")
        _utc(self.created_at, "CREATED_AT")
        object.__setattr__(self, "digest", _signal_ref_digest(self))


@dataclass(frozen=True)
class AcceptanceRefV1:
    """Frozen provenance reference for one upstream inbox acceptance.

    Binds the report id, the real receipt id, the ACK binding (receiver
    agent and ack message id), the ledger epoch, and the admission instant.
    ``digest`` is a SHA-256 audit reference — NOT a signature, NOT a
    credential, NOT a permission token.
    """

    report_id: str
    receipt_id: str
    report_kind: str
    event_id: str
    evidence_hash: str
    evidence_version: int
    sender_agent_id: str
    receiver_agent_id: str
    ack_id: str
    verification_source: str
    ledger_epoch: int
    accepted_at: datetime
    digest: str = ""

    @property
    def is_permission(self) -> bool:
        """Always False: the digest and this object authorize nothing."""
        return False

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def permission_token(self) -> str:
        """Explicit refusal: this reference can never become a permission."""
        raise SignalRefBridgeError(NOT_A_PERMISSION)

    def __post_init__(self) -> None:
        _label(self.report_id, "REPORT_ID", "REPORT_ID_INVALID")
        _label(self.receipt_id, "RECEIPT_ID", "RECEIPT_ID_INVALID")
        if type(self.report_kind) is not str or self.report_kind not in (
                "ANOMALY", "HEALTH"):
            raise SignalRefBridgeError("REPORT_KIND_INVALID")
        _label(self.event_id, "EVENT_ID", "EVENT_ID_INVALID")
        _sha_hex(self.evidence_hash, "EVIDENCE_HASH_INVALID")
        _positive_int(self.evidence_version, "EVIDENCE_VERSION_INVALID")
        _label(self.sender_agent_id, "SENDER_AGENT_ID",
               "SENDER_AGENT_ID_INVALID")
        _label(self.receiver_agent_id, "RECEIVER_AGENT_ID",
               "RECEIVER_AGENT_ID_INVALID")
        _label(self.ack_id, "ACK_ID", "ACK_ID_INVALID")
        _label(self.verification_source, "VERIFICATION_SOURCE",
               "VERIFICATION_SOURCE_INVALID")
        if type(self.ledger_epoch) is not int or self.ledger_epoch < 0:
            raise SignalRefBridgeError("LEDGER_EPOCH_INVALID")
        _utc(self.accepted_at, "ACCEPTED_AT")
        object.__setattr__(self, "digest", _acceptance_ref_digest(self))


def _signal_ref_digest(ref: SignalRefV1) -> str:
    """SHA-256 over the canonical field string; audit reference only."""
    return digest([
        SIGNAL_REF_DIGEST_DOMAIN,
        ref.signal_id,
        ref.event_id,
        ref.producer_agent_id,
        ref.report_kind,
        ref.evidence_hash,
        ref.evidence_version,
        ref.pool_id,
        ref.pool_version,
        ref.membership_id,
        ref.market,
        ref.symbol,
        ref.rule_id,
        ref.rule_version,
        utc_text(ref.trigger_at),
        utc_text(ref.created_at),
    ])


def _acceptance_ref_digest(ref: AcceptanceRefV1) -> str:
    """SHA-256 over the canonical field string; audit reference only."""
    return digest([
        ACCEPTANCE_REF_DIGEST_DOMAIN,
        ref.report_id,
        ref.receipt_id,
        ref.report_kind,
        ref.event_id,
        ref.evidence_hash,
        ref.evidence_version,
        ref.sender_agent_id,
        ref.receiver_agent_id,
        ref.ack_id,
        ref.verification_source,
        ref.ledger_epoch,
        utc_text(ref.accepted_at),
    ])


def _compare(ref: Any, rebuilt: Any, digest_code: str) -> None:
    """Field-by-field comparison plus explicit digest recomputation check."""
    for field in fields(rebuilt):
        if field.name == "digest":
            continue
        if getattr(ref, field.name) != getattr(rebuilt, field.name):
            raise SignalRefBridgeError(REF_FIELD_MISMATCH)
    if ref.digest != rebuilt.digest:
        raise SignalRefBridgeError(digest_code)


def build_signal_ref(intent: WatchSignalIntent,
                     entry: SignalPoolEntry) -> SignalRefV1:
    """Build a ``SignalRefV1`` from a real intent and its real pool entry.

    Fails closed when the objects are of the wrong type, disagree on any
    bound field, lack pool version / membership / security identity, carry
    a revoked entry, are not an ANOMALY business signal, or violate the
    cross-object time order (``trigger_at <= prepared_at <=
    entry.created_at`` — the intent is prepared on/after its asserted
    trigger, and the pool entry is only appended afterwards;
    ``REF_TIME_ORDER_INVALID``). A bare ``ControlledSignalScope`` is
    refused because it carries no pool snapshot identity. Pure derivation:
    no upstream ledger is touched.
    """
    if isinstance(intent, ControlledSignalScope):
        # A scope is a selector, not a pool snapshot; pool version,
        # membership and security identity cannot come from it.
        raise SignalRefBridgeError(SCOPE_POOL_IDENTITY_UNAVAILABLE)
    if type(intent) is not WatchSignalIntent:
        raise SignalRefBridgeError(INTENT_TYPE_INVALID)
    if type(entry) is not SignalPoolEntry:
        raise SignalRefBridgeError(ENTRY_TYPE_INVALID)
    if (entry.signal_id, entry.report_kind, entry.event_id,
            entry.evidence_hash, entry.evidence_version) != (
            intent.draft.signal_id, intent.draft.report_kind,
            intent.draft.event_id, intent.draft.evidence_hash,
            intent.draft.evidence_version):
        raise SignalRefBridgeError(REF_FIELD_MISMATCH)
    if entry.producer_agent_id != intent.producer_agent_id:
        raise SignalRefBridgeError(PRODUCER_MISMATCH)
    if entry.status == STATUS_REVOKED or entry.status == STATUS_REVOKED_SENTINEL:
        raise SignalRefBridgeError(REVOKED_REFUSED)
    if entry.report_kind != "ANOMALY":
        # Only ANOMALY enters business signal references (HEALTH is an
        # upstream health report, mirroring internal_signal_fanout).
        raise SignalRefBridgeError(BUSINESS_SIGNAL_REQUIRED)
    _label(intent.pool_id, "POOL_ID", POOL_IDENTITY_MISSING)
    _positive_int(intent.pool_version, POOL_VERSION_MISSING)
    _label(intent.membership_id, "MEMBERSHIP_ID", MEMBERSHIP_MISSING)
    if type(intent.market) is not str or _MARKET.fullmatch(intent.market) is None:
        raise SignalRefBridgeError(SECURITY_IDENTITY_MISSING)
    if type(intent.symbol) is not str or _SYMBOL.fullmatch(intent.symbol) is None:
        raise SignalRefBridgeError(SECURITY_IDENTITY_MISSING)
    _label(intent.rule_id, "RULE_ID", RULE_IDENTITY_MISSING)
    _positive_int(intent.rule_version, RULE_IDENTITY_MISSING)
    _utc(intent.trigger_at, "TRIGGER_AT")
    _utc(intent.prepared_at, "PREPARED_AT")
    _utc(entry.created_at, "CREATED_AT")
    # Cross-object temporal gate (fail-closed): the intent must not be
    # prepared before its asserted trigger, and the pool entry must not be
    # created before the intent was prepared or triggered. Equal instants
    # are legitimate (the adapter prepares on the trigger instant).
    if (intent.trigger_at > intent.prepared_at
            or intent.prepared_at > entry.created_at
            or intent.trigger_at > entry.created_at):
        raise SignalRefBridgeError(REF_TIME_ORDER_INVALID)
    return SignalRefV1(
        signal_id=entry.signal_id,
        event_id=entry.event_id,
        producer_agent_id=entry.producer_agent_id,
        report_kind=entry.report_kind,
        evidence_hash=entry.evidence_hash,
        evidence_version=entry.evidence_version,
        pool_id=intent.pool_id,
        pool_version=intent.pool_version,
        membership_id=intent.membership_id,
        market=intent.market,
        symbol=intent.symbol,
        rule_id=intent.rule_id,
        rule_version=intent.rule_version,
        trigger_at=intent.trigger_at,
        created_at=entry.created_at,
    )


def verify_signal_ref(ref: SignalRefV1, intent: WatchSignalIntent,
                      entry: SignalPoolEntry) -> SignalRefV1:
    """Recompute the digest and compare every field against the bound pair.

    Fails closed on missing objects, wrong types, any field mismatch, or a
    digest that no longer matches the bound objects. Returns the reference
    unchanged on success.
    """
    if ref is None or intent is None or entry is None:
        raise SignalRefBridgeError(REF_OBJECT_MISSING)
    if type(ref) is not SignalRefV1:
        raise SignalRefBridgeError(REF_TYPE_INVALID)
    rebuilt = build_signal_ref(intent, entry)
    _compare(ref, rebuilt, DIGEST_MISMATCH)
    return ref


def build_acceptance_ref(
    report: QuantUpstreamReport, receipt: UpstreamReceipt, *,
    inbox_epoch: int,
    inbox_receipt_lookup: Callable[[str], "UpstreamReceipt | None"],
) -> AcceptanceRefV1:
    """Build an ``AcceptanceRefV1`` from a real report/receipt/epoch triple.

    Fails closed when:

    - the receipt does not bind this exact report (report id, envelope
      hash, sender), the ACK does not bind the report (report id,
      receiver) with ``upstream-inbox-v1`` verification, or the report is
      executable;
    - the observed ``inbox_epoch`` is not a non-negative int or disagrees
      with the receipt's ledger epoch. Epoch 0 is the legitimate
      first-acceptance epoch of a fresh real ``UpstreamInbox`` and is
      accepted when consistent; only type-invalid or negative values are
      refused (``INBOX_EPOCH_INVALID`` / ``LEDGER_EPOCH_INVALID``);
    - cross-object time order is violated: the acceptance instant must not
      precede the report's creation and the ACK instant must not precede
      the acceptance instant (``REF_TIME_ORDER_INVALID``);
    - the mandatory trusted ``inbox_receipt_lookup(report_id)`` callback is
      missing or not callable (``RECEIPT_LOOKUP_MISSING``), reports the
      receipt unknown / revoked / inactive, or itself fails
      (``RECEIPT_NOT_ACTIVE``), or returns a receipt (or non-receipt)
      whose fields disagree with the caller-supplied receipt
      (``RECEIPT_MISMATCH``). The callback is the single trusted
      dependency and must be bound to the real ``UpstreamInbox`` (e.g.
      ``receipt_for_report`` combined with ``is_revoked``) so a stale or
      revoked receipt can never pass.

    Pure derivation: the inbox ledger is never written.
    """
    if type(report) is not QuantUpstreamReport:
        raise SignalRefBridgeError(REPORT_TYPE_INVALID)
    if type(receipt) is not UpstreamReceipt:
        raise SignalRefBridgeError(RECEIPT_TYPE_INVALID)
    if type(receipt.ack) is not UpstreamAck:
        raise SignalRefBridgeError(ACK_TYPE_INVALID)
    if receipt.report_id != report.report_id:
        raise SignalRefBridgeError(RECEIPT_REPORT_MISMATCH)
    if receipt.envelope_hash != report.envelope_hash:
        raise SignalRefBridgeError(ENVELOPE_MISMATCH)
    if receipt.sender_agent_id != report.sender_agent_id:
        raise SignalRefBridgeError(SENDER_MISMATCH)
    if receipt.ack.report_id != report.report_id:
        raise SignalRefBridgeError(ACK_REPORT_MISMATCH)
    if receipt.ack.receiver_agent_id != report.receiver_agent_id:
        raise SignalRefBridgeError(ACK_RECEIVER_MISMATCH)
    if receipt.ack.verification_source != VERIFICATION_SOURCE_UPSTREAM_INBOX_V1:
        raise SignalRefBridgeError(VERIFICATION_SOURCE_MISMATCH)
    if report.non_executable is not True:
        raise SignalRefBridgeError(EXECUTION_FORBIDDEN)
    if type(inbox_epoch) is not int or inbox_epoch < 0:
        raise SignalRefBridgeError(INBOX_EPOCH_INVALID)
    # Epoch 0 is the legitimate first-acceptance epoch of a fresh real
    # inbox; only type-invalid or negative values are refused here. The
    # epoch's authenticity is established by consistency with the trusted
    # receipt-state lookup below.
    if type(receipt.ledger_epoch) is not int or receipt.ledger_epoch < 0:
        raise SignalRefBridgeError(LEDGER_EPOCH_INVALID)
    if receipt.ledger_epoch != inbox_epoch:
        raise SignalRefBridgeError(EPOCH_MISMATCH)
    # Cross-object temporal gates (fail-closed): the report must exist
    # before it is accepted, and the ACK can never precede acceptance.
    _utc(report.created_at, "REPORT_CREATED_AT")
    _utc(receipt.accepted_at, "ACCEPTED_AT")
    _utc(receipt.ack.acked_at, "ACKED_AT")
    if (receipt.accepted_at < report.created_at
            or receipt.ack.acked_at < receipt.accepted_at):
        raise SignalRefBridgeError(REF_TIME_ORDER_INVALID)
    # Trusted receipt-state gate: without a callback bound to the real
    # inbox the reference is refused, so a stale or revoked receipt can
    # never pass. No missing-dependency silent allowance exists.
    if not callable(inbox_receipt_lookup):
        raise SignalRefBridgeError(RECEIPT_LOOKUP_MISSING)
    try:
        current = inbox_receipt_lookup(report.report_id)
    except Exception as exc:
        raise SignalRefBridgeError(RECEIPT_NOT_ACTIVE) from exc
    if current is None:
        raise SignalRefBridgeError(RECEIPT_NOT_ACTIVE)
    if type(current) is not UpstreamReceipt:
        raise SignalRefBridgeError(RECEIPT_MISMATCH)
    if (current.receipt_id, current.report_id, current.envelope_hash,
            current.sender_agent_id, current.accepted_at, current.ack,
            current.ledger_epoch) != (
            receipt.receipt_id, receipt.report_id, receipt.envelope_hash,
            receipt.sender_agent_id, receipt.accepted_at, receipt.ack,
            receipt.ledger_epoch):
        raise SignalRefBridgeError(RECEIPT_MISMATCH)
    return AcceptanceRefV1(
        report_id=report.report_id,
        receipt_id=receipt.receipt_id,
        report_kind=report.report_kind,
        event_id=report.event_id,
        evidence_hash=report.evidence_hash,
        evidence_version=report.evidence_version,
        sender_agent_id=report.sender_agent_id,
        receiver_agent_id=report.receiver_agent_id,
        ack_id=receipt.ack.ack_id,
        verification_source=receipt.ack.verification_source,
        ledger_epoch=receipt.ledger_epoch,
        accepted_at=receipt.accepted_at,
    )


def verify_acceptance_ref(
    ref: AcceptanceRefV1, report: QuantUpstreamReport,
    receipt: UpstreamReceipt, *,
    inbox_epoch: int,
    inbox_receipt_lookup: Callable[[str], "UpstreamReceipt | None"],
) -> AcceptanceRefV1:
    """Recompute the digest and compare every field against the bound pair.

    Runs the same fail-closed gates as ``build_acceptance_ref`` (types,
    binding, epoch consistency, cross-object time order, and the mandatory
    trusted ``inbox_receipt_lookup`` receipt-state gate), then fails closed
    on any field mismatch or a digest that no longer matches the bound
    objects. Returns the reference unchanged on success.
    """
    if ref is None or report is None or receipt is None:
        raise SignalRefBridgeError(REF_OBJECT_MISSING)
    if type(ref) is not AcceptanceRefV1:
        raise SignalRefBridgeError(REF_TYPE_INVALID)
    rebuilt = build_acceptance_ref(
        report, receipt, inbox_epoch=inbox_epoch,
        inbox_receipt_lookup=inbox_receipt_lookup)
    _compare(ref, rebuilt, DIGEST_MISMATCH)
    return ref
