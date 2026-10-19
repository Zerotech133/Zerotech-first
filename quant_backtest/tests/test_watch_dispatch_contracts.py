"""Synthetic offline-only G0 contracts; no external credentials or side effects."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch import RouteIntent, freeze_offline
from quant_backtest.watch_dispatch.canonical import canonical_bytes, parse_canonical

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64


def sample(**changes):
    fields = dict(schema_version=1, event_id="ev1", evidence_version=1,
                  previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
                  market="SH", code="600000", source_membership_id="member1",
                  source_revision="rev1", rule_id="r1", rule_version=1,
                  comparator="GE", threshold="1.00", unit="ratio", window="5m",
                  observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
                  raw_digest=H, source_id="src1", calendar_id="cal1",
                  source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
                  valid_until=T + timedelta(minutes=10), facts=("observed",),
                  hypotheses=("candidate",), supporting_refs=("raw:1",),
                  contradicting_refs=(), confirmation_condition="independent confirmation",
                  invalidation_condition="source revoked")
    fields.update(changes)
    return freeze_offline(**fields)


def route(envelope, **changes):
    fields = dict(intent_id="intent1", event_id=envelope.event_id,
                  evidence_version=envelope.evidence_version,
                  envelope_hash=envelope.envelope_hash, route="QUANT_UPSTREAM_REPORT",
                  target_type="desk", target_id="desk1", tenant_scope="owner1",
                  payload_digest=H, policy_version="p1", control_epoch=0,
                  created_at=T, valid_until=T + timedelta(minutes=5))
    fields.update(changes)
    return RouteIntent(**fields)


def test_offline_observation_never_grants_permissions():
    envelope = sample()
    envelope.validate(now=T)
    r = route(envelope)
    r.validate(envelope, now=T)
    for item in (envelope, r):
        assert item.production_permitted is False
        assert item.notification_permitted is False
        assert item.trading_permitted is False
    assert r.observation_only is True and r.executable is False
    with pytest.raises(ValueError, match="NOT_CURRENT"):
        envelope.validate(now=envelope.valid_until)


@pytest.mark.parametrize("bad", [
    {"pool": "C"}, {"tenant_or_owner_id": ""}, {"market": "sh"},
    {"threshold": "NaN"}, {"observed_value": "1e309"},
    {"source_as_of": T + timedelta(seconds=1)},
    {"visible_at": T + timedelta(seconds=1)},
    {"detected_at": T + timedelta(seconds=1)},
    {"valid_until": T}, {"facts": ("",)}, {"facts": []},
    {"raw_digest": "bad"}, {"freeze_at": T.replace(tzinfo=None)},
])
def test_invalid_envelope_fails_closed(bad):
    with pytest.raises(ValueError):
        sample(**bad)


def test_digest_tampering_and_chain_binding():
    first = sample()
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        replace(first, code="600001").validate()
    second = sample(evidence_version=2, _previous=first,
                    freeze_at=T + timedelta(minutes=1))
    with pytest.raises(ValueError, match="PREVIOUS_UNVERIFIED"):
        second.validate()
    second.validate(previous=first)
    with pytest.raises(ValueError, match="PREVIOUS_UNVERIFIED"):
        second.validate(previous=replace(first, event_id="different"))
    with pytest.raises(ValueError, match="PREVIOUS_UNVERIFIED"):
        second.validate(previous=None)
    with pytest.raises(ValueError, match="PREVIOUS_HASH_MISMATCH"):
        second.validate(previous=replace(first, raw_digest="b" * 64))


def test_single_upstream_route_keys_differ_by_target_and_never_execute():
    envelope = sample()
    keys = set()
    for target in ("quant-desk-1", "quant-desk-2"):
        r = route(envelope, target_id=target)
        keys.add(r.idempotency_key(envelope))
    assert len(keys) == 2
    assert route(envelope).idempotency_key(envelope) != route(envelope, target_id="desk2").idempotency_key(envelope)
    for bad_route in ("FEISHU_INFO", "RESEARCH_PENDING", "EXCHANGE_OBSERVE",
                      "SIM_DISPATCH", "DIRECT_FEISHU"):
        with pytest.raises(ValueError):
            route(envelope, route=bad_route).validate(envelope)
    for change in ({"executable": True}, {"observation_only": False},
                   {"no_order_permission": False}, {"status": "SENT"},
                   {"tenant_scope": "other"},
                   {"control_epoch": True}, {"created_at": T - timedelta(seconds=1)}):
        with pytest.raises(ValueError):
            route(envelope, **change).validate(envelope)
    with pytest.raises(ValueError, match="NOT_CURRENT"):
        route(envelope).validate(envelope, now=T + timedelta(minutes=5))


def test_canonical_encoding_rejects_ambiguous_data():
    raw = canonical_bytes({"中文": [True, None, 1]})
    assert parse_canonical(raw) == {"中文": [True, None, 1]}
    for payload in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.0}', b' {"a":1}', b'{"a":1e2}'):
        with pytest.raises(ValueError):
            parse_canonical(payload)
    for obj in ({1: "value"}, (1, 2), {"a": float("nan")}, {"a": {1, 2}}):
        with pytest.raises(ValueError):
            canonical_bytes(obj)
