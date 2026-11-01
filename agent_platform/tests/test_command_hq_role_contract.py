# -*- coding: utf-8 -*-
"""K-ORG-20261101-HQORCH（W-C2 依赖）：RoleOpinion v1 最小本地契约测试。

注：本文件是 HQ-A W-C1 正式 role_contract 到来前的**占位实现**测试；
正式版合入后应保持同接口（ROLES/POSITIONS/RoleOpinion/RoleError/SCHEMA）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

AP = Path(__file__).resolve().parent.parent
if str(AP) not in sys.path:
    sys.path.insert(0, str(AP))

from command_hq.role_contract import (  # noqa: E402
    POSITIONS,
    ROLES,
    SCHEMA,
    RoleError,
    RoleOpinion,
    dedupe_opinions,
)


def _base(**kw):
    data = {"role": "TIANFENG", "topic_id": "HQ-1",
            "recommendation": "同意", "position": "support"}
    data.update(kw)
    return data


def test_valid_support_roundtrip_and_sha():
    op = RoleOpinion.from_dict(_base())
    assert op.schema == SCHEMA and op.role == "TIANFENG"
    data = op.to_dict()
    assert data["role_cn"] == "田丰"
    again = RoleOpinion.from_dict(data)
    assert again.to_dict() == data
    assert op.content_sha256() == again.content_sha256()
    # created_at 不参与内容 hash（快照/重跑幂等）
    op2 = RoleOpinion.from_dict(_base(created_at="2026-11-01T10:00:00+08:00"))
    assert op2.content_sha256() == op.content_sha256()


@pytest.mark.parametrize("field,value", [
    ("role", "UNKNOWN"),
    ("position", "maybe"),
    ("schema", "wrong/v1"),
    ("topic_id", ""),
    ("recommendation", ""),
    ("round", 0),
])
def test_invalid_fields_rejected(field, value):
    with pytest.raises(RoleError):
        RoleOpinion.from_dict(_base(**{field: value}))


def test_conditional_requires_conditions():
    with pytest.raises(RoleError, match="conditional"):
        RoleOpinion.from_dict(_base(position="conditional"))
    op = RoleOpinion.from_dict(_base(position="conditional",
                                     conditions=["补 1 条 live 证据"]))
    assert op.position == "conditional" and op.conditions


def test_oppose_requires_alternative_or_reason():
    with pytest.raises(RoleError, match="oppose"):
        RoleOpinion.from_dict(_base(position="oppose"))
    assert RoleOpinion.from_dict(_base(position="oppose",
                                       conditions=["改走 B 方案"])).position == "oppose"
    assert RoleOpinion.from_dict(_base(position="oppose",
                                       escalate_reason="不可接受：无回滚")).position == "oppose"


def test_escalate_requires_reason():
    with pytest.raises(RoleError, match="escalate_reason"):
        RoleOpinion.from_dict(_base(escalate=True))
    op = RoleOpinion.from_dict(_base(escalate=True, escalate_reason="L2 需老田定版"))
    assert op.escalate is True


def test_evidence_verified_requires_ref_and_digest():
    bad1 = _base(evidence=[{"kind": "test", "verified": True, "digest": "sha256:x"}])
    with pytest.raises(RoleError, match="ref"):
        RoleOpinion.from_dict(bad1)
    bad2 = _base(evidence=[{"kind": "test", "verified": True, "ref": "pytest"}])
    with pytest.raises(RoleError, match="digest"):
        RoleOpinion.from_dict(bad2)
    bad3 = _base(evidence=[{"kind": "unknown", "ref": "x"}])
    with pytest.raises(RoleError, match="kind"):
        RoleOpinion.from_dict(bad3)
    ok = RoleOpinion.from_dict(_base(evidence=[
        {"kind": "test", "ref": "pytest -q", "verified": False},
        {"kind": "file", "ref": "a.py", "digest": "sha256:abc", "verified": True},
    ]))
    assert len(ok.evidence) == 2


def test_dedupe_opinions_by_role_round():
    ops = [
        RoleOpinion.from_dict(_base(role="ZHUGE", recommendation="第一份")),
        RoleOpinion.from_dict(_base(role="ZHUGE", recommendation="第二份")),  # 同键去重
        RoleOpinion.from_dict(_base(role="TIANFENG")),
        RoleOpinion.from_dict(_base(role="GUANYU", round=2)),
    ]
    out = dedupe_opinions(ops)
    assert [o.role for o in out] == ["TIANFENG", "ZHUGE", "GUANYU"]
    zhuge = [o for o in out if o.role == "ZHUGE"][0]
    assert zhuge.recommendation == "第一份"   # 保留首份


def test_constants_exposed():
    assert ROLES == ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
    assert set(POSITIONS) == {"support", "conditional", "oppose", "abstain"}
