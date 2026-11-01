# -*- coding: utf-8 -*-
"""指挥部输出契约 RoleOpinion v1（2026-11-01 K-ORG-20261101-HQROLE / 专项计划 §3.2）。

纯函数 + 只读：只做校验/规范化/序列化/去重；**不联网、不调模型、不写盘、不执行**。

契约（schema ``hq_role_opinion/v1``）::

    {
      "schema": "hq_role_opinion/v1",
      "topic_id": "HQ-20261101-001",
      "role": "ZHUGE|ZHAOYUN|HUANGZHONG|GUANYU|TIANFENG",
      "round": 1,
      "position": "support|conditional|oppose|abstain",
      "recommendation": "一句话结论",
      "evidence": [{"kind": "file|command|test|live|hash",
                    "ref": "...", "digest": "sha256:...", "verified": true}],
      "risks": ["P0/P1 风险，必须可执行"],
      "conditions": ["附条件通过时必填"],
      "escalate": false,
      "escalate_reason": "",
      "created_at": "2026-11-01T10:00:00+08:00"
    }

规则（专项计划 §3.2）：

1. 每条 evidence 必须有可复核 ``ref``（kind ∈ file/command/test/live/hash）；
   hash 类必须带 ``sha256:<64hex>`` digest；**无证据不得写 verified=true**；
2. ``oppose`` 必须给替代方案（conditions）或明确「不可接受原因」（risks）；
3. ``conditional`` 必须给可验收条件（conditions）；
4. ``escalate=true`` 时必须给 ``escalate_reason``（田丰出飞书卡，不内部消化）。

本模块不评价意见对错、不代签、不生成决策；只保证五角色输出可机读、可复核、可去重。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

SCHEMA = "hq_role_opinion/v1"
ROLES = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
POSITIONS = ("support", "conditional", "oppose", "abstain")
EVIDENCE_KINDS = ("file", "command", "test", "live", "hash")
REQUIRED_FIELDS = ("schema", "topic_id", "role", "round", "position",
                   "recommendation", "evidence", "risks", "conditions",
                   "escalate", "escalate_reason", "created_at")
DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
MAX_TEXT = 4000


class RoleOpinionError(ValueError):
    """RoleOpinion 校验失败（携带全部错误，便于一次性修正）。"""

    def __init__(self, errors):
        self.errors = [str(item) for item in (errors or [])] or ["unknown_role_opinion_error"]
        super().__init__("; ".join(self.errors))


def _err(errors, path, message):
    errors.append("%s: %s" % (path, message))


def _text(value, errors, path, *, required=True, allow_empty=True, limit=MAX_TEXT):
    if value is None:
        if required:
            _err(errors, path, "必填")
        return ""
    if not isinstance(value, str):
        _err(errors, path, "必须是字符串")
        return ""
    value = value.strip()
    if not value and not allow_empty:
        _err(errors, path, "不能为空")
    if len(value) > limit:
        _err(errors, path, "超长（>%d）" % limit)
    return value


def _text_list(value, errors, path):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        _err(errors, path, "必须是字符串数组")
        return []
    out = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _err(errors, "%s[%d]" % (path, index), "必须是非空字符串")
            continue
        if len(item.strip()) > MAX_TEXT:
            _err(errors, "%s[%d]" % (path, index), "超长（>%d）" % MAX_TEXT)
            continue
        out.append(item.strip())
    return out


def _parse_created_at(value, errors, path):
    if not isinstance(value, str) or not value.strip():
        _err(errors, path, "必须是 ISO8601 时间串（如 2026-11-01T10:00:00+08:00）")
        return ""
    text = value.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _err(errors, path, "不是合法 ISO8601：%r" % text)
    return text


def validate_evidence(evidence) -> list:
    """统一 evidence 校验（file/command/test/live/hash + ref/digest）。→ errors。"""
    errors = []
    if evidence is None:
        return errors
    if not isinstance(evidence, (list, tuple)):
        return ["evidence: 必须是数组"]
    for index, item in enumerate(evidence):
        path = "evidence[%d]" % index
        if not isinstance(item, dict):
            _err(errors, path, "必须是对象")
            continue
        kind = item.get("kind")
        if not isinstance(kind, str) or kind.strip().lower() not in EVIDENCE_KINDS:
            _err(errors, path + ".kind",
                 "必须是 %s 之一" % "/".join(EVIDENCE_KINDS))
            kind = ""
        else:
            kind = kind.strip().lower()
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            _err(errors, path + ".ref", "必须有可复核 ref（文件路径/命令/用例/实时端点）")
        elif len(ref.strip()) > 1000:
            _err(errors, path + ".ref", "超长（>1000）")
        digest = item.get("digest")
        if digest not in (None, ""):
            if not isinstance(digest, str) or not DIGEST_RE.match(digest.strip()):
                _err(errors, path + ".digest", "必须是 sha256:<64hex>")
        elif kind == "hash":
            _err(errors, path + ".digest", "kind=hash 必须给 sha256 digest")
        verified = item.get("verified")
        if verified is not None and not isinstance(verified, bool):
            _err(errors, path + ".verified", "必须是布尔值")
        if verified is True and not (isinstance(ref, str) and ref.strip()):
            _err(errors, path + ".verified", "无证据 ref 不得 verified=true")
    return errors


def validate_opinion(opinion) -> list:
    """校验 RoleOpinion v1，返回错误列表（空列表=合法）。纯函数、不写盘。"""
    errors = []
    if not isinstance(opinion, dict):
        return ["opinion: 必须是 JSON 对象"]
    for field in REQUIRED_FIELDS:
        if field not in opinion:
            _err(errors, field, "缺失必填字段")
    schema = opinion.get("schema")
    if schema != SCHEMA:
        _err(errors, "schema", "必须是 %r" % SCHEMA)
    topic_id = _text(opinion.get("topic_id"), errors, "topic_id", allow_empty=False, limit=120)
    role = _text(opinion.get("role"), errors, "role", allow_empty=False)
    if role and role.upper() not in ROLES:
        _err(errors, "role", "必须是 %s 之一" % "/".join(ROLES))
    round_no = opinion.get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 1:
        _err(errors, "round", "必须是 >=1 的整数")
    position = _text(opinion.get("position"), errors, "position", allow_empty=False)
    if position and position.lower() not in POSITIONS:
        _err(errors, "position", "必须是 %s 之一" % "/".join(POSITIONS))
    _text(opinion.get("recommendation"), errors, "recommendation", allow_empty=False)
    evidence = opinion.get("evidence")
    if not isinstance(evidence, (list, tuple)):
        _err(errors, "evidence", "必须是数组（可为空数组，但空数组不得含 verified=true）")
        evidence = []
    errors.extend(validate_evidence(evidence))
    risks = _text_list(opinion.get("risks"), errors, "risks")
    conditions = _text_list(opinion.get("conditions"), errors, "conditions")
    escalate = opinion.get("escalate")
    if not isinstance(escalate, bool):
        _err(errors, "escalate", "必须是布尔值")
    escalate_reason = _text(opinion.get("escalate_reason"), errors,
                            "escalate_reason", allow_empty=True)
    _parse_created_at(opinion.get("created_at"), errors, "created_at")
    # 规则 2/3/4
    if position and position.lower() == "oppose" and not (risks or conditions):
        _err(errors, "position",
             "oppose 必须给替代方案（conditions）或明确不可接受原因（risks）")
    if position and position.lower() == "conditional" and not conditions:
        _err(errors, "conditions", "conditional 必须给可验收条件（非空）")
    if escalate is True and not escalate_reason:
        _err(errors, "escalate_reason", "escalate=true 时必须给升级原因")
    return errors


def normalize_opinion(opinion):
    """规范化（role 大写/position 小写/去首尾空白/evidence kind 小写），非法 → RoleOpinionError。"""
    errors = validate_opinion(opinion)
    if errors:
        raise RoleOpinionError(errors)
    evidence = []
    for item in opinion.get("evidence") or []:
        entry = {"kind": str(item.get("kind") or "").strip().lower(),
                 "ref": str(item.get("ref") or "").strip()}
        if item.get("digest") not in (None, ""):
            entry["digest"] = str(item["digest"]).strip()
        entry["verified"] = bool(item.get("verified") is True)
        for extra_key in ("note", "sha16"):
            if extra_key in item:
                entry[extra_key] = item[extra_key]
        evidence.append(entry)
    return {
        "schema": SCHEMA,
        "topic_id": str(opinion["topic_id"]).strip(),
        "role": str(opinion["role"]).strip().upper(),
        "round": int(opinion["round"]),
        "position": str(opinion["position"]).strip().lower(),
        "recommendation": str(opinion["recommendation"]).strip(),
        "evidence": evidence,
        "risks": [str(x).strip() for x in opinion.get("risks") or []],
        "conditions": [str(x).strip() for x in opinion.get("conditions") or []],
        "escalate": bool(opinion["escalate"]),
        "escalate_reason": str(opinion.get("escalate_reason") or "").strip(),
        "created_at": str(opinion["created_at"]).strip(),
    }


def make_opinion(role, topic_id, *, position="abstain", recommendation="",
                 evidence=(), risks=(), conditions=(), escalate=False,
                 escalate_reason="", round=1, created_at=None):
    """构造并校验一份 RoleOpinion v1（不调模型；供角色适配器调用）。"""
    payload = {
        "schema": SCHEMA,
        "topic_id": topic_id,
        "role": role,
        "round": round,
        "position": position,
        "recommendation": recommendation or "（未给结论）",
        "evidence": list(evidence or []),
        "risks": list(risks or []),
        "conditions": list(conditions or []),
        "escalate": escalate,
        "escalate_reason": escalate_reason,
        "created_at": created_at or _now_iso(),
    }
    return normalize_opinion(payload)


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def serialize_opinion(opinion, *, indent=None) -> str:
    """规范化后确定性序列化（UTF-8 文本，不写文件）。"""
    normalized = normalize_opinion(opinion)
    return json.dumps(normalized, ensure_ascii=False, indent=indent, sort_keys=False)


def deserialize_opinion(text):
    """解析 + 校验 JSON 文本 → 规范化 dict；坏 JSON → RoleOpinionError。"""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RoleOpinionError(["json: 解析失败（%s）" % exc])
    return normalize_opinion(payload)


def dedupe_key(opinion):
    """同题同角色同轮次去重键（策略：保留最新一份）。"""
    normalized = normalize_opinion(opinion)
    return (normalized["topic_id"], normalized["role"], normalized["round"])


def opinion_fingerprint(opinion) -> str:
    """内容指纹（规范化 JSON 的 sha256），用于内容级去重。"""
    normalized = normalize_opinion(opinion)
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _created_ts(opinion) -> float:
    try:
        return datetime.fromisoformat(
            str(opinion.get("created_at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def dedupe_opinions(opinions, *, by="role_round"):
    """去重：``role_round``（默认）同题同角色同轮保留最新；``content`` 按内容指纹。

    返回 ``{"unique": [...], "duplicates": [...], "input": n}``；不改原对象。
    """
    if by not in ("role_round", "content"):
        raise ValueError("by 必须是 role_round 或 content")
    items = list(opinions or [])
    unique = []
    seen = {}
    duplicates = []
    for opinion in items:
        key = dedupe_key(opinion) if by == "role_round" else opinion_fingerprint(opinion)
        if key not in seen:
            seen[key] = opinion
            unique.append(opinion)
            continue
        duplicates.append(key if isinstance(key, tuple) else str(key))
        if by == "role_round" and _created_ts(opinion) > _created_ts(seen[key]):
            seen[key] = opinion
            unique = [opinion if dedupe_key(item) == key else item for item in unique]
    return {"unique": unique, "duplicates": duplicates, "input": len(items)}
