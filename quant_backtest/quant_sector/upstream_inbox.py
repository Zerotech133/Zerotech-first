"""Quant-sector upstream inbox: accept watch reports, issue agent ACKs.

W-Q1 approved scope. The watch agent reports upward (ANOMALY / HEALTH) to
the quant sector's registered receiving agent; this inbox is the sector's
own acceptance ledger. It issues a locally verified agent-shaped ACK per
accepted report and never forwards anything itself.

Design rules (fail-closed everywhere):
- Identical re-acceptance of the same (report_id, envelope_hash) returns
  the exact same receipt and ACK (idempotent).
- Same report_id with a different envelope_hash is a hard conflict
  (RECEIPT_CONFLICT) - never a silent overwrite.
- The ledger is append-only; revoke only appends a revocation record and
  advances the ledger epoch. The epoch never moves backward. A revoked
  report_id can never be accepted again (RECEIPT_REVOKED).
- All timestamps must be timezone-aware UTC (offset zero); naive times are
  rejected.
- acked_at >= accepted_at always; verification_source is mandatory.

Offline only: no network, no persistence, no execution semantics. No
object here carries any trading permission concept.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = frozenset(("ANOMALY", "HEALTH"))
_VERIFICATION_SOURCE = "upstream-inbox-v1"


class UpstreamInboxError(ValueError):
    """Fail-closed upstream intake violation."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise UpstreamInboxError(field + "_INVALID")


