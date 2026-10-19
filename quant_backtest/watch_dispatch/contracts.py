"""G0 frozen offline observation contracts. Never a delivery or trade authority."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .canonical import digest, utc_text

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MARKET = re.compile(r"[A-Z][A-Z0-9_]{0,15}\Z")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,31}\Z")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_ROUTES = frozenset(("QUANT_UPSTREAM_REPORT",))


def _match(value: object, pattern: re.Pattern[str], field: str) -> None:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(field + "_INVALID")


def _decimal(value: object, field: str) -> None:
    if type(value) is not str or _NUMBER.fullmatch(value) is None:
        raise ValueError(field + "_INVALID")
    try:
        if not Decimal(value).is_finite():
            raise ValueError(field + "_INVALID")
    except InvalidOperation as exc:
        raise ValueError(field + "_INVALID") from exc


def _refs(values: object, field: str) -> None:
    if type(values) is not tuple or any(type(v) is not str or not v or len(v) > 512 for v in values):
        raise ValueError(field + "_INVALID")


@dataclass(frozen=True)
class EvidenceEnvelope:
    schema_version: int
    event_id: str
    evidence_version: int
    previous_version_hash: str | None
    envelope_hash: str
    pool: str
    tenant_or_owner_id: str
    market: str
    code: str
    source_membership_id: str
    source_revision: str
    rule_id: str
    rule_version: int
    comparator: str
    threshold: str
    unit: str
    window: str
    observed_value: str
    baseline_value: str
    raw_ref: str
    raw_digest: str
    source_id: str
    calendar_id: str
    source_as_of: datetime
    visible_at: datetime
    detected_at: datetime
    freeze_at: datetime
    valid_until: datetime
    facts: tuple[str, ...]
    hypotheses: tuple[str, ...]
    supporting_refs: tuple[str, ...]
    contradicting_refs: tuple[str, ...]
    confirmation_condition: str
    invalidation_condition: str
    source_qualification: str = "SOURCE_UNVERIFIED"

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def payload(self) -> dict[str, object]:
        fields: dict[str, object] = {}
        for name in self.__dataclass_fields__:
            if name == "envelope_hash":
                continue
            value = getattr(self, name)
            if type(value) is datetime:
                value = utc_text(value)
            elif type(value) is tuple:
                value = list(value)
            fields[name] = value
        return fields

    def validate(self, *, now: datetime | None = None, previous: EvidenceEnvelope | None = None) -> None:
        if type(self) is not EvidenceEnvelope or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("SCHEMA_INVALID")
        for name in ("event_id", "tenant_or_owner_id", "source_membership_id", "source_revision",
                     "rule_id", "unit", "window", "source_id", "calendar_id"):
            _match(getattr(self, name), _LABEL, name.upper())
        _match(self.market, _MARKET, "MARKET")
        _match(self.code, _CODE, "CODE")
        if self.pool not in ("A", "B") or type(self.pool) is not str:
            raise ValueError("POOL_INVALID")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise ValueError("VERSION_INVALID")
        if type(self.rule_version) is not int or self.rule_version < 1:
            raise ValueError("RULE_VERSION_INVALID")
        if self.evidence_version == 1:
            if self.previous_version_hash is not None or previous is not None:
                raise ValueError("PREVIOUS_INVALID")
        else:
            _match(self.previous_version_hash, _SHA, "PREVIOUS_HASH")
            if previous is None or previous.event_id != self.event_id or previous.pool != self.pool or previous.tenant_or_owner_id != self.tenant_or_owner_id or previous.market != self.market or previous.code != self.code or previous.evidence_version + 1 != self.evidence_version or previous.envelope_hash != self.previous_version_hash:
                raise ValueError("PREVIOUS_UNVERIFIED")
            if previous.envelope_hash != digest(previous.payload()):
                raise ValueError("PREVIOUS_HASH_MISMATCH")
            if self.freeze_at <= previous.freeze_at:
                raise ValueError("VERSION_TIME_INVALID")
        if self.comparator not in ("GT", "GE", "LT", "LE", "EQ", "NE") or type(self.comparator) is not str:
            raise ValueError("COMPARATOR_INVALID")
        for name in ("threshold", "observed_value", "baseline_value"):
            _decimal(getattr(self, name), name.upper())
        for name in ("raw_ref", "confirmation_condition", "invalidation_condition"):
            value = getattr(self, name)
            if type(value) is not str or not value or len(value) > 512:
                raise ValueError(name.upper() + "_INVALID")
        _match(self.raw_digest, _SHA, "RAW_DIGEST")
        for name in ("facts", "hypotheses", "supporting_refs", "contradicting_refs"):
            _refs(getattr(self, name), name.upper())
        if not self.facts or not self.supporting_refs:
            raise ValueError("EVIDENCE_EMPTY")
        if self.source_qualification != "SOURCE_UNVERIFIED":
            raise ValueError("SOURCE_QUALIFICATION_UNTRUSTED")
        source, visible, detected, freeze, until = (
            utc_text(getattr(self, name)) for name in
            ("source_as_of", "visible_at", "detected_at", "freeze_at", "valid_until")
        )
        if not (source <= visible <= detected <= freeze < until):
            raise ValueError("TIME_ORDER_INVALID")
        if now is not None:
            utc_text(now)
            if not (self.freeze_at <= now < self.valid_until):
                raise ValueError("EVIDENCE_NOT_CURRENT")
        _match(self.envelope_hash, _SHA, "ENVELOPE_HASH")
        if digest(self.payload()) != self.envelope_hash:
            raise ValueError("ENVELOPE_HASH_MISMATCH")


def freeze_offline(**values: object) -> EvidenceEnvelope:
    """Construct a self-consistent offline envelope, never a trusted source receipt."""
    if "envelope_hash" in values or "source_qualification" in values or "previous_envelope" in values:
        raise ValueError("OFFLINE_ONLY")
    previous = values.get("_previous")
    fields = {key: value for key, value in values.items() if key != "_previous"}
    if previous is not None:
        fields["previous_version_hash"] = previous.envelope_hash
    candidate = EvidenceEnvelope(envelope_hash="0" * 64, **fields)
    result = replace(candidate, envelope_hash=digest(candidate.payload()))
    result.validate(previous=previous)
    return result


@dataclass(frozen=True)
class RouteIntent:
    """Single-upstream reporting intent: watch reports ONLY to its parent layer.

    Monitoring (watch) is a sub-module of quant trading. Anomalies and problems
    are reported upward to the quant trading department, which owns any fan-out
    to Feishu, research, or the trading desk. Watch never delivers to those
    channels directly; only QUANT_UPSTREAM_REPORT is a valid route here.
    """

    intent_id: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    route: str
    target_type: str
    target_id: str
    tenant_scope: str
    payload_digest: str
    policy_version: str
    control_epoch: int
    created_at: datetime
    valid_until: datetime
    status: str = "READY_OFFLINE"
    observation_only: bool = True
    executable: bool = False
    no_order_permission: bool = True

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def validate(self, envelope: EvidenceEnvelope, *, now: datetime | None = None) -> None:
        envelope.validate()
        if type(self) is not RouteIntent or type(self.route) is not str or self.route not in _ROUTES:
            raise ValueError("ROUTE_INVALID")
        for name in ("intent_id", "target_type", "target_id", "tenant_scope", "policy_version"):
            _match(getattr(self, name), _LABEL, name.upper())
        if self.tenant_scope != envelope.tenant_or_owner_id or self.event_id != envelope.event_id or type(self.evidence_version) is not int or self.evidence_version != envelope.evidence_version or self.envelope_hash != envelope.envelope_hash:
            raise ValueError("ENVELOPE_IDENTITY_MISMATCH")
        _match(self.payload_digest, _SHA, "PAYLOAD_DIGEST")
        if type(self.control_epoch) is not int or self.control_epoch < 0:
            raise ValueError("EPOCH_INVALID")
        if type(self.status) is not str or self.status != "READY_OFFLINE" or self.observation_only is not True or self.executable is not False or self.no_order_permission is not True:
            raise ValueError("EXECUTION_FORBIDDEN")
        utc_text(self.created_at)
        utc_text(self.valid_until)
        if not (envelope.freeze_at <= self.created_at < self.valid_until <= envelope.valid_until):
            raise ValueError("ROUTE_TIME_INVALID")
        if now is not None:
            envelope.validate(now=now)
            if not (self.created_at <= now < self.valid_until):
                raise ValueError("ROUTE_NOT_CURRENT")

    def idempotency_key(self, envelope: EvidenceEnvelope) -> str:
        """Target-specific offline key; not a provider receipt."""
        self.validate(envelope)
        return digest([self.event_id, self.evidence_version, self.envelope_hash,
                       self.route, self.target_type, self.target_id, self.tenant_scope])
