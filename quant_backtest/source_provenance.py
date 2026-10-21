# -*- coding: utf-8 -*-
"""Pure in-memory, fail-closed minute-source evidence verifier (S1 only).

The caller supplies independent trusted registries and an ACL decision. Neither a
credential, a manifest, nor this verifier can issue a real production license.
No filesystem, network, clock, or production runner is accessed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Callable, Mapping, Optional, Sequence, Tuple

if __package__:
    from .bar_dto import Bar
else:
    from bar_dto import Bar


@dataclass(frozen=True)
class SourceUseRequest:
    """Server-established subject and exact intended use of one minute window."""

    principal: str
    source_ref: str
    purpose: str
    environment: str
    market: str
    exchange: str
    symbol: str
    window_start: datetime
    window_end: datetime
    output_class: str


@dataclass(frozen=True)
class SourceCredential:
    """Untrusted reference to separately registered grant and snapshot evidence."""

    grant_ref: str
    grant_generation: int
    snapshot_ref: str
    snapshot_sha256: str
    manifest_sha256: str
    research_only: bool = False


@dataclass(frozen=True)
class LicenseGrant:
    """Trusted registry entry, including upstream evidence and revocation epoch."""

    grant_ref: str
    source_ref: str
    upstream_authorization_ref: str
    valid_from: datetime
    expires_at: datetime
    revocation_generation: int
    revoked: bool
    principals: Tuple[str, ...]
    purposes: Tuple[str, ...]
    environments: Tuple[str, ...]
    markets: Tuple[str, ...]
    exchanges: Tuple[str, ...]
    symbols: Tuple[str, ...]
    output_classes: Tuple[str, ...]
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class ShardManifest:
    """Untrusted manifest description of one contiguous minute interval."""

    shard_ref: str
    start: datetime
    end: datetime
    sha256: str
    size: int


@dataclass(frozen=True)
class SourceManifest:
    """Untrusted source lineage and complete expected minute intervals."""

    snapshot_ref: str
    source_ref: str
    market: str
    exchange: str
    symbol: str
    calendar_ref: str
    calendar_hash: str
    market_rule_ref: str
    market_rule_hash: str
    lineage: Tuple[str, ...]
    shards: Tuple[ShardManifest, ...]
    coverage: str = "complete"


@dataclass(frozen=True)
class TrustedSnapshot:
    """Independent trusted registry pin, not supplied by credential/manifest."""

    snapshot_ref: str
    source_ref: str
    sha256: str
    size: int
    manifest_sha256: str
    calendar_ref: str
    calendar_hash: str
    market_rule_ref: str
    market_rule_hash: str
    lineage: Tuple[str, ...]
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class TrustedIntervalEvidence:
    """Independent calendar/rule pins and complete expected minute timestamps."""

    snapshot_ref: str
    calendar_ref: str
    calendar_hash: str
    market_rule_ref: str
    market_rule_hash: str
    expected_intervals: Tuple[datetime, ...]


@dataclass(frozen=True)
class ProvenanceResult:
    """Local evidence verdict; ok never constitutes production authorization."""

    ok: bool
    error_code: Optional[str]
    snapshot_ref: Optional[str]
    production_permitted: bool = False


class _EvidenceError(ValueError):
    """Internal stable-code validation failure."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise _EvidenceError(code)


