"""G1-A: pure, synthetic evidence projection DTO. No authority or persistence.

Parsing validates shape, not source rights. Unknown clocks stay unknown; a caller
must never use these objects as evidence of real authorization or durable ack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


class ProjectionRejected(ValueError):
    """Fail-closed malformed projection."""


_CODE = re.compile(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ|[489]\d{5}\.BJ)\Z", re.ASCII)
_LINK_STATES = frozenset({"fact", "candidate"})  # fact is reserved until a trusted source verifier exists


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionRejected(f"{field}: nonempty string required")
    return value


def _exact(data: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(data, Mapping) or set(data) - allowed:
        raise ProjectionRejected("unknown projection field")


@dataclass(frozen=True)
class SourceClock:
    """UTC instant or explicit unknown; ambiguous local times are forbidden."""

    value: datetime | None
    provenance: str

    def __post_init__(self) -> None:
        _required(self.provenance, "provenance")
        if (self.value is None) != (self.provenance == "unknown"):
            raise ProjectionRejected("clock: unknown provenance requires missing value")
        if self.value is not None:
            if not isinstance(self.value, datetime) or self.value.tzinfo is None or self.value.utcoffset() is None:
                raise ProjectionRejected("clock: timezone-aware datetime required")
            object.__setattr__(self, "value", self.value.astimezone(timezone.utc))

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> SourceClock:
        _exact(data, {"value", "provenance"})
        if "value" not in data or "provenance" not in data:
            raise ProjectionRejected("clock: value and provenance required")
        raw = data["value"]
        if raw is not None:
            if not isinstance(raw, str):
                raise ProjectionRejected("clock: ISO string required")
            try:
                raw = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise ProjectionRejected("clock: invalid ISO time") from exc
        return cls(raw, data.get("provenance"))


@dataclass(frozen=True)
class EvidenceRef:
    source_id: str
    subsource: str
    native_id: str
    version: str
    source_ref: str
    content_hash: str | None
    occurred_at: SourceClock
    published_at: SourceClock
    first_seen_at: SourceClock
    available_at: SourceClock

    def __post_init__(self) -> None:
        for name in ("source_id", "subsource", "native_id", "version", "source_ref"):
            _required(getattr(self, name), name)
        if self.content_hash is not None:
            _required(self.content_hash, "content_hash")
        clocks = (self.occurred_at, self.published_at, self.first_seen_at, self.available_at)
        if not all(isinstance(c, SourceClock) for c in clocks):
            raise ProjectionRejected("four clocks required")
        publication, seen, available = (c.value for c in clocks[1:])
        if publication is not None and seen is not None and publication > seen:
            raise ProjectionRejected("first seen before publication")
        if seen is not None and available is not None and seen > available:
            raise ProjectionRejected("available before first seen")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.source_id, self.subsource, self.native_id, self.version

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> EvidenceRef:
        names = {"source_id", "subsource", "native_id", "version", "source_ref", "content_hash",
                 "occurred_at", "published_at", "first_seen_at", "available_at"}
        _exact(data, names)
        if not names <= set(data):
            raise ProjectionRejected("missing evidence projection field")
        return cls(*(data[n] for n in ("source_id", "subsource", "native_id", "version", "source_ref", "content_hash")),
                   *(SourceClock.parse(data[n]) for n in ("occurred_at", "published_at", "first_seen_at", "available_at")))

    def available_as_of(self, at: datetime) -> bool:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ProjectionRejected("as_of: timezone-aware datetime required")
        if self.published_at.value is None or self.first_seen_at.value is None or self.available_at.value is None:
            return False
        instant = at.astimezone(timezone.utc)
        return all(c.value <= instant for c in (self.published_at, self.first_seen_at, self.available_at))


def assert_consistent_evidence(left: EvidenceRef, right: EvidenceRef) -> None:
    """Reject divergent versions of one key at an explicit in-memory merge boundary.

    Callers must compare against every existing record for that key; constructing
    an EvidenceRef alone cannot detect collisions across records or processes.
    """
    if not isinstance(left, EvidenceRef) or not isinstance(right, EvidenceRef):
        raise ProjectionRejected("evidence refs required for consistency check")
    if left.key == right.key and left != right:
        raise ProjectionRejected("conflicting evidence projection for the same key")


@dataclass(frozen=True)
class SecurityRef:
    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _CODE.fullmatch(self.code):
            raise ProjectionRejected("invalid A-share code")


@dataclass(frozen=True)
class EvidenceSecurityLink:
    evidence: EvidenceRef
    security: SecurityRef
    relation_type: str
    match_basis: str
    state: str
    valid_from: SourceClock
    valid_to: SourceClock
    relation_available_at: SourceClock | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, EvidenceRef) or not isinstance(self.security, SecurityRef):
            raise ProjectionRejected("evidence and security required")
        _required(self.relation_type, "relation_type")
        _required(self.match_basis, "match_basis")
        if not isinstance(self.state, str) or self.state not in _LINK_STATES:
            raise ProjectionRejected("link state must be fact or candidate")
        if self.state == "fact":
            raise ProjectionRejected("fact requires a trusted source verifier, not a caller-supplied basis")
        if not isinstance(self.valid_from, SourceClock) or not isinstance(self.valid_to, SourceClock):
            raise ProjectionRejected("validity clocks required")
        if self.valid_from.value is not None and self.valid_to.value is not None and self.valid_from.value > self.valid_to.value:
            raise ProjectionRejected("invalid relation validity")
        if self.relation_available_at is not None and not isinstance(self.relation_available_at, SourceClock):
            raise ProjectionRejected("relation availability clock required")

    def available_as_of(self, at: datetime) -> bool:
        if not self.evidence.available_as_of(at):
            return False
        if self.relation_available_at is None or self.relation_available_at.value is None:
            return False
        instant = at.astimezone(timezone.utc)
        return ((self.valid_from.value is None or self.valid_from.value <= instant)
                and (self.valid_to.value is None or instant <= self.valid_to.value)
                and self.relation_available_at.value <= instant)


@dataclass(frozen=True)
class ModelJudgement:
    """A model judgement is not an EvidenceSecurityLink or a factual edge."""

    bias: str
    confidence: float
    model_version: str

    def __post_init__(self) -> None:
        import math
        if self.bias not in ("bull", "bear", "neutral") or isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ProjectionRejected("invalid model judgement")
        _required(self.model_version, "model_version")


@dataclass(frozen=True)
class RightsDecision:
    """Descriptive scope reference only; never an authorization token."""

    rights_ref: str | None
    state: str

    def __post_init__(self) -> None:
        if self.state not in ("unknown", "blocked", "reviewed"):
            raise ProjectionRejected("invalid rights state")
        if self.rights_ref is not None:
            _required(self.rights_ref, "rights_ref")
        if self.state == "reviewed" and self.rights_ref is None:
            raise ProjectionRejected("reviewed rights require reference")


def page_status(total: int, shown: int, *, partial: bool, errors: tuple[str, ...] = ()) -> dict[str, object]:
    """Report a bounded projection, not a claim of full graph coverage."""
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (total, shown)) or shown > total:
        raise ProjectionRejected("invalid page counts")
    if not isinstance(partial, bool) or not isinstance(errors, tuple) or any(not isinstance(e, str) or not e for e in errors):
        raise ProjectionRejected("invalid page status")
    if errors and not partial:
        raise ProjectionRejected("errors require partial status")
    return {"total": total, "shown": shown, "truncated": shown < total, "partial": partial,
            "errors": list(errors), "coverage": "loaded_authorized_scope_only"}
