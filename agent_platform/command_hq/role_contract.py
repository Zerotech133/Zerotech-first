# -*- coding: utf-8 -*-
"""HQ 五角色意见契约 · **最小本地占位实现**（K-ORG-20261101-HQORCH / W-C2 依赖）。

背景：W-C1 正式实现 `agent_platform/command_hq/role_contract.py` 由 HQ-A 并行开发；
本单需要可独立测试的编排器，故先提供与专项计划 §3.2 对齐的**最小本地契约**
（RoleOpinion v1 校验/序列化/去重）。HQ-A 合入后以其正式版替换本文件即可
（接口保持：ROLES / POSITIONS / RoleOpinion / RoleError / SCHEMA）。

契约（专项计划 §3.2，schema=hq_role_opinion/v1）：
  role            ZHUGE|ZHAOYUN|HUANGZHONG|GUANYU|TIANFENG
  position        support|conditional|oppose|abstain
  evidence        [{kind:file|command|test|live|hash, ref, digest, verified}]
  risks/conditions/escalate/escalate_reason

规则（计划 §3.2 规则 1-4）：
  1) evidence 有 verified=true 时必须给 ref + digest（无证据不得声称已验证）；
  2) oppose 必须给替代方案/不可接受原因（conditions 或 escalate_reason）；
  3) conditional 必须给可验收条件（conditions 非空）；
  4) escalate=true 必须给 escalate_reason（田丰必须出飞书卡，不得内部消化）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

SCHEMA = "hq_role_opinion/v1"
ROLES = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
ROLE_CN = {
    "TIANFENG": "田丰",
    "ZHUGE": "诸葛亮",
    "ZHAOYUN": "赵云",
    "HUANGZHONG": "黄忠",
    "GUANYU": "关羽",
}
POSITIONS = ("support", "conditional", "oppose", "abstain")
EVIDENCE_KINDS = ("file", "command", "test", "live", "hash")


class RoleError(ValueError):
    """RoleOpinion 契约非法。"""


@dataclass
class RoleOpinion:
    role: str
    topic_id: str
    recommendation: str
    position: str = "abstain"
    round: int = 1
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    escalate: bool = False
    escalate_reason: str = ""
    created_at: str = ""
    schema: str = SCHEMA

    # ---- 校验 ----
    def validate(self) -> "RoleOpinion":
        issues: List[str] = []
        if self.schema != SCHEMA:
            issues.append("schema=%r 期望 %r" % (self.schema, SCHEMA))
        if self.role not in ROLES:
            issues.append("role=%r 不在五角色 %s" % (self.role, list(ROLES)))
        if not str(self.topic_id or "").strip():
            issues.append("topic_id 缺失")
        if not str(self.recommendation or "").strip():
            issues.append("recommendation 缺失")
        if self.position not in POSITIONS:
            issues.append("position=%r 不在 %s" % (self.position, list(POSITIONS)))
        if int(self.round) < 1:
            issues.append("round 必须 >=1")
        if not isinstance(self.evidence, list):
            issues.append("evidence 必须为数组")
        else:
            for i, item in enumerate(self.evidence):
                if not isinstance(item, dict):
                    issues.append("evidence[%d] 不是对象" % i)
                    continue
                kind = str(item.get("kind") or "")
                ref = str(item.get("ref") or "").strip()
                if kind not in EVIDENCE_KINDS:
                    issues.append("evidence[%d].kind=%r 不在 %s"
                                  % (i, kind, list(EVIDENCE_KINDS)))
                if item.get("verified") is True:
                    if not ref:
                        issues.append("evidence[%d] verified=true 但缺 ref" % i)
                    if not str(item.get("digest") or "").strip():
                        issues.append("evidence[%d] verified=true 但缺 digest" % i)
        if self.position == "conditional" and not self.conditions:
            issues.append("conditional 必须给可验收条件 conditions")
        if self.position == "oppose" and not (self.conditions or self.escalate_reason):
            issues.append("oppose 必须给替代方案/不可接受原因（conditions 或 escalate_reason）")
        if self.escalate and not str(self.escalate_reason or "").strip():
            issues.append("escalate=true 必须给 escalate_reason")
        if issues:
            raise RoleError("RoleOpinion 非法：%s" % "；".join(issues))
        return self

    # ---- 序列化 / 去重 ----
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["role_cn"] = ROLE_CN.get(self.role, "")
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RoleOpinion":
        if not isinstance(data, dict):
            raise RoleError("RoleOpinion 必须是对象，实得 %r" % type(data).__name__)
        allowed = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        opinion = cls(**kwargs)
        return opinion.validate()

    def fingerprint(self) -> str:
        """同题同角色同轮去重键（内容无关，防重复意见刷屏）。"""
        raw = "%s|%s|%s" % (self.topic_id, self.role, self.round)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def content_sha256(self) -> str:
        payload = {k: v for k, v in self.to_dict().items() if k != "created_at"}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def dedupe_opinions(items: List[RoleOpinion]) -> List[RoleOpinion]:
    """去重：同 (topic_id, role, round) 保留首份；返回按 ROLES 稳定排序后的列表。"""
    seen = {}
    for op in items:
        op.validate()
        key = op.fingerprint()
        if key not in seen:
            seen[key] = op
    order = {role: i for i, role in enumerate(ROLES)}
    return sorted(seen.values(), key=lambda o: order.get(o.role, len(ROLES)))
