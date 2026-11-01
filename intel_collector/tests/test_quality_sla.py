# -*- coding: utf-8 -*-
"""质量 SLA 测试：覆盖 no_data/ok/stale/breach/empty_ratio/circuit 注入。"""
from __future__ import annotations

from datetime import datetime, timezone

from core.contracts import Observation, SourceClock, Status
from core.quality_sla import (
    STATE_BREACH, STATE_NO_DATA, STATE_OK, STATE_STALE,
    compute_sla, compute_sla_for_source,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _spec(fresh=30, max_fail=3):
    return {"sla": {"freshness_minutes": fresh, "max_failures": max_fail}}


def _obs(sid, ts, status="complete"):
    return Observation(
        source_id=sid, snapshot_id=f"{sid}_{ts}", entity_type="t", entity_id="e",
        clock=SourceClock(collected_at=ts), status=status, completeness="ok",
        payload={}, allowed_use_cases=(), source_field="f", raw_sha256="x",
    )


def test_no_data():
    r = compute_sla_for_source("x", _spec(), [], NOW)
    assert r.state == STATE_NO_DATA
    assert r.observation_count == 0
    assert r.age_minutes is None


def test_ok_within_freshness():
    # fresh=30m, 采集于 now-10m
    r = compute_sla_for_source("x", _spec(30), [_obs("x", "2026-11-01T11:50:00Z")], NOW)
    assert r.state == STATE_OK
    assert r.age_minutes == 10.0


def test_stale_between_freshness_and_2x():
    # fresh=30m, 采集于 now-40m -> 30 < 40 <= 60
    r = compute_sla_for_source("x", _spec(30), [_obs("x", "2026-11-01T11:20:00Z")], NOW)
    assert r.state == STATE_STALE


def test_breach_beyond_2x():
    # fresh=30m, 采集于 now-70m -> >60
    r = compute_sla_for_source("x", _spec(30), [_obs("x", "2026-11-01T10:50:00Z")], NOW)
    assert r.state == STATE_BREACH


def test_empty_ratio():
    obs = [_obs("x", "2026-11-01T11:50:00Z", Status.COMPLETE.value),
           _obs("x", "2026-11-01T11:51:00Z", Status.EMPTY.value),
           _obs("x", "2026-11-01T11:52:00Z", Status.EMPTY.value)]
    r = compute_sla_for_source("x", _spec(30), obs, NOW)
    assert r.observation_count == 3
    assert r.empty_count == 2
    assert abs(r.empty_ratio - 2 / 3) < 1e-6


def test_circuit_open_note():
    r = compute_sla_for_source("x", _spec(30), [_obs("x", "2026-11-01T10:50:00Z")], NOW,
                              circuit="open", consecutive_failures=3)
    assert r.circuit == "open"
    assert r.consecutive_failures == 3
    assert "熔断" in r.note


def test_compute_sla_all_sources():
    sources = {"a": _spec(30), "b": _spec(60), "c": _spec(1440)}
    obs = [_obs("a", "2026-11-01T11:50:00Z"), _obs("b", "2026-11-01T09:50:00Z")]
    reports = compute_sla(sources, obs, NOW, circuit_map={"a": {"circuit": "closed", "consecutive_failures": 0}})
    assert set(reports) == {"a", "b", "c"}
    assert reports["a"].state == STATE_OK
    assert reports["b"].state == STATE_BREACH
    assert reports["c"].state == STATE_NO_DATA
    assert reports["a"].circuit == "closed"