def _utc(value: datetime) -> datetime:
    _require(isinstance(value, datetime) and value.tzinfo is not None and
             value.utcoffset() is not None, "SOURCE_AVAILABILITY_UNVERIFIED")
    return value.astimezone(timezone.utc)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _encode_json(value: object) -> bytes:
    def encode(item: object) -> object:
        if isinstance(item, datetime):
            return _utc(item).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, tuple):
            return [encode(child) for child in item]
        if isinstance(item, list):
            return [encode(child) for child in item]
        if isinstance(item, dict):
            return {key: encode(child) for key, child in item.items()}
        return item

    return json.dumps(encode(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_bar_bytes(bars: Sequence[Bar]) -> bytes:
    """Encode complete Bar rows; raw bytes must exactly match these observations."""
    return _encode_json([asdict(bar) for bar in bars])


def canonical_manifest_bytes(manifest: SourceManifest) -> bytes:
    """Encode a manifest for comparison to an independent trusted SHA-256 pin."""
    return _encode_json(asdict(manifest))


def _known(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.strip().upper() != "UNKNOWN"


def _minutes(start: datetime, end: datetime) -> Tuple[datetime, ...]:
    beginning, ending = _utc(start), _utc(end)
    delta = ending - beginning
    _require(delta >= timedelta(0) and delta.total_seconds() % 60 == 0,
             "SOURCE_COVERAGE_INCOMPLETE")
    count = int(delta.total_seconds() // 60) + 1
    _require(0 < count <= 100_000, "SOURCE_COVERAGE_INCOMPLETE")
    return tuple(beginning + timedelta(minutes=index) for index in range(count))


def validate_source_use(
    request: SourceUseRequest,
    credential: SourceCredential,
    manifest: SourceManifest,
    bars: Sequence[Bar],
    raw_snapshot_bytes: bytes,
    raw_shards: Mapping[str, bytes],
    trusted_license_registry: Mapping[str, LicenseGrant],
    acl_authorizer: Callable[[SourceUseRequest], bool],
    trusted_snapshot_registry: Mapping[str, TrustedSnapshot],
    now: datetime,
    trusted_interval_registry: Optional[Mapping[str, TrustedIntervalEvidence]] = None,
) -> ProvenanceResult:
    """Verify independently pinned authorization, identity, bytes and coverage.

    This only evaluates provided in-memory evidence; even ``ok=True`` is not a
    production permit. Missing or malformed evidence returns a stable error.
    """
    try:
        _require(isinstance(request, SourceUseRequest) and
                 isinstance(credential, SourceCredential) and
                 isinstance(manifest, SourceManifest), "SOURCE_MANIFEST_MISSING")
        current = _utc(now)
        for value in (request.principal, request.source_ref, request.purpose,
                      request.environment, request.market, request.exchange,
                      request.symbol, request.output_class):
            _require(_known(value), "SOURCE_ACCESS_DENIED")
        start, end = _utc(request.window_start), _utc(request.window_end)
        _require(start <= end, "SOURCE_COVERAGE_INCOMPLETE")
        _require(isinstance(trusted_license_registry, Mapping),
                 "SOURCE_LICENSE_UNVERIFIED")
        _require(callable(acl_authorizer), "SOURCE_ACCESS_DENIED")
        _require(isinstance(trusted_snapshot_registry, Mapping) and
                 isinstance(trusted_interval_registry, Mapping), "SOURCE_MANIFEST_MISSING")
        grant = trusted_license_registry.get(credential.grant_ref)
        _require(isinstance(grant, LicenseGrant) and
                 _known(grant.upstream_authorization_ref), "SOURCE_LICENSE_UNVERIFIED")
        _require(grant.grant_ref == credential.grant_ref and
                 grant.source_ref == request.source_ref, "SOURCE_LICENSE_UNVERIFIED")
        _require(type(credential.grant_generation) is int and
                 type(grant.revocation_generation) is int and
                 credential.grant_generation == grant.revocation_generation and
                 grant.revoked is False, "SOURCE_LICENSE_UNVERIFIED")
        _require(_utc(grant.valid_from) <= current < _utc(grant.expires_at),
                 "SOURCE_LICENSE_EXPIRED")
        _require(request.principal in grant.principals and
                 request.purpose in grant.purposes and
                 request.environment in grant.environments and
                 request.market in grant.markets and
                 request.exchange in grant.exchanges and
                 request.symbol in grant.symbols and
                 request.output_class in grant.output_classes and
                 _utc(grant.window_start) <= start <= end <= _utc(grant.window_end),
                 "SOURCE_ACCESS_DENIED")
        _require(not credential.research_only or
                 (request.purpose == "RESEARCH" and request.environment != "PRODUCTION"
                  and request.output_class != "PRODUCTION"), "SOURCE_ACCESS_DENIED")
        try:
            acl_granted = acl_authorizer(request)
        except Exception as exc:
            raise _EvidenceError("SOURCE_ACCESS_DENIED") from exc
        _require(acl_granted is True, "SOURCE_ACCESS_DENIED")
        trusted = trusted_snapshot_registry.get(credential.snapshot_ref)
        _require(isinstance(trusted, TrustedSnapshot), "SOURCE_MANIFEST_MISSING")
        interval_evidence = trusted_interval_registry.get(credential.snapshot_ref)
        _require(isinstance(interval_evidence, TrustedIntervalEvidence) and
                 interval_evidence.snapshot_ref == credential.snapshot_ref,
                 "SOURCE_COVERAGE_INCOMPLETE")
        _require(trusted.snapshot_ref == credential.snapshot_ref == manifest.snapshot_ref and
                 trusted.source_ref == request.source_ref == manifest.source_ref and
                 (manifest.market, manifest.exchange, manifest.symbol) ==
                 (request.market, request.exchange, request.symbol), "SOURCE_SNAPSHOT_MISMATCH")
        _require(_utc(trusted.window_start) == start and
                 _utc(trusted.window_end) == end, "SOURCE_COVERAGE_INCOMPLETE")
        _require(all(_known(value) for value in (
            trusted.calendar_ref, trusted.calendar_hash, trusted.market_rule_ref,
            trusted.market_rule_hash)) and bool(trusted.lineage) and
                 all(_known(item) for item in trusted.lineage), "SOURCE_MANIFEST_MISSING")
        _require((manifest.calendar_ref, manifest.calendar_hash) ==
                 (trusted.calendar_ref, trusted.calendar_hash) ==
                 (interval_evidence.calendar_ref, interval_evidence.calendar_hash),
                 "CALENDAR_MISMATCH")
        _require((manifest.market_rule_ref, manifest.market_rule_hash) ==
                 (trusted.market_rule_ref, trusted.market_rule_hash) ==
                 (interval_evidence.market_rule_ref, interval_evidence.market_rule_hash),
                 "MARKET_RULE_MISMATCH")
        _require(manifest.lineage == trusted.lineage, "SOURCE_MANIFEST_MISSING")
        _require(manifest.coverage == "complete" and
                 credential.manifest_sha256 == trusted.manifest_sha256 and
                 _sha256(canonical_manifest_bytes(manifest)) == trusted.manifest_sha256,
                 "SOURCE_INTEGRITY_MISMATCH")
        _require(isinstance(raw_snapshot_bytes, bytes) and
                 isinstance(raw_shards, Mapping) and
                 len(raw_snapshot_bytes) == trusted.size and
                 _sha256(raw_snapshot_bytes) == trusted.sha256 and
                 credential.snapshot_sha256 == trusted.sha256, "SOURCE_SNAPSHOT_MISMATCH")
        _require(isinstance(bars, (list, tuple)) and bool(bars) and
                 all(isinstance(bar, Bar) for bar in bars), "SOURCE_COVERAGE_INCOMPLETE")
        _require(raw_snapshot_bytes == canonical_bar_bytes(bars), "SOURCE_SNAPSHOT_MISMATCH")
        expected = tuple(_utc(item) for item in interval_evidence.expected_intervals)
        _require(bool(expected) and tuple(sorted(set(expected))) == expected and
                 expected[0] == start and expected[-1] == end,
                 "SOURCE_COVERAGE_INCOMPLETE")
        observed = []
        _require(bool(manifest.shards), "SOURCE_COVERAGE_INCOMPLETE")
        seen_shards = set()
        expected_shard_minutes = []
        for shard in manifest.shards:
            _require(isinstance(shard, ShardManifest) and _known(shard.shard_ref) and
                     shard.shard_ref not in seen_shards, "SOURCE_INTEGRITY_MISMATCH")
            seen_shards.add(shard.shard_ref)
            minutes = _minutes(shard.start, shard.end)
            expected_shard_minutes.extend(minutes)
            shard_rows = [bar for bar in bars if bar.dt in minutes]
            payload = raw_shards.get(shard.shard_ref)
            _require(isinstance(payload, bytes) and type(shard.size) is int and
                     len(payload) == shard.size and _sha256(payload) == shard.sha256 and
                     payload == canonical_bar_bytes(shard_rows), "SOURCE_INTEGRITY_MISMATCH")
        _require(tuple(expected_shard_minutes) == expected, "SOURCE_COVERAGE_INCOMPLETE")
        _require(set(raw_shards) == seen_shards, "SOURCE_INTEGRITY_MISMATCH")
        for bar in bars:
            _require(bar.timeframe == "1m" and bar.market == request.market and
                     bar.exchange == request.exchange and bar.symbol == request.symbol and
                     bar.closed is True, "SOURCE_INTEGRITY_MISMATCH")
            _require(bar.calendar_ref == trusted.calendar_ref and
                     bar.calendar_version == trusted.calendar_hash, "CALENDAR_MISMATCH")
            _require(bar.available_at is not None and bar.close_at is not None and
                     _utc(bar.close_at) <= _utc(bar.available_at) <= current,
                     "SOURCE_AVAILABILITY_UNVERIFIED")
            observed.append(bar.dt)
        _require(tuple(observed) == expected, "SOURCE_COVERAGE_INCOMPLETE")
        return ProvenanceResult(True, None, trusted.snapshot_ref)
    except _EvidenceError as exc:
        return ProvenanceResult(False, str(exc), None)
    except (ValueError, TypeError, AttributeError, OverflowError, KeyError):
        return ProvenanceResult(False, "SOURCE_INTEGRITY_MISMATCH", None)
