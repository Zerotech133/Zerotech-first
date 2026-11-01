# -*- coding: utf-8 -*-
"""PIT 时间旅行查询测试（不触网、纯函数）。"""
from __future__ import annotations

from core.contracts import Observation, SourceClock
from core.pit import filter_as_of, pit_rollup, pit_entity_state


def _obs(sid, eid, ts, status="complete", etype="post"):
    return Observation(
        source_id=sid, snapshot_id=f"{sid}-{eid}-{ts}",
        entity_type=etype, entity_id=eid,
        clock=SourceClock(collected_at=ts),
        status=status, completeness="exhausted",
        payload={"x": eid}, raw_sha256="h", source_payload_sha256="sp",
        allowed_use_cases=("internal_retention",), source_field="page",
    )


def test_filter_as_of_before_all():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z"),
           _obs("b", "K", "2026-10-22T08:00:00Z")]
    out = filter_as_of(obs, "2026-10-20T00:00:00Z")
    assert out == []


def test_filter_as_of_midpoint():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z"),
           _obs("b", "K", "2026-10-22T08:00:00Z")]
    out = filter_as_of(obs, "2026-10-21T23:59:59Z")
    assert [o.source_id for o in out] == ["a"]


def test_filter_as_of_inclusive_equal():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z")]
    out = filter_as_of(obs, "2026-10-21T08:00:00Z")
    assert len(out) == 1  # <= 含等于


def test_filter_as_of_after_all():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z"),
           _obs("b", "K", "2026-10-22T08:00:00Z")]
    out = filter_as_of(obs, "2026-10-30T00:00:00Z")
    assert {o.source_id for o in out} == {"a", "b"}


def test_filter_as_of_unparseable_falls_back_to_all():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z")]
    out = filter_as_of(obs, "not-a-time")
    assert len(out) == 1  # 不静默丢数据


def test_pit_rollup_source_count_grows_over_time():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z"),
           _obs("b", "K", "2026-10-21T09:00:00Z"),
           _obs("c", "K", "2026-10-22T08:00:00Z")]
    early = pit_rollup(obs, "2026-10-21T23:59:59Z")
    late = pit_rollup(obs, "2026-10-30T00:00:00Z")
    assert len(early[("generic", "K")].sources) == 2
    assert len(late[("generic", "K")].sources) == 3


def test_pit_entity_state_exists_false_before_first():
    obs = [_obs("a", "LATE", "2026-10-22T08:00:00Z")]
    st = pit_entity_state(obs, "2026-10-21T00:00:00Z", "generic", "LATE")
    assert st.exists is False
    assert st.source_count == 0


def test_pit_entity_state_time_travel():
    obs = [_obs("a", "K", "2026-10-21T08:00:00Z"),
           _obs("b", "K", "2026-10-22T08:00:00Z")]
    early = pit_entity_state(obs, "2026-10-21T23:59:59Z", "generic", "K")
    late = pit_entity_state(obs, "2026-10-30T00:00:00Z", "generic", "K")
    assert early.exists and early.source_count == 1 and early.last_collected == "2026-10-21T08:00:00Z"
    assert late.exists and late.source_count == 2 and late.last_collected == "2026-10-22T08:00:00Z"
