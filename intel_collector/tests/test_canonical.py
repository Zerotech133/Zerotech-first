# -*- coding: utf-8 -*-
"""canonical 模块测试：A股归一化、跨源归并、消费契约 rollup。"""
from __future__ import annotations

from core.canonical import (
    normalize_symbol,
    extract_symbols,
    CanonicalEntity,
    resolve_entities,
    rollup_by_entity,
)
from core.contracts import Observation, SourceClock


def _xq_obs(target="SH600519"):
    payload = {
        "target": target, "mode": "timeline", "page": 1,
        "items": [{"title": "贵州茅台上涨", "desc": "synthetic", "status_id": "x1",
                   "user_id": "u1", "created_at": "2026-10-22T08:00:00Z"}],
    }
    return Observation(
        source_id="xueqiu", snapshot_id="snap_xq", entity_type="xq_feed",
        entity_id=f"{target}:timeline:p1",
        clock=SourceClock(collected_at="2026-10-22T09:00:00Z"),
        status="complete", payload=payload, source_field="post",
        allowed_use_cases=("internal_retention", "cleaning", "controlled_query"),
    )


def _guba_obs(code6="600519"):
    payload = {"code6": code6, "page": 1,
               "posts": [{"post_id": "p1", "title": "茅台讨论",
                          "ts": "2026-10-20T09:00:00Z"}]}
    return Observation(
        source_id="guba", snapshot_id="snap_gb", entity_type="guba_page",
        entity_id=f"{code6}:p1",
        clock=SourceClock(collected_at="2026-10-20T10:00:00Z"),
        status="complete", payload=payload,
    )


def _news_obs():
    payload = {"endpoint": "sina_finance",
               "items": [{"id": "n1", "title": "贵州茅台 600519 发布季报",
                          "ts": "2026-10-21T08:00:00Z"}]}
    return Observation(
        source_id="news", snapshot_id="snap_nw", entity_type="news_page",
        entity_id="sina_finance",
        clock=SourceClock(collected_at="2026-10-21T09:00:00Z"),
        status="complete", payload=payload,
    )


def _yt_obs():
    payload = {"info": {"id": "vid1", "title": "quant talk",
                        "published_at": "2026-10-19T00:00:00Z"}}
    return Observation(
        source_id="youtube_envelope", snapshot_id="snap_yt",
        entity_type="video_metadata", entity_id="vid1",
        clock=SourceClock(collected_at="2026-10-20T00:00:00Z",
                          platform_published_at="2026-10-19T00:00:00Z"),
        status="complete", payload=payload,
    )


def test_normalize_symbol():
    assert normalize_symbol("SH600519") == "SH600519"
    assert normalize_symbol("600519") == "SH600519"        # 自动补板
    assert normalize_symbol("000001") == "SZ000001"
    assert normalize_symbol("830799") == "BJ830799"
    assert normalize_symbol("sh600519") == "SH600519"      # 小写
    assert normalize_symbol("abc") is None
    assert normalize_symbol("123") is None
    assert normalize_symbol("1234567") is None             # 7位无合法板


def test_extract_symbols():
    out = extract_symbols("提及 600519 和 000001 的帖子")
    assert out == ["SH600519", "SZ000001"]
    assert extract_symbols("无代码文本") == []


def test_xueqiu_resolves_astock():
    ents = resolve_entities(_xq_obs())
    assert CanonicalEntity("astock", "SH600519") in ents


def test_guba_and_xueqiu_merge_same_symbol():
    # 雪球 SH600519 与股吧 600519 应归并到同一 astock 实体
    xq = resolve_entities(_xq_obs())
    gb = resolve_entities(_guba_obs())
    assert CanonicalEntity("astock", "SH600519") in xq
    assert CanonicalEntity("astock", "SH600519") in gb


def test_news_extracts_symbol_and_endpoint():
    ents = resolve_entities(_news_obs())
    assert CanonicalEntity("astock", "SH600519") in ents
    assert CanonicalEntity("news", "sina_finance") in ents


def test_youtube_uses_yt_domain():
    ents = resolve_entities(_yt_obs())
    assert CanonicalEntity("yt", "vid1") in ents


def test_rollup_aggregates_cross_source():
    groups = rollup_by_entity([_xq_obs(), _guba_obs(), _news_obs(), _yt_obs()])
    key = ("astock", "SH600519")
    assert key in groups
    r = groups[key]
    assert r.sources == {"xueqiu", "guba", "news"}   # 跨三源归并
    assert r.observation_count == 3
    assert groups[("yt", "vid1")].sources == {"youtube_envelope"}
    # 消费契约结构化可序列化
    d = r.to_dict()
    assert d["source_count"] == 3
    assert "SH600519" in d["key"]
