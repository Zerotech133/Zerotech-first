"""R02 isolated, synthetic-only evidence and source-rights contract.

No network, filesystem, or database access. This is not a collector or durable ack.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class ContractDenied(ValueError):
    """A closed-on-failure contract rejection."""


@dataclass(frozen=True)
class RightsScope:
    source_id: str
    content_type: str
    licensing_party: str
    evidence_ref: str
    owner_id: str
    permitted_actions: frozenset[str]
    permitted_use: str
    valid_from: datetime
    valid_until: datetime
    retention_rule: str
    revocation_rule: str
    stop_condition: str
    review_state: str


@dataclass(frozen=True)
class EvidenceCandidate:
    source_id: str
    content_type: str
    source_record_id: str
    source_ref: str
    content_hash: str
    parser_version: str
    run_id: str
    receipt_id: str
    source_published_at: datetime | None = None
    raw_pointer: str | None = None


@dataclass(frozen=True)
class EvidenceVersion:
    source_id: str
    content_type: str
    source_record_id: str
    content_hash: str
    version: int
    rights_ref: str
    rights_valid_from: datetime
    rights_valid_until: datetime
    permitted_actions: frozenset[str]
    permitted_use: str
    first_seen_at: datetime | None
    source_published_at: datetime | None = None
    source_ref: str = ""          # A6 fix: provenance must be auditable on the version itself
    receipt_id: str = ""          # A6 fix: collection receipt lineage, same rule


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractDenied("TIME_INVALID: timezone-aware timestamp required")


def _utc(value: datetime) -> datetime:
    _time(value)
    return value.astimezone(timezone.utc)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class EvidenceBook:
    """In-memory contract harness; persistence and real authorization are out of scope."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str, str], list[EvidenceVersion]] = {}
        self._issued: dict[int, EvidenceVersion] = {}
        self._observations: dict[EvidenceVersion, datetime] = {}
        self._visibility: dict[tuple[EvidenceVersion, str], datetime] = {}
        self._visibility_permissions: dict[tuple[EvidenceVersion, str], tuple[str, str]] = {}
        self._revoked: set[tuple[str, str, str]] = set()
        self._active_scopes: dict[tuple[str, str, str], RightsScope] = {}

    def _known(self, item: EvidenceVersion) -> None:
        if not isinstance(item, EvidenceVersion) or self._issued.get(id(item)) is not item:
            raise ContractDenied("EVIDENCE_UNKNOWN")

    def versions(self, source_id: str, content_type: str, record_id: str) -> tuple[EvidenceVersion, ...]:
        return tuple(self._versions.get((source_id, content_type, record_id), ()))

    def accept(self, rights: RightsScope, candidate: EvidenceCandidate, *,
               action: str, use: str, observed_at: datetime | None) -> EvidenceVersion:
        if not isinstance(rights, RightsScope) or not isinstance(candidate, EvidenceCandidate):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: verified scope required")
        if observed_at is not None:
            _time(observed_at)
        if candidate.source_published_at is not None:
            _time(candidate.source_published_at)
        fields = (candidate.source_id, candidate.content_type, candidate.source_record_id,
                  candidate.source_ref, candidate.content_hash, candidate.parser_version,
                  candidate.run_id, candidate.receipt_id)
        if not all(_text(field) for field in fields):
            raise ContractDenied("EVIDENCE_INVALID: stable identity and provenance required")
        if rights.source_id != candidate.source_id or rights.content_type != candidate.content_type:
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: source or type mismatch")
        rights_fields = (rights.source_id, rights.content_type, rights.licensing_party,
                         rights.evidence_ref, rights.owner_id, rights.permitted_use,
                         rights.retention_rule, rights.revocation_rule, rights.stop_condition)
        if not all(_text(field) for field in rights_fields):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: scope incomplete")
        if not isinstance(rights.permitted_actions, frozenset) or not all(
            _text(value) for value in rights.permitted_actions
        ):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: invalid actions")
        try:
            _time(rights.valid_from)
            _time(rights.valid_until)
        except ContractDenied as error:
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: invalid validity window") from error
        if (observed_at is not None and candidate.source_published_at is not None
                and _utc(candidate.source_published_at) > _utc(observed_at)):
            raise ContractDenied("TIME_INVALID: observation before publication")
        if (rights.review_state != "approved" or not _text(action) or not _text(use)
                or action not in rights.permitted_actions or use != rights.permitted_use
                or observed_at is None or not _utc(rights.valid_from) <= _utc(observed_at) <= _utc(rights.valid_until)
                or (rights.source_id, rights.content_type, rights.evidence_ref) in self._revoked):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: unapproved, expired or revoked")
        scope_key = (rights.source_id, rights.content_type, rights.evidence_ref)
        prior_scope = self._active_scopes.get(scope_key)
        if prior_scope is not None and prior_scope != rights:
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: scope revision requires new reference")
        key = (candidate.source_id, candidate.content_type, candidate.source_record_id)
        records = self._versions.setdefault(key, [])
        if records and records[-1].content_hash == candidate.content_hash and records[-1].rights_ref == rights.evidence_ref:
            # A6 fix: identical content under identical rights must also carry identical
            # provenance. A moved source_ref or a new receipt_id with the same hash is a
            # lineage drift, not a no-op — fail closed instead of silently returning the
            # old version whose stored pointers no longer describe where content came from.
            if records[-1].source_ref != candidate.source_ref or records[-1].receipt_id != candidate.receipt_id:
                raise ContractDenied(
                    "PROVENANCE_CONFLICT: same content hash with changed source_ref/receipt_id "
                    "requires explicit versioning decision, not silent reuse")
            return records[-1]
        item = EvidenceVersion(*key, candidate.content_hash, len(records) + 1,
                               rights.evidence_ref, _utc(rights.valid_from),
                               _utc(rights.valid_until), rights.permitted_actions,
                               rights.permitted_use, _utc(observed_at), candidate.source_published_at,
                               candidate.source_ref, candidate.receipt_id)
        records.append(item)
        self._active_scopes[scope_key] = rights
        self._issued[id(item)] = item
        if observed_at is not None:
            self._observations[item] = _utc(observed_at)
        return item

    def observe(self, item: EvidenceVersion, at: datetime, receipt_id: str) -> None:
        self._known(item)
        _time(at)
        if not _text(receipt_id):
            raise ContractDenied("OBSERVATION_RECEIPT_REQUIRED")
        if item not in self._observations:
            self._observations[item] = _utc(at)

    def mark_visible(self, item: EvidenceVersion, consumer_id: str, at: datetime,
                     receipt_id: str, *, persisted: bool, action: str,
                     use: str) -> None:
        self._known(item)
        moment = _utc(at)
        if not _text(consumer_id) or not _text(receipt_id):
            raise ContractDenied("VISIBILITY_RECEIPT_REQUIRED")
        if persisted is not True:
            raise ContractDenied("PERSISTENCE_REQUIRED")
        if (item.source_id, item.content_type, item.rights_ref) in self._revoked:
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: revoked")
        if (action not in item.permitted_actions or use != item.permitted_use
                or not item.rights_valid_from <= moment <= item.rights_valid_until):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: action, use or validity")
        first_seen = self._observations.get(item)
        if first_seen is None or moment < first_seen:
            raise ContractDenied("TIME_INVALID: availability before observation")
        key = (item, consumer_id)
        if key in self._visibility and self._visibility_permissions[key] != (action, use):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: consumer action changed")
        if key not in self._visibility:
            self._visibility[key] = moment
            self._visibility_permissions[key] = (action, use)

    def as_of(self, item: EvidenceVersion, consumer_id: str, decision_at: datetime, *,
              action: str, use: str) -> bool:
        self._known(item)
        moment = _utc(decision_at)
        if (item.source_id, item.content_type, item.rights_ref) in self._revoked:
            return False
        if not item.rights_valid_from <= moment <= item.rights_valid_until:
            return False
        if not _text(consumer_id) or not _text(action) or not _text(use):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: consumer scope required")
        key = (item, consumer_id)
        if action not in item.permitted_actions or use != item.permitted_use:
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: consumer action or use mismatch")
        bound = self._visibility_permissions.get(key)
        if bound is not None and bound != (action, use):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: consumer action or use mismatch")
        first_seen = self._observations.get(item)
        available = self._visibility.get((item, consumer_id))
        if first_seen is None or available is None:
            raise ContractDenied("PIT_TIME_UNKNOWN: observation or consumer visibility missing")
        return first_seen <= moment and available <= moment

    def revoke(self, source_id: str, content_type: str, evidence_ref: str) -> None:
        if not all(_text(field) for field in (source_id, content_type, evidence_ref)):
            raise ContractDenied("BLOCK_SOURCE_RIGHTS: invalid revocation identity")
        self._revoked.add((source_id, content_type, evidence_ref))
