# -*- coding: utf-8 -*-
"""RoleOpinion v1 契约测试（2026-11-01 K-ORG-20261101-HQROLE；专项计划 §3.2）。

覆盖：合法/缺字段/非法 position/oppose 无替代/conditional 无条件/escalate 无原因/
无证据 verified/hash digest/非法 kind/round/created_at/序列化往返/去重/构造工厂。
全部纯内存 + tmp，不联网、不调模型、不写生产。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[2]
if str(WT) not in sys.path:
    sys.path.insert(0, str(WT))

from agent_platform.command_hq import role_contract as rc  # noqa: E402

DIGEST = "sha256:" + "a" * 64


def _evidence(kind="file", ref="docs/plans/2026-11-01_指挥部五角色智能体与验收闭环研发计划_v1.md",
              digest=None, verified=True):
    entry = {"kind": kind, "ref": ref, "verified": verified}
    if digest:
        entry["digest"] = digest
    return entry


def _valid(**over):
    opinion = {
        "schema": rc.SCHEMA,
        "topic_id": "HQ-20261101-001",
        "role": "ZHUGE",
        "round": 1,
        "position": "support",
        "recommendation": "建议按 B 方案推进",
        "evidence": [_evidence()],
        "risks": ["P1：窗口紧，需以验收证据为准"],
        "conditions": [],
        "escalate": False,
        "escalate_reason": "",
        "created_at": "2026-11-01T10:00:00+08:00",
    }
    opinion.update(over)
    return opinion


def test_valid_opinion_has_no_errors():
    assert rc.validate_opinion(_valid()) == []


def test_missing_required_fields_reported():
    opinion = _valid()
    for field in ("topic_id", "position", "created_at"):
        opinion.pop(field)
    errors = rc.validate_opinion(opinion)
    joined = "\n".join(errors)
    for field in ("topic_id", "position", "created_at"):
        assert field in joined


def test_invalid_enum_position_role_schema():
    assert any("position" in e for e in rc.validate_opinion(_valid(position="maybe")))
    assert any("role" in e for e in rc.validate_opinion(_valid(role="LAOTIAN")))
    assert any("schema" in e for e in rc.validate_opinion(_valid(schema="x/v0")))


def test_round_and_created_at():
    for bad in (0, -1, True, "1"):
        assert any("round" in e for e in rc.validate_opinion(_valid(round=bad)))
    assert any("created_at" in e for e in rc.validate_opinion(_valid(created_at="2026/11/01")))


def test_evidence_kind_ref_digest():
    assert any("kind" in e for e in
               rc.validate_opinion(_valid(evidence=[_evidence(kind="vibes")])))
    assert any("ref" in e for e in
               rc.validate_opinion(_valid(evidence=[_evidence(ref=" ")])))
    hash_no_digest = rc.validate_opinion(_valid(evidence=[_evidence(kind="hash")]))
    assert any("digest" in e for e in hash_no_digest)
    bad_digest = rc.validate_opinion(
        _valid(evidence=[_evidence(kind="hash", digest="sha256:xyz")]))
    assert any("digest" in e for e in bad_digest)
    assert rc.validate_opinion(
        _valid(evidence=[_evidence(kind="hash", digest=DIGEST)])) == []
    # 无证据：允许空数组（但自然不存在 verified=true）
    assert rc.validate_opinion(_valid(evidence=[])) == []


def test_verified_requires_ref():
    entry = {"kind": "file", "ref": "", "verified": True}
    errors = rc.validate_opinion(_valid(evidence=[entry]))
    assert any("verified" in e or "ref" in e for e in errors)


def test_oppose_requires_alternative_or_reason():
    no_alt = rc.validate_opinion(_valid(position="oppose", risks=[], conditions=[]))
    assert any("oppose" in e for e in no_alt)
    assert rc.validate_opinion(
        _valid(position="oppose", risks=["P0：不可接受，证据不可复核"], conditions=[])) == []
    assert rc.validate_opinion(
        _valid(position="oppose", risks=[],
               conditions=["替代方案：先补 M4 会诊包再签"])) == []


def test_conditional_requires_conditions():
    assert any("conditional" in e or "conditions" in e for e in
               rc.validate_opinion(_valid(position="conditional", conditions=[])))
    assert rc.validate_opinion(
        _valid(position="conditional",
               conditions=["验收条件：A0 明示后由 ARCH1 补签"])) == []


def test_escalate_requires_reason():
    assert any("escalate_reason" in e for e in
               rc.validate_opinion(_valid(escalate=True, escalate_reason="")))
    assert rc.validate_opinion(
        _valid(escalate=True, escalate_reason="涉及 L2 授权，需老田定版")) == []


def test_normalize_serialize_roundtrip():
    raw = _valid(role="tianfeng", position="SUPPORT",
                 evidence=[_evidence(kind="FILE")])
    normalized = rc.normalize_opinion(raw)
    assert normalized["role"] == "TIANFENG"
    assert normalized["position"] == "support"
    assert normalized["evidence"][0]["kind"] == "file"
    text = rc.serialize_opinion(normalized)
    assert rc.deserialize_opinion(text) == normalized
    assert rc.serialize_opinion(normalized) == text, "序列化必须确定性/幂等"
    assert json.loads(text)["schema"] == rc.SCHEMA


def test_deserialize_bad_json_raises():
    with pytest.raises(rc.RoleOpinionError) as exc_info:
        rc.deserialize_opinion("{bad json")
    assert exc_info.value.errors


def test_make_opinion_factory_and_invalid_raises():
    opinion = rc.make_opinion(
        "HUANGZHONG", "HQ-20261101-002", position="support",
        recommendation="建议放行（证据齐）",
        evidence=[_evidence(kind="test", ref="pytest -q", digest=DIGEST, verified=True)],
        risks=["P1：抽样面有限"], round=2)
    assert rc.validate_opinion(opinion) == []
    assert opinion["role"] == "HUANGZHONG" and opinion["round"] == 2
    with pytest.raises(rc.RoleOpinionError) as exc_info:
        rc.make_opinion("ZHUGE", "HQ-X", position="conditional",
                        recommendation="x", evidence=[_evidence()])
    assert any("conditional" in e or "conditions" in e
               for e in exc_info.value.errors)


def test_dedupe_role_round_keeps_latest():
    early = rc.make_opinion("ZHUGE", "HQ-1", recommendation="早版",
                            evidence=[_evidence()],
                            created_at="2026-11-01T10:00:00+08:00")
    late = rc.make_opinion("ZHUGE", "HQ-1", recommendation="晚版",
                           evidence=[_evidence()],
                           created_at="2026-11-01T11:00:00+08:00")
    other = rc.make_opinion("ZHAOYUN", "HQ-1", recommendation="赵云版",
                            evidence=[_evidence()],
                            created_at="2026-11-01T10:30:00+08:00")
    report = rc.dedupe_opinions([early, late, other])
    assert report["input"] == 3
    assert report["duplicates"] == [("HQ-1", "ZHUGE", 1)]
    assert len(report["unique"]) == 2
    by_role = {op["role"]: op for op in report["unique"]}
    assert by_role["ZHUGE"]["recommendation"] == "晚版"
    assert rc.dedupe_key(early) == ("HQ-1", "ZHUGE", 1)


def test_dedupe_content_mode():
    one = rc.make_opinion("GUANYU", "HQ-2", recommendation="同一内容",
                          evidence=[_evidence()])
    two = rc.make_opinion("GUANYU", "HQ-2", recommendation="同一内容",
                          evidence=[_evidence()])
    report = rc.dedupe_opinions([one, two], by="content")
    assert report["input"] == 2 and len(report["unique"]) == 1
    assert report["duplicates"]
    assert rc.opinion_fingerprint(one) == rc.opinion_fingerprint(two)
