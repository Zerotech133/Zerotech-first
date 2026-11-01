# -*- coding: utf-8 -*-
"""PII 检测与脱敏测试。安全约束：任何断言不得依赖明文输出。"""
from __future__ import annotations

from core.contracts import Observation, SourceClock
from core.pii import redact_observation, redact_text, scan_pii, scan_store_pii


def test_redact_email():
    t, n = redact_text("联系我 a.b@example.com 谢谢")
    assert n == 1
    assert "@" not in t.replace("@", "") or "example" not in t  # 邮箱被打码
    assert "a.b@example.com" not in t


def test_redact_cn_mobile():
    t, n = redact_text("微信 13800138000 加我")
    assert n == 1
    assert "13800138000" not in t


def test_redact_id_card():
    t, n = redact_text("身份证 11010119900307123X 已核验")
    assert n == 1
    assert "11010119900307123X" not in t


def test_scan_pii_counts_only():
    hits = scan_pii("邮箱 x@y.com 手机 13912345678")
    types = {h[0] for h in hits}
    assert "email" in types and "cn_mobile" in types
    assert len(hits) == 2


def test_redact_observation_recursive():
    obs = Observation(
        source_id="xq", snapshot_id="s1", entity_type="t", entity_id="e",
        clock=SourceClock(collected_at="2026-11-01T00:00:00Z"),
        status="complete", completeness="ok",
        payload={"items": [{"title": "加我 13700001111", "desc": "邮件 a@b.com"},
                           {"title": "普通文本"}]},
        allowed_use_cases=(), source_field="f", raw_sha256="x",
    )
    new = redact_observation(obs)
    # 原 obs 不被修改（不可变契约）
    assert "13700001111" in obs.payload["items"][0]["title"]
    assert "13700001111" not in new.payload["items"][0]["title"]
    assert "a@b.com" not in new.payload["items"][0]["desc"]
    assert "普通文本" in new.payload["items"][1]["title"]


def test_scan_store_pii():
    obs = Observation(
        source_id="news", snapshot_id="n1", entity_type="t", entity_id="e",
        clock=SourceClock(collected_at="2026-11-01T00:00:00Z"),
        status="complete", completeness="ok",
        payload={"items": [{"title": "联系方式 13611112222"}]},
        allowed_use_cases=(), source_field="f", raw_sha256="x",
    )
    obs2 = Observation(
        source_id="news", snapshot_id="n2", entity_type="t", entity_id="e",
        clock=SourceClock(collected_at="2026-11-01T00:00:00Z"),
        status="complete", completeness="ok", payload={"items": [{"title": "无敏感"}]},
        allowed_use_cases=(), source_field="f", raw_sha256="x",
    )
    counts = scan_store_pii([obs, obs2])
    assert counts.get("news") == 1
