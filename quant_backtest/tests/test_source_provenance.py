# -*- coding: utf-8 -*-
"""In-memory adversarial tests for S1 source provenance (not a license)."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import Bar
from source_provenance import (
    LicenseGrant, ShardManifest, SourceCredential, SourceManifest,
    SourceUseRequest, TrustedSnapshot, TrustedIntervalEvidence, canonical_bar_bytes,
    canonical_manifest_bytes, validate_source_use,
)

AT = datetime(2026, 1, 2, 1, 30, tzinfo=timezone.utc)
CAL_HASH = "a" * 64
RULE_HASH = "b" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture() -> dict:
    """Build independent trusted registry entries and 3 exact minute rows."""
    request = SourceUseRequest("service:backtest", "fixture", "BACKTEST", "PRODUCTION",
                               "CN", "SZSE", "000001.SZ", AT, AT + timedelta(minutes=2),
                               "BACKTEST_RESULT")
    bars = tuple(
        Bar("000001.SZ", "CN", "1m", AT + timedelta(minutes=index),
            date(2026, 1, 2), 10, 10, 10, 10, 100, 1000,
            AT + timedelta(minutes=index), AT + timedelta(minutes=index), True,
            "fixture", "v1", "none", "observed", (), "registry:calendar-cn",
            CAL_HASH, "SZSE", "equity", "CNY")
        for index in range(3)
    )
    raw_shards = {
        "shard-0": canonical_bar_bytes(bars[:2]),
        "shard-1": canonical_bar_bytes(bars[2:]),
    }
    shards = (
        ShardManifest("shard-0", AT, AT + timedelta(minutes=1),
                      _sha(raw_shards["shard-0"]), len(raw_shards["shard-0"])),
        ShardManifest("shard-1", AT + timedelta(minutes=2), AT + timedelta(minutes=2),
                      _sha(raw_shards["shard-1"]), len(raw_shards["shard-1"])),
    )
    manifest = SourceManifest("snapshot:v1", "fixture", "CN", "SZSE", "000001.SZ",
                              "registry:calendar-cn", CAL_HASH, "registry:rules-cn",
                              RULE_HASH, ("upstream-feed", "minute-normalizer"), shards)
    snapshot_bytes = canonical_bar_bytes(bars)
    credential = SourceCredential("license:v1", 7, "snapshot:v1",
                                  _sha(snapshot_bytes), _sha(canonical_manifest_bytes(manifest)))
    grant = LicenseGrant("license:v1", "fixture", "upstream:agreement:2026",
                         AT - timedelta(days=1), AT + timedelta(days=2), 7, False,
                         ("service:backtest",), ("BACKTEST",), ("PRODUCTION",),
                         ("CN",), ("SZSE",), ("000001.SZ",), ("BACKTEST_RESULT",),
                         request.window_start, request.window_end)
    snapshot = TrustedSnapshot("snapshot:v1", "fixture", _sha(snapshot_bytes),
                               len(snapshot_bytes), credential.manifest_sha256,
                               manifest.calendar_ref, manifest.calendar_hash,
                               manifest.market_rule_ref, manifest.market_rule_hash,
                               manifest.lineage, request.window_start, request.window_end)
    return dict(request=request, credential=credential, manifest=manifest, bars=bars,
                raw_snapshot_bytes=snapshot_bytes, raw_shards=raw_shards,
                trusted_license_registry={"license:v1": grant},
                acl_authorizer=lambda _request: True,
                trusted_snapshot_registry={"snapshot:v1": snapshot},
                trusted_interval_registry={"snapshot:v1": TrustedIntervalEvidence(
                    "snapshot:v1", manifest.calendar_ref, manifest.calendar_hash,
                    manifest.market_rule_ref, manifest.market_rule_hash,
                    tuple(bar.dt for bar in bars))},
                now=AT + timedelta(minutes=4))


def _check(case: dict):
    return validate_source_use(**case)


def test_valid_synthetic_evidence_is_not_production_license() -> None:
    result = _check(_fixture())
    assert result.ok and result.error_code is None
    assert result.production_permitted is False


@pytest.mark.parametrize("change,code", [
    ("grant", "SOURCE_LICENSE_UNVERIFIED"), ("acl", "SOURCE_ACCESS_DENIED"),
    ("expired", "SOURCE_LICENSE_EXPIRED"), ("revoked", "SOURCE_LICENSE_UNVERIFIED"),
    ("generation", "SOURCE_LICENSE_UNVERIFIED"), ("scope", "SOURCE_ACCESS_DENIED"),
    ("research", "SOURCE_ACCESS_DENIED"), ("visibility", "SOURCE_AVAILABILITY_UNVERIFIED"),
    ("empty", "SOURCE_COVERAGE_INCOMPLETE"), ("missing_minute", "SOURCE_SNAPSHOT_MISMATCH"),
    ("bytes", "SOURCE_SNAPSHOT_MISMATCH"), ("calendar", "CALENDAR_MISMATCH"),
    ("rule", "MARKET_RULE_MISMATCH"), ("lineage", "SOURCE_MANIFEST_MISSING"),
    ("shard_gap", "SOURCE_INTEGRITY_MISMATCH"),
])
def test_missing_or_conflicting_evidence_fails_closed(change: str, code: str) -> None:
    case = _fixture()
    grant = case["trusted_license_registry"]["license:v1"]
    manifest = case["manifest"]
    if change == "grant":
        case["trusted_license_registry"] = {}
    elif change == "acl":
        case["acl_authorizer"] = lambda _request: False
    elif change == "expired":
        case["now"] = grant.expires_at
    elif change == "revoked":
        case["trusted_license_registry"] = {"license:v1": replace(grant, revoked=True)}
    elif change == "generation":
        case["credential"] = replace(case["credential"], grant_generation=8)
    elif change == "scope":
        case["request"] = replace(case["request"], output_class="EXPORT")
    elif change == "research":
        case["credential"] = replace(case["credential"], research_only=True)
    elif change == "visibility":
        case["bars"] = tuple(replace(bar, available_at=None) if index == 1 else bar
                             for index, bar in enumerate(case["bars"]))
        _repin(case)
        snapshot = case["trusted_snapshot_registry"]["snapshot:v1"]
        case["trusted_snapshot_registry"] = {
            "snapshot:v1": replace(snapshot, sha256=case["credential"].snapshot_sha256,
                                   size=len(case["raw_snapshot_bytes"]),
                                   manifest_sha256=case["credential"].manifest_sha256)}
    elif change == "empty":
        case["bars"] = ()
    elif change == "missing_minute":
        case["bars"] = case["bars"][:1] + case["bars"][2:]
    elif change == "bytes":
        case["raw_snapshot_bytes"] += b"tamper"
    elif change in ("calendar", "rule", "lineage"):
        field = {"calendar": "calendar_hash", "rule": "market_rule_hash",
                 "lineage": "lineage"}[change]
        replacement = ("forged",) if change == "lineage" else "c" * 64
        case["manifest"] = replace(manifest, **{field: replacement})
    elif change == "shard_gap":
        case["manifest"] = replace(manifest, shards=manifest.shards[:1])
    result = _check(case)
    assert result.ok is False and result.error_code == code
    assert result.production_permitted is False


def _repin(case: dict) -> None:
    """Recompute attacker-controlled hashes but leave trusted pins independent."""
    bars = case["bars"]
    case["raw_snapshot_bytes"] = canonical_bar_bytes(bars)
    case["raw_shards"] = {
        shard.shard_ref: canonical_bar_bytes(tuple(
            bar for bar in bars if shard.start <= bar.dt <= shard.end))
        for shard in case["manifest"].shards
    }
    shards = tuple(
        replace(shard, sha256=_sha(case["raw_shards"][shard.shard_ref]),
                size=len(case["raw_shards"][shard.shard_ref]))
        for shard in case["manifest"].shards
    )
    case["manifest"] = replace(case["manifest"], shards=shards)
    case["credential"] = replace(case["credential"],
                                 snapshot_sha256=_sha(case["raw_snapshot_bytes"]),
                                 manifest_sha256=_sha(canonical_manifest_bytes(case["manifest"])))


def test_attacker_rehashes_rows_shards_and_manifest_but_trusted_pin_rejects() -> None:
    case = _fixture()
    case["bars"] = tuple(replace(bar, c=11) if index == 1 else bar
                         for index, bar in enumerate(case["bars"]))
    _repin(case)
    result = _check(case)
    assert not result.ok and result.error_code == "SOURCE_INTEGRITY_MISMATCH"


def test_attacker_rehashes_manifest_and_trusted_snapshot_but_no_license_acl() -> None:
    case = _fixture()
    case["trusted_license_registry"] = {}
    case["acl_authorizer"] = lambda _request: True
    assert _check(case).error_code == "SOURCE_LICENSE_UNVERIFIED"


def test_empty_interval_with_forged_complete_metadata_never_passes() -> None:
    case = _fixture()
    case["bars"] = (case["bars"][0], case["bars"][2])
    _repin(case)
    snapshot = case["trusted_snapshot_registry"]["snapshot:v1"]
    case["trusted_snapshot_registry"] = {
        "snapshot:v1": replace(snapshot, sha256=case["credential"].snapshot_sha256,
                               size=len(case["raw_snapshot_bytes"]),
                               manifest_sha256=case["credential"].manifest_sha256)}
    assert case["manifest"].coverage == "complete"
    assert _check(case).error_code == "SOURCE_COVERAGE_INCOMPLETE"


def test_same_snapshot_ref_with_changed_hash_fails_closed() -> None:
    case = _fixture()
    case["credential"] = replace(case["credential"], snapshot_sha256="f" * 64)
    assert _check(case).error_code == "SOURCE_SNAPSHOT_MISMATCH"


def test_missing_independent_calendar_intervals_is_not_covered() -> None:
    case = _fixture()
    case["trusted_interval_registry"] = {}
    assert _check(case).error_code == "SOURCE_COVERAGE_INCOMPLETE"


def test_trusted_calendar_and_rule_pins_reject_manifest_identity_flap() -> None:
    case = _fixture()
    evidence = case["trusted_interval_registry"]["snapshot:v1"]
    case["trusted_interval_registry"] = {
        "snapshot:v1": replace(evidence, calendar_hash="f" * 64)}
    assert _check(case).error_code == "CALENDAR_MISMATCH"
    case["trusted_interval_registry"] = {
        "snapshot:v1": replace(evidence, market_rule_hash="f" * 64)}
    assert _check(case).error_code == "MARKET_RULE_MISMATCH"


def test_trusted_calendar_gap_rejects_rehashed_manifest_and_snapshot() -> None:
    case = _fixture()
    case["bars"] = (case["bars"][0], case["bars"][2])
    _repin(case)
    snapshot = case["trusted_snapshot_registry"]["snapshot:v1"]
    case["trusted_snapshot_registry"] = {
        "snapshot:v1": replace(snapshot, sha256=case["credential"].snapshot_sha256,
                               size=len(case["raw_snapshot_bytes"]),
                               manifest_sha256=case["credential"].manifest_sha256)}
    assert _check(case).error_code == "SOURCE_COVERAGE_INCOMPLETE"
