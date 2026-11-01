# -*- coding: utf-8 -*-
"""指挥部 W-C3：讨论共识判定 + 飞书定版卡 dry-run + 决策回写（K-ORG-20261101-HQCONS）。

依据：`docs/plans/2026-11-01_指挥部五角色智能体与验收闭环研发计划_v1.md` §4 W-C3。

职责边界（只出判定/卡片/账本，不执行、不发飞书）：
- 输入五角色 RoleOpinion（`hq_role_opinion/v1`，dict 或同名字段对象均可）；
- 轮次/同题去重（同题同角色同轮保留 created_at 最新，重复项显式留存）；
- 反对保留（oppose/abstain 意见原样保留，不因多数票丢弃）；
- 分歧聚类（按 position + 最高风险等级聚合，确定性排序）；
- 共识判定：
    * L0/L1 且田丰+黄忠+关羽均为支持（support/conditional）、支持数 >=3/5、
      无 P0 风险、无 escalate → `auto_ready`；
    * 显式 L2 / 出现 P0 / 任一 opinion escalate / 缺角色 / 支持数不足 → `escalate`；
- 飞书定版卡 payload schema（`hq_final_card/v1`）：topic/options A/B/C/推荐/风险/截止/
  decision_id；`dry_run_card()` **只渲染不发送**（模块不 import 任何网络/飞书 SDK）；
- 回写（`hq_decision/v1`）：decision_id/选择/理由/owner；超时（截止后）→ `blocked`，
  沉默/超时**不默认同意**、不自动执行。

幂等：同一 topic + 同一组最新轮意见 → 同一 `decision_id`（内容哈希，与输入顺序无关）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_OPINION = "hq_role_opinion/v1"
SCHEMA_CONSENSUS = "hq_consensus/v1"
SCHEMA_CARD = "hq_final_card/v1"
SCHEMA_DECISION = "hq_decision/v1"

ROLES = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
REQUIRED_SUPPORTERS = ("TIANFENG", "HUANGZHONG", "GUANYU")
POSITIONS = ("support", "conditional", "oppose", "abstain")
SUPPORT_POSITIONS = ("support", "conditional")
RISK_LEVELS = ("L0", "L1", "L2")
OPTION_KEYS = ("A", "B", "C")
CARD_STATUSES = ("pending_decision", "decided", "blocked")
DECISION_STATUSES = ("decided", "blocked")
MIN_SUPPORT = 3

# 角色别名（历史/席位口径 → 统一契约名）：田丰=A0、诸葛亮=AI1
ROLE_ALIASES = {
    "TIANFENG": "TIANFENG", "A0": "TIANFENG", "田丰": "TIANFENG",
    "ZHUGE": "ZHUGE", "AI1": "ZHUGE", "诸葛亮": "ZHUGE",
    "ZHAOYUN": "ZHAOYUN", "赵云": "ZHAOYUN",
    "HUANGZHONG": "HUANGZHONG", "黄忠": "HUANGZHONG",
    "GUANYU": "GUANYU", "关羽": "GUANYU",
}

SEVERITY_RE = re.compile(r"\bP([0-3])\b")
L2_RE = re.compile(r"\bL2\b", re.I)
DECISION_ID_RE = re.compile(r"^HQD-[A-Za-z0-9_-]{3,64}$")
EVIDENCE_KINDS = ("file", "command", "test", "live", "hash")

_CST = timezone(timedelta(hours=8))


class ConsensusError(ValueError):
    """输入/契约不合法（失败关闭，不做静默降级）。"""


# ---------------------------------------------------------------- 基础工具
def _now() -> datetime:
    return datetime.now(_CST)


def _iso(dt) -> str:
    return dt.astimezone(_CST).isoformat(timespec="seconds")


def _parse_dt(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_CST)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_CST)


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha10(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()[:10]


def _get(op, key, default=None):
    """兼容 dict 与 RoleOpinion 对象（字段名同契约）。"""
    if isinstance(op, dict):
        return op.get(key, default)
    return getattr(op, key, default)


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _str_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_role(role) -> str:
    """席位/角色名归一；未知角色抛 ConsensusError（不做猜测）。"""
    raw = _text(role, 40).upper()
    role_name = ROLE_ALIASES.get(raw, raw)
    if role_name not in ROLES:
        raise ConsensusError("未知角色: %r（允许 %s）" % (role, "/".join(ROLES)))
    return role_name


def normalize_risk_level(value) -> str:
    raw = _text(value, 8).upper()
    if raw not in RISK_LEVELS:
        raise ConsensusError("风险级别非法: %r（允许 L0/L1/L2）" % (value,))
    return raw


def risk_severity(risks) -> str:
    """从风险列表中取最高严重度（P0 > P1 > P2 > P3）；无风险返回 ""。"""
    best = None
    for item in _str_list(risks):
        for match in SEVERITY_RE.finditer(item):
            level = int(match.group(1))
            if best is None or level < best:
                best = level
    return "" if best is None else "P%d" % best


def _normalize_evidence(evidence, *, strict: bool = True) -> list:
    out = []
    if evidence is None:
        return out
    if isinstance(evidence, dict):
        evidence = [evidence]
    if not isinstance(evidence, (list, tuple)):
        raise ConsensusError("evidence 必须是列表")
    for item in evidence:
        if isinstance(item, str):
            out.append({"kind": "file", "ref": item, "digest": "",
                        "verified": False})
            continue
        if not isinstance(item, dict):
            raise ConsensusError("evidence 项必须是 dict/str: %r" % (item,))
        kind = _text(item.get("kind"), 20).lower()
        ref = _text(item.get("ref"), 500)
        digest = _text(item.get("digest"), 200)
        verified = bool(item.get("verified", False))
        if kind and kind not in EVIDENCE_KINDS:
            raise ConsensusError("evidence.kind 非法: %r" % (kind,))
        if verified and (not ref or not digest):
            # 规划 §3 铁律：无证据不得写 verified=true
            msg = "evidence verified=true 必须同时有 ref/digest"
            if strict:
                raise ConsensusError(msg)
            verified = False
        out.append({"kind": kind or "file", "ref": ref,
                    "digest": digest, "verified": verified})
    return out


def normalize_opinion(op, *, strict: bool = True) -> dict:
    """RoleOpinion v1 → 规范化 dict；不合法即抛 ConsensusError（失败关闭）。"""
    topic_id = _text(_get(op, "topic_id"), 120)
    if not topic_id:
        raise ConsensusError("RoleOpinion 缺少 topic_id")
    role = normalize_role(_get(op, "role"))
    round_no = _to_int(_get(op, "round"), 1)
    if round_no < 1:
        raise ConsensusError("round 必须 >= 1: %r" % (round_no,))
    position = _text(_get(op, "position"), 20).lower()
    if position not in POSITIONS:
        raise ConsensusError("position 非法: %r（允许 %s）"
                             % (position, "/".join(POSITIONS)))
    recommendation = _text(_get(op, "recommendation"), 1000)
    evidence = _normalize_evidence(_get(op, "evidence"), strict=strict)
    risks = _str_list(_get(op, "risks"))
    conditions = _str_list(_get(op, "conditions"))
    escalate = bool(_get(op, "escalate", False))
    escalate_reason = _text(_get(op, "escalate_reason"), 500)
    created = _parse_dt(_get(op, "created_at"))
    if position == "oppose" and not (recommendation or risks):
        # 规划 §3 规则 2：oppose 必须给替代方案或明确不可接受原因
        raise ConsensusError("%s oppose 必须给 recommendation 或 risks" % role)
    if position == "conditional" and not conditions:
        raise ConsensusError("%s conditional 必须给可验收 conditions" % role)
    if escalate and not escalate_reason and strict:
        raise ConsensusError("%s escalate=true 必须给 escalate_reason" % role)
    return {
        "schema": SCHEMA_OPINION,
        "topic_id": topic_id,
        "role": role,
        "round": round_no,
        "position": position,
        "recommendation": recommendation,
        "evidence": evidence,
        "risks": risks,
        "conditions": conditions,
        "escalate": escalate,
        "escalate_reason": escalate_reason,
        "created_at": _iso(created) if created else "",
    }


def _opinion_sort_key(op: dict) -> tuple:
    return (op["round"], ROLES.index(op["role"]))


# ---------------------------------------------------------------- 轮次/去重/聚类
def dedupe_opinions(opinions, *, strict: bool = True):
    """同题同角色同轮去重：保留 created_at 最新；时间相同则按内容哈希稳定取舍。

    返回 `(kept, duplicates)`：kept 按 (round, 角色契约顺序) 排序；
    duplicates 为被替换掉的规范化意见（显式留存，不静默丢弃）。
    """
    normalized = [normalize_opinion(op, strict=strict) for op in opinions]
    winners = {}
    duplicates = []
    for op in normalized:
        key = (op["topic_id"], op["role"], op["round"])
        current = winners.get(key)
        if current is None:
            winners[key] = op
            continue
        prev_ts = _parse_dt(current.get("created_at"))
        cur_ts = _parse_dt(op.get("created_at"))
        if cur_ts is not None and prev_ts is not None and cur_ts == prev_ts:
            # 同一时间戳的重复：按规范化内容排序，保证与输入顺序无关
            take_new = _canonical(op) > _canonical(current)
        elif cur_ts is not None and (prev_ts is None or cur_ts > prev_ts):
            take_new = True
        else:
            take_new = False
        if take_new:
            duplicates.append(current)
            winners[key] = op
        else:
            duplicates.append(op)
    kept = sorted(winners.values(), key=_opinion_sort_key)
    duplicates.sort(key=lambda x: (x["topic_id"], x["role"], x["round"],
                                    x.get("created_at", "")))
    return kept, duplicates


def latest_rounds(opinions) -> dict:
    """每个角色取最大 round 的最新意见（用于判定；历史意见另存 history）。"""
    latest = {}
    for op in opinions:
        cur = latest.get(op["role"])
        if cur is None or op["round"] > cur["round"]:
            latest[op["role"]] = op
    return latest


def cluster_disagreements(opinions) -> list:
    """分歧聚类：按 (position, 最高风险等级) 聚合，角色/建议/风险确定性排序。"""
    buckets = {}
    for op in opinions:
        severity = risk_severity(op.get("risks")) or "-"
        key = "%s/%s" % (op["position"], severity)
        bucket = buckets.setdefault(key, {
            "key": key,
            "position": op["position"],
            "severity": severity,
            "roles": set(),
            "recommendations": set(),
            "risks": set(),
            "conditions": set(),
        })
        bucket["roles"].add(op["role"])
        if op.get("recommendation"):
            bucket["recommendations"].add(op["recommendation"])
        for risk in op.get("risks") or []:
            bucket["risks"].add(risk)
        for cond in op.get("conditions") or []:
            bucket["conditions"].add(cond)
    out = []
    for key in sorted(buckets):
        bucket = buckets[key]
        out.append({
            "key": key,
            "position": bucket["position"],
            "severity": bucket["severity"],
            "roles": sorted(bucket["roles"]),
            "count": len(bucket["roles"]),
            "recommendations": sorted(bucket["recommendations"]),
            "risks": sorted(bucket["risks"]),
            "conditions": sorted(bucket["conditions"]),
        })
    return out


# ---------------------------------------------------------------- 共识判定
def make_decision_id(topic_id: str, latest: dict) -> str:
    """同一 topic + 同一组最新轮意见 → 稳定 decision_id（与输入顺序无关）。"""
    payload = {"topic_id": topic_id, "latest": {}}
    for role in ROLES:
        op = latest.get(role)
        if not op:
            continue
        payload["latest"][role] = {
            "round": op["round"],
            "position": op["position"],
            "recommendation": op["recommendation"],
            "risks": sorted(op["risks"]),
            "conditions": sorted(op["conditions"]),
            "escalate": op["escalate"],
        }
    dates = []
    for op in latest.values():
        created = _parse_dt(op.get("created_at"))
        if created is not None:
            dates.append(created.astimezone(_CST).strftime("%Y%m%d"))
    if dates:
        day = min(dates)
    else:
        # 无 created_at 时也不能把“今天”混进 id（跨日重跑会漂移）：
        # 优先取 topic_id 里的 YYYYMMDD；否则固定 00000000 占位。
        match = re.search(r"(20\d{6})", topic_id)
        day = match.group(1) if match else "00000000"
    return "HQD-%s-%s" % (day, _sha10(payload))


def build_consensus(opinions, *, topic_id: str = "", risk_level: str = "L1",
                    strict: bool = True) -> dict:
    """五角色意见 → 共识判定（`hq_consensus/v1`）。

    auto_ready 条件（全部满足）：
      1) 风险级别 L0/L1；
      2) 五角色齐全（缺任一 → escalate，不拿四票冒充五席）；
      3) 支持数 >= 3/5，且田丰+黄忠+关羽均在支持集（support/conditional）；
      4) 无 P0 风险、无任何 opinion escalate（含 L2 标记）。
    其它情况一律 escalate（可带 blockers 明细），绝不默认通过。
    """
    level = normalize_risk_level(risk_level)
    items = list(opinions or [])
    if not items:
        raise ConsensusError("至少需要 1 条 RoleOpinion")
    kept, dropped = dedupe_opinions(items, strict=strict)
    if not kept:
        raise ConsensusError("去重后无有效意见")
    topics = sorted({op["topic_id"] for op in kept})
    if topic_id:
        topic_id = _text(topic_id, 120)
        kept = [op for op in kept if op["topic_id"] == topic_id]
        dropped = [op for op in dropped if op["topic_id"] == topic_id]
        if not kept:
            raise ConsensusError("topic_id=%s 没有有效意见" % topic_id)
    elif len(topics) == 1:
        topic_id = topics[0]
    else:
        raise ConsensusError("存在多个 topic_id，必须显式传 topic_id: %r" % topics)

    latest = latest_rounds(kept)
    support_roles = sorted(r for r, op in latest.items()
                           if op["position"] in SUPPORT_POSITIONS)
    oppose_roles = sorted(r for r, op in latest.items()
                          if op["position"] == "oppose")
    conditional_roles = sorted(r for r, op in latest.items()
                               if op["position"] == "conditional")
    abstain_roles = sorted(r for r, op in latest.items()
                           if op["position"] == "abstain")
    missing_roles = [r for r in ROLES if r not in latest]

    blockers = []
    if level == "L2":
        blockers.append({"code": "l2_forced_escalate",
                         "detail": "风险级别 L2：AI 只给建议，必须人工定版"})
    for op in latest.values():
        if L2_RE.search(" ".join(op.get("risks") or [])):
            blockers.append({"code": "l2_marker", "role": op["role"],
                             "detail": "意见风险含 L2 标记"})
    for op in latest.values():
        if risk_severity(op.get("risks")) == "P0":
            blockers.append({"code": "p0_risk", "role": op["role"],
                             "detail": "出现 P0 风险（必须升级）"})
    for op in latest.values():
        if op.get("escalate"):
            blockers.append({"code": "opinion_escalate", "role": op["role"],
                             "detail": op.get("escalate_reason")
                             or "角色要求升级"})
    for role in missing_roles:
        blockers.append({"code": "role_missing", "role": role,
                         "detail": "五角色意见不齐（缺 %s）" % role})
    if len(support_roles) < MIN_SUPPORT:
        blockers.append({
            "code": "quorum_insufficient",
            "detail": "支持 %d/5 < %d/5" % (len(support_roles), MIN_SUPPORT),
        })
    missing_required = [r for r in REQUIRED_SUPPORTERS
                        if r not in support_roles]
    if missing_required:
        blockers.append({
            "code": "required_supporter_missing",
            "detail": "田丰+黄忠+关羽未全部支持（缺 %s）"
                      % "+".join(missing_required),
        })

    verdict = "escalate" if blockers else "auto_ready"
    reason = "" if verdict == "auto_ready" else (
        "%s：%s" % (blockers[0]["code"], blockers[0]["detail"]))
    decision_id = make_decision_id(topic_id, latest)
    return {
        "schema": SCHEMA_CONSENSUS,
        "topic_id": topic_id,
        "risk_level": level,
        "verdict": verdict,
        "escalate": verdict == "escalate",
        "auto_ready": verdict == "auto_ready",
        "reason": reason,
        "blockers": blockers,
        "decision_id": decision_id,
        "quorum": len(support_roles),
        "min_support": MIN_SUPPORT,
        "required_supporters": list(REQUIRED_SUPPORTERS),
        "support_roles": support_roles,
        "conditional_roles": conditional_roles,
        "oppose_roles": oppose_roles,
        "abstain_roles": abstain_roles,
        "missing_roles": missing_roles,
        "latest": {role: latest[role] for role in ROLES if role in latest},
        "history": kept,
        "duplicates": dropped,
        "duplicate_count": len(dropped),
        "clusters": cluster_disagreements(list(latest.values())),
        "oppositions": [op for op in latest.values()
                        if op["position"] == "oppose"],
    }


def ensure_countersign(consensus: dict) -> dict:
    """auto_ready 时校验证据门槛（无证据的“共识”不算共识）；不满足则降级 escalate。"""
    if not isinstance(consensus, dict) or consensus.get("schema") != SCHEMA_CONSENSUS:
        raise ConsensusError("consensus schema 非法")
    if consensus.get("verdict") != "auto_ready":
        return consensus
    out = dict(consensus)
    weak = []
    for role, op in (consensus.get("latest") or {}).items():
        evidence = op.get("evidence") or []
        if not any(item.get("verified") and item.get("ref")
                   for item in evidence):
            weak.append(role)
    if weak:
        out["verdict"] = "escalate"
        out["auto_ready"] = False
        out["escalate"] = True
        out["blockers"] = list(out.get("blockers") or []) + [{
            "code": "no_verified_evidence",
            "detail": "以下角色无 verified 证据：%s" % "+".join(weak),
        }]
        out["reason"] = "no_verified_evidence：" + "+".join(weak)
    return out


# ---------------------------------------------------------------- 飞书定版卡
def _normalize_options(options) -> list:
    """选项 A/B/C：支持 dict/list/str；键必须 ∈ {A,B,C} 且不重复。"""
    if options is None:
        options = [{"key": "A", "label": "同意"},
                   {"key": "B", "label": "附条件同意"},
                   {"key": "C", "label": "不同意"}]
    items = []
    if isinstance(options, dict):
        for key, label in options.items():
            items.append({"key": key, "label": label})
    elif isinstance(options, (list, tuple)):
        for idx, item in enumerate(options):
            if idx >= len(OPTION_KEYS) and not isinstance(item, dict):
                raise ConsensusError("options 最多 3 个（A/B/C 定版）")
            if isinstance(item, dict):
                items.append({"key": item.get("key") or OPTION_KEYS[idx],
                              "label": item.get("label") or "",
                              "impact": item.get("impact") or ""})
            else:
                items.append({"key": OPTION_KEYS[idx], "label": str(item)})
    else:
        raise ConsensusError("options 必须是 dict/list")
    if not items:
        raise ConsensusError("options 不能为空")
    if len(items) < 2:
        raise ConsensusError("options 至少 2 个（A/B/C 定版）")
    out = []
    seen = set()
    for idx, item in enumerate(items):
        key = _text(item.get("key"), 1).upper()
        if key not in OPTION_KEYS:
            raise ConsensusError("选项键必须是 A/B/C: %r" % (key,))
        if key in seen:
            raise ConsensusError("选项键重复: %r" % (key,))
        seen.add(key)
        label = _text(item.get("label"), 200) or ("选项 " + key)
        out.append({"key": key, "label": label,
                    "impact": _text(item.get("impact"), 500)})
    out.sort(key=lambda x: OPTION_KEYS.index(x["key"]))
    return out


def _normalize_recommendation(recommendation) -> dict:
    if recommendation is None:
        return {"option": "", "text": ""}
    if isinstance(recommendation, dict):
        return {"option": _text(recommendation.get("option"), 1).upper(),
                "text": _text(recommendation.get("text"), 1000)}
    return {"option": "", "text": _text(recommendation, 1000)}


def build_final_card(topic, *, options=None, recommendation=None, risks=None,
                     deadline=None, decision_id: str = "",
                     created_at=None, status: str = "pending_decision") -> dict:
    """构造飞书定版卡 payload（`hq_final_card/v1`）；纯数据，不发送。"""
    if isinstance(topic, str):
        topic = {"id": "", "title": topic}
    if not isinstance(topic, dict):
        raise ConsensusError("topic 必须是 str/dict")
    topic_id = _text(topic.get("id") or topic.get("topic_id"), 120)
    title = _text(topic.get("title"), 200)
    if not (topic_id or title):
        raise ConsensusError("topic 需要 id 或 title")
    options_norm = _normalize_options(options)
    recommendation_norm = _normalize_recommendation(recommendation)
    if recommendation_norm["option"] and recommendation_norm["option"] not in {
            item["key"] for item in options_norm}:
        raise ConsensusError("推荐选项不在 options 内: %r"
                             % recommendation_norm["option"])
    risks_norm = _str_list(risks)
    deadline_dt = _parse_dt(deadline)
    if deadline_dt is None:
        raise ConsensusError("deadline 必填（ISO 时间）")
    did = _text(decision_id, 64)
    if did and not DECISION_ID_RE.match(did):
        raise ConsensusError("decision_id 非法: %r" % (did,))
    if not did:
        did = "HQD-%s-%s" % (
            (deadline_dt.astimezone(_CST).strftime("%Y%m%d")),
            _sha10({"topic": topic_id, "title": title,
                    "options": options_norm, "recommendation": recommendation_norm,
                    "risks": sorted(risks_norm), "deadline": _iso(deadline_dt)}))
    if status not in CARD_STATUSES:
        raise ConsensusError("card status 非法: %r" % (status,))
    created = _parse_dt(created_at) or _now()
    return {
        "schema": SCHEMA_CARD,
        "topic": {"id": topic_id, "title": title,
                  "summary": _text(topic.get("summary"), 1000)},
        "options": options_norm,
        "recommendation": recommendation_norm,
        "risks": risks_norm,
        "deadline": _iso(deadline_dt),
        "decision_id": did,
        "status": status,
        "created_at": _iso(created),
        "delivery": {"dry_run": True, "channel": "feishu", "sent": False},
    }


def build_escalation_card(consensus: dict, *, deadline, options=None,
                          recommendation=None, risks=None) -> dict:
    """escalate 判定 → 飞书定版卡；auto_ready 不建卡（内部执行，不打扰老田）。"""
    if not isinstance(consensus, dict) or consensus.get("schema") != SCHEMA_CONSENSUS:
        raise ConsensusError("consensus schema 非法")
    if not consensus.get("escalate"):
        raise ConsensusError("auto_ready 判定不应生成定版卡（不冒充人工定版）")
    risk_list = _str_list(risks)
    if not risk_list:
        seen = set()
        for op in (consensus.get("latest") or {}).values():
            for risk in op.get("risks") or []:
                if risk not in seen:
                    seen.add(risk)
                    risk_list.append(risk)
    if not risk_list:
        risk_list = [b.get("detail") or b.get("code") or "需人工定版"
                     for b in consensus.get("blockers") or []]
    rec = recommendation
    if rec is None:
        rec = "需老田定版：%s" % (consensus.get("reason") or "存在分歧/风险")
    return build_final_card(
        {"id": consensus.get("topic_id", ""),
         "title": consensus.get("topic_id", ""),
         "summary": consensus.get("reason", "")},
        options=options,
        recommendation=rec,
        risks=risk_list,
        deadline=deadline,
        decision_id=consensus.get("decision_id", ""),
    )


def render_card_text(card: dict) -> str:
    """确定性纯文本渲染（dry-run 预览；也可作为飞书卡正文草稿）。"""
    validate_card(card)
    lines = [
        "【指挥部定版卡 · %s】%s" % (card["decision_id"], card["topic"]["title"]),
        "主题：%s" % (card["topic"]["summary"] or card["topic"]["title"]),
        "选项：",
    ]
    for item in card["options"]:
        lines.append("  %s) %s%s" % (
            item["key"], item["label"],
            ("（影响：%s）" % item["impact"]) if item["impact"] else ""))
    rec = card["recommendation"]
    lines.append("推荐：%s%s" % (
        (rec["option"] + " · ") if rec["option"] else "", rec["text"] or "（无）"))
    lines.append("风险：%s" % ("；".join(card["risks"]) or "（无）"))
    lines.append("截止：%s（超时=blocked，沉默不默认同意）" % card["deadline"])
    lines.append("状态：%s · dry-run 仅渲染，未发送" % card["status"])
    return "\n".join(lines)


def dry_run_card(card: dict) -> dict:
    """dry-run：返回 payload + 渲染文本；**明确不发送**（sent=False）。"""
    validate_card(card)
    return {
        "schema": SCHEMA_CARD,
        "dry_run": True,
        "sent": False,
        "channel": "feishu",
        "decision_id": card["decision_id"],
        "payload": card,
        "rendered": render_card_text(card),
    }


def validate_card(card) -> dict:
    if not isinstance(card, dict) or card.get("schema") != SCHEMA_CARD:
        raise ConsensusError("card schema 非法")
    for key in ("topic", "options", "recommendation", "risks", "deadline",
                "decision_id", "status"):
        if key not in card:
            raise ConsensusError("card 缺少字段: %s" % key)
    if not DECISION_ID_RE.match(_text(card["decision_id"], 64)):
        raise ConsensusError("card.decision_id 非法")
    if card.get("status") not in CARD_STATUSES:
        raise ConsensusError("card.status 非法")
    keys = [item.get("key") for item in card.get("options") or []]
    if not keys or len(set(keys)) != len(keys) or any(k not in OPTION_KEYS for k in keys):
        raise ConsensusError("card.options 非法: %r" % (keys,))
    if _parse_dt(card.get("deadline")) is None:
        raise ConsensusError("card.deadline 非法")
    return card


# ---------------------------------------------------------------- 超时 / 回写
def is_timed_out(card, now=None) -> bool:
    """截止时间严格晚于当前才算未超时；等于/超过均视为超时（失败关闭）。"""
    deadline = _parse_dt(card.get("deadline") if isinstance(card, dict) else card)
    if deadline is None:
        raise ConsensusError("缺少可解析 deadline")
    current = _parse_dt(now) or _now()
    return current >= deadline


def build_decision(decision_id: str, choice: str, reason: str, owner: str, *,
                   topic_id: str = "", decided_at=None,
                   source: str = "feishu_callback") -> dict:
    """回写记录（`hq_decision/v1`）：decision_id/选择/理由/执行 owner。"""
    did = _text(decision_id, 64)
    if not DECISION_ID_RE.match(did):
        raise ConsensusError("decision_id 非法: %r" % (decision_id,))
    choice_key = _text(choice, 1).upper()
    if choice_key not in OPTION_KEYS:
        raise ConsensusError("choice 必须 A/B/C: %r" % (choice,))
    reason_text = _text(reason, 2000)
    if not reason_text:
        raise ConsensusError("reason 必填（一次一议，留痕）")
    owner_text = _text(owner, 120)
    if not owner_text:
        raise ConsensusError("owner 必填（执行责任人）")
    decided = _parse_dt(decided_at) or _now()
    return {
        "schema": SCHEMA_DECISION,
        "decision_id": did,
        "topic_id": _text(topic_id, 120),
        "status": "decided",
        "choice": choice_key,
        "reason": reason_text,
        "owner": owner_text,
        "decided_at": _iso(decided),
        "source": _text(source, 60),
        "timeout": False,
        "auto_approved": False,
    }


def timeout_decision(decision_id: str, card=None, *, topic_id: str = "",
                     now=None, source: str = "timeout") -> dict:
    """超时/沉默 → blocked（不默认同意、不自动执行）；只有真超时才允许调用。"""
    did = _text(decision_id, 64)
    if not did and isinstance(card, dict):
        did = _text(card.get("decision_id"), 64)
    if not DECISION_ID_RE.match(did):
        raise ConsensusError("decision_id 非法: %r" % (decision_id,))
    if card is not None and not is_timed_out(card, now):
        raise ConsensusError("未到截止时间，不得标记 blocked")
    current = _parse_dt(now) or _now()
    return {
        "schema": SCHEMA_DECISION,
        "decision_id": did,
        "topic_id": _text(topic_id or (card or {}).get("topic", {}).get("id"), 120),
        "status": "blocked",
        "choice": None,
        "reason": "deadline_timeout：截止前未选择，按规则 blocked（不默认同意）",
        "owner": "",
        "decided_at": _iso(current),
        "source": _text(source, 60),
        "timeout": True,
        "auto_approved": False,
    }


def resolve_card(card, choice, reason, owner, *, now=None,
                 source: str = "feishu_callback") -> dict:
    """飞书回调回写：未超时 → decided；已超时 → blocked（迟到的选择不生效）。"""
    validate_card(card)
    if is_timed_out(card, now):
        return timeout_decision(card["decision_id"], card,
                                topic_id=card["topic"]["id"], now=now,
                                source=source)
    return build_decision(card["decision_id"], choice, reason, owner,
                          topic_id=card["topic"]["id"],
                          decided_at=now, source=source)


def handle_callback(card, payload, *, now=None, ledger_path=None,
                    source: str = "feishu_callback") -> dict:
    """飞书定版卡回调 → 决议记录（可选落 JSONL 账）；超时一律 blocked。

    payload 字段：`choice|option`（A/B/C）、`reason|note`、`owner|operator|sub`。
    未超时才要求 choice/reason/owner；已超时则忽略选择，写 blocked（不默认同意）。
    """
    if not isinstance(payload, dict):
        raise ConsensusError("callback payload 必须是 dict")
    choice = payload.get("choice") or payload.get("option") or ""
    reason = payload.get("reason") or payload.get("note") or ""
    owner = payload.get("owner") or payload.get("operator") or payload.get("sub")
    record = resolve_card(card, choice, reason, owner, now=now, source=source)
    if ledger_path is not None:
        writeback_decision(record, ledger_path)
    return record


def validate_decision(record) -> dict:
    if not isinstance(record, dict) or record.get("schema") != SCHEMA_DECISION:
        raise ConsensusError("decision schema 非法")
    for key in ("decision_id", "status", "choice", "reason", "owner",
                "decided_at", "auto_approved"):
        if key not in record:
            raise ConsensusError("decision 缺少字段: %s" % key)
    if record["status"] not in DECISION_STATUSES:
        raise ConsensusError("decision.status 非法: %r" % (record["status"],))
    if record["status"] == "blocked":
        if record.get("choice") is not None:
            raise ConsensusError("blocked 记录不得有 choice（不默认同意）")
        if record.get("auto_approved"):
            raise ConsensusError("blocked 记录不得 auto_approved")
    else:
        if record.get("choice") not in OPTION_KEYS:
            raise ConsensusError("decided.choice 必须 A/B/C")
        if not record.get("reason") or not record.get("owner"):
            raise ConsensusError("decided 记录 reason/owner 必填")
        if record.get("timeout"):
            raise ConsensusError("decided 记录不得 timeout=true")
    return record


_DECISION_KEY_FIELDS = ("status", "choice", "reason", "owner")


def writeback_decision(record: dict, ledger_path, *,
                       idempotent: bool = True) -> Path:
    """把回写记录 append 到 JSONL 决议账；返回落盘路径（调用方负责传生产路径）。

    幂等（一次一议）：同 decision_id 重复回调 → 内容一致则 no-op；内容冲突 → 拒绝
    （blocked 后迟到的选择也不得改写为 decided）。
    """
    validate_decision(record)
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if idempotent and path.is_file():
        for existing in load_decision_ledger(path):
            if existing.get("decision_id") != record["decision_id"]:
                continue
            if all(existing.get(key) == record.get(key)
                   for key in _DECISION_KEY_FIELDS):
                return path
            raise ConsensusError(
                "decision_id %s 已有不同决议（一次一议；禁止改写/重复定版）"
                % record["decision_id"])
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def load_decision_ledger(ledger_path) -> list:
    """读取 JSONL 决议账（只读；坏行抛 ConsensusError，不做静默丢弃）。"""
    path = Path(ledger_path)
    if not path.is_file():
        return []
    rows = []
    for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise ConsensusError("ledger 第 %d 行非法 JSON: %s"
                                 % (line_no, exc)) from exc
    return rows