def canonical_bytes(value: Any) -> bytes:
    """Strict canonical JSON encoding; only unambiguous values allowed."""
    def check(item: Any) -> Any:
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is list:
            return [check(child) for child in item]
        if type(item) is dict:
            if not all(type(key) is str for key in item):
                raise UpstreamInboxError("JSON_KEY_INVALID")
            return {key: check(child) for key, child in item.items()}
        raise UpstreamInboxError("JSON_VALUE_INVALID")

    return json.dumps(check(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    """Content digest only; never a signature, credential or permission."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def utc_text(value: datetime) -> str:
    """Canonical UTC text; naive or non-zero-offset times are rejected."""
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC_REQUIRED")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc(value: object, field: str) -> None:
    try:
        utc_text(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise UpstreamInboxError(field + "_INVALID") from exc


@dataclass(frozen=True)
class QuantUpstreamReport:
    """Immutable upward report from the watch agent; never executable.

    Field shape mirrors the watch-side AgentReport but is defined locally:
    this package does not import watch_dispatch.
    """

    report_id: str
    report_kind: str
    event_id: str
    evidence_hash: str
    evidence_version: int
    sender_agent_id: str
    receiver_agent_id: str
    created_at: datetime
    non_executable: bool = True

    def payload(self) -> Dict[str, object]:
        """Canonical payload used to derive the report's envelope hash."""
        return {
            "kind": "quant-upstream-report-v1",
            "report_id": self.report_id,
            "report_kind": self.report_kind,
            "event_id": self.event_id,
            "evidence_hash": self.evidence_hash,
            "evidence_version": self.evidence_version,
            "sender_agent_id": self.sender_agent_id,
            "receiver_agent_id": self.receiver_agent_id,
            "created_at": utc_text(self.created_at),
            "non_executable": self.non_executable,
        }

    @property
    def envelope_hash(self) -> str:
        """Deterministic content hash binding this exact report."""
        return digest(self.payload())

    def __post_init__(self) -> None:
        if self.non_executable is not True:
            raise UpstreamInboxError("EXECUTION_FORBIDDEN")
        _label(self.report_id, "REPORT_ID")
        if type(self.report_kind) is not str or self.report_kind not in _KINDS:
            raise UpstreamInboxError("REPORT_KIND_INVALID")
        _label(self.event_id, "EVENT_ID")
        if (type(self.evidence_hash) is not str
                or _SHA.fullmatch(self.evidence_hash) is None):
            raise UpstreamInboxError("EVIDENCE_HASH_INVALID")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise UpstreamInboxError("VERSION_INVALID")
        _label(self.sender_agent_id, "SENDER_AGENT_ID")
        _label(self.receiver_agent_id, "RECEIVER_AGENT_ID")
        if self.sender_agent_id == self.receiver_agent_id:
            raise UpstreamInboxError("AGENT_SELF_REPORT_FORBIDDEN")
        _utc(self.created_at, "REPORT_CREATED_AT")


@dataclass(frozen=True)
class UpstreamAck:
    """Agent-shaped acknowledgement issued locally by the inbox.

    Field shape mirrors the watch-side AgentAck (ack_id, report_id,
    receiver_agent_id, acked_at, verification_source) without importing it.
    """

    ack_id: str
    report_id: str
    receiver_agent_id: str
    acked_at: datetime
    verification_source: str

    def __post_init__(self) -> None:
        for name in ("ack_id", "report_id", "receiver_agent_id",
                     "verification_source"):
            _label(getattr(self, name), name.upper())
        _utc(self.acked_at, "ACKED_AT")


@dataclass(frozen=True)
class UpstreamReceipt:
    """Immutable acceptance record binding a report to its local ACK."""

    receipt_id: str
    report_id: str
    envelope_hash: str
    sender_agent_id: str
    accepted_at: datetime
    ack: UpstreamAck
    ledger_epoch: int

    def __post_init__(self) -> None:
        _label(self.receipt_id, "RECEIPT_ID")
        _label(self.report_id, "REPORT_ID")
        if (type(self.envelope_hash) is not str
                or _SHA.fullmatch(self.envelope_hash) is None):
            raise UpstreamInboxError("ENVELOPE_HASH_INVALID")
        _label(self.sender_agent_id, "SENDER_AGENT_ID")
        _utc(self.accepted_at, "ACCEPTED_AT")
        if type(self.ack) is not UpstreamAck:
            raise UpstreamInboxError("ACK_INVALID")
        if type(self.ledger_epoch) is not int or self.ledger_epoch < 0:
            raise UpstreamInboxError("EPOCH_INVALID")
        if self.ack.report_id != self.report_id:
            raise UpstreamInboxError("ACK_REPORT_MISMATCH")
        if self.ack.acked_at < self.accepted_at:
            raise UpstreamInboxError("ACK_TIME_INVALID")


class UpstreamInbox:
    """Append-only acceptance ledger; identical re-acceptance is idempotent."""

    def __init__(self) -> None:
        self._reports: Dict[str, QuantUpstreamReport] = {}
        self._receipts: Dict[str, UpstreamReceipt] = {}
        self._by_report: Dict[str, str] = {}
        self._revoked: Dict[str, int] = {}
        self._epoch: int = 0

    @property
    def ledger_epoch(self) -> int:
        """Current control epoch; only revoke() advances it."""
        return self._epoch

    def accept(self, report: QuantUpstreamReport, *,
               now: datetime) -> UpstreamReceipt:
        """Accept one upstream report and issue the local ACK.

        Idempotent for identical (report_id, envelope_hash); conflicting
        content under the same report_id fails closed.
        """
        _utc(now, "ACCEPTED_AT")
        if type(report) is not QuantUpstreamReport:
            raise UpstreamInboxError("REPORT_INVALID")
        _utc(report.created_at, "REPORT_CREATED_AT")
        if report.created_at > now:
            raise UpstreamInboxError("REPORT_CREATED_AT_FUTURE")
        report_id = report.report_id
        envelope_hash = report.envelope_hash
        if report_id in self._revoked:
            raise UpstreamInboxError("RECEIPT_REVOKED")
        prior_id = self._by_report.get(report_id)
        if prior_id is not None:
            prior = self._receipts[prior_id]
            if prior.envelope_hash != envelope_hash:
                raise UpstreamInboxError("RECEIPT_CONFLICT")
            _utc(prior.accepted_at, "ACCEPTED_AT")
            _utc(prior.ack.acked_at, "ACKED_AT")
            if prior.accepted_at > now:
                raise UpstreamInboxError("RECEIPT_ACCEPTED_AT_FUTURE")
            if prior.ack.acked_at > now:
                raise UpstreamInboxError("ACKED_AT_FUTURE")
            return prior
        accepted_at = now
        receipt_id = digest(["upstream-receipt-v1", report_id, envelope_hash])
        ack_id = digest(["upstream-ack-v1", report_id, envelope_hash,
                         report.receiver_agent_id])
        ack = UpstreamAck(
            ack_id=ack_id, report_id=report_id,
            receiver_agent_id=report.receiver_agent_id,
            acked_at=accepted_at,
            verification_source=_VERIFICATION_SOURCE)
        # Explicit invariant: the ACK can never precede acceptance.
        if ack.acked_at < accepted_at:
            raise UpstreamInboxError("ACK_TIME_INVALID")
        receipt = UpstreamReceipt(
            receipt_id=receipt_id, report_id=report_id,
            envelope_hash=envelope_hash,
            sender_agent_id=report.sender_agent_id,
            accepted_at=accepted_at, ack=ack, ledger_epoch=self._epoch)
        self._reports[report_id] = report
        self._receipts[receipt_id] = receipt
        self._by_report[report_id] = receipt_id
        return receipt

    def revoke(self, receipt_id: str, *, now: datetime) -> int:
        """Append a revocation record; the ledger epoch only moves forward."""
        _utc(now, "REVOKE_NOW")
        _label(receipt_id, "RECEIPT_ID")
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise UpstreamInboxError("RECEIPT_UNKNOWN")
        if receipt.report_id in self._revoked:
            raise UpstreamInboxError("RECEIPT_ALREADY_REVOKED")
        self._epoch += 1
        self._revoked[receipt.report_id] = self._epoch
        return self._epoch

    def is_active(self, receipt_id: str) -> bool:
        """Injected-query target: True only while accepted and not revoked."""
        receipt = self._receipts.get(receipt_id)
        return receipt is not None and receipt.report_id not in self._revoked

    def is_revoked(self, report_id: str) -> bool:
        return report_id in self._revoked

    def get(self, receipt_id: str) -> UpstreamReceipt | None:
        """Read-only lookup by receipt id."""
        return self._receipts.get(receipt_id)

    def receipt_for_report(self, report_id: str) -> UpstreamReceipt | None:
        """Read-only lookup by upstream report id."""
        receipt_id = self._by_report.get(report_id)
        if receipt_id is None:
            return None
        return self._receipts.get(receipt_id)

    def list_accepted(self) -> Tuple[UpstreamReceipt, ...]:
        """Deterministic read-only snapshot of every accepted receipt."""
        return tuple(sorted(
            self._receipts.values(),
            key=lambda r: (utc_text(r.accepted_at), r.receipt_id)))
