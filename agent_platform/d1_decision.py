# -*- coding: utf-8 -*-
"""D1 最终裁决核心（P0，2026-11-01 WO-D1-DECISION-20261101）。

职责：把鲁肃（D1）的池内研究结论落成**正式 decision 记录**，供 D1 投研页/
军团/D1 投研会复用。当前阶段只落 decision 账，不生成交易单、不接实盘。

边界：
  · 只写 d1_decisions.jsonl（JSONL 追加，fsync）；
  · 五档枚举强校验：STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL；
  · 必须带 decision_id、code、confidence、rationale、decided_by=D1、paper_only=true；
  · 拒绝任何执行字段（order/qty/broker/execute/...），防止把“裁决”误当“下单”。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "d1_decision/v1"
TIERS = ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")
TIER_LABELS = {
    "STRONG_BUY": "强烈买入",
    "BUY": "买入",
    "HOLD": "持有/观察",
    "SELL": "卖出",
    "STRONG_SELL": "强烈卖出",
}
DECIDER = "D1"
DECIDER_NAME = "鲁肃"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "agent_platform" / "data" / "d1_decisions.jsonl"
DECISION_ID_RE = re.compile(r"^d1-\d{14}-[0-9a-f]{6}$")
FORBIDDEN_EXEC_KEYS = (
    "order", "order_id", "qty", "quantity", "broker", "execute", "execution",
    "trade", "trade_id", "fill", "fills", "account", "position_size",
)


class D1DecisionError(ValueError):
    """校验失败（API 层映射 400）。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_decision_id(now=None):
    dt = now or datetime.now()
    return "d1-" + dt.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)


def _clip(v, n):
    return str(v or "").strip()[:n]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(v):
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise D1DecisionError("bad_evidence", "evidence/dissent 必须是列表")
    return [_clip(x, 300) for x in v if _clip(x, 300)]


def validate_decision(payload):
    """校验并归一化 D1 decision；返回可落盘 dict。"""
    if not isinstance(payload, dict):
        raise D1DecisionError("bad_body", "body 必须是 JSON object")
    code = _clip(payload.get("code") or payload.get("candidate_id"), 32)
    if not code:
        raise D1DecisionError("missing_code", "code/candidate_id 必填")
    raw_tier = _clip(payload.get("tier") or payload.get("recommendation"), 24).upper()
    if raw_tier not in TIERS:
        raise D1DecisionError(
            "bad_tier",
            "tier 必须是 %s 之一（当前=%r）" % ("/".join(TIERS), raw_tier),
        )
    confidence = _f(payload.get("confidence"))
    if confidence is None or not (0.0 <= confidence <= 1.0):
        raise D1DecisionError("bad_confidence", "confidence 必须是 0~1 的数字")
    rationale = _clip(payload.get("rationale") or payload.get("reason"), 2000)
    if len(rationale) < 10:
        raise D1DecisionError("bad_rationale", "rationale 至少 10 字，须可追溯")
    decided_by = _clip(payload.get("decided_by") or DECIDER, 16).upper()
    if decided_by != DECIDER:
        raise D1DecisionError("bad_decider", "decided_by 必须是 D1（鲁肃），不能代签")
    if payload.get("paper_only") is False:
        raise D1DecisionError("not_paper", "本阶段只允许 paper_only=true，不接实盘")
    for key in FORBIDDEN_EXEC_KEYS:
        if key in payload and payload.get(key) not in (None, "", [], {}):
            raise D1DecisionError(
                "execution_field_forbidden",
                "decision 不得携带执行字段 %r；执行归 TR1/paper 链" % key,
            )
    did = _clip(payload.get("decision_id"), 40) or new_decision_id()
    if not DECISION_ID_RE.match(did):
        raise D1DecisionError("bad_decision_id", "decision_id 格式应为 d1-YYYYMMDDHHMMSS-xxxxxx")
    source = _clip(payload.get("source") or "d1_research", 32)
    return {
        "schema": SCHEMA,
        "decision_id": did,
        "code": code,
        "name": _clip(payload.get("name"), 64),
        "market": _clip(payload.get("market"), 8).upper(),
        "tier": raw_tier,
        "tier_label": TIER_LABELS[raw_tier],
        "confidence": round(confidence, 4),
        "rationale": rationale,
        "l1_basis": _clip(payload.get("l1_basis"), 1000),
        "evidence": _list(payload.get("evidence")),
        "dissent": _list(payload.get("dissent")),
        "decided_by": DECIDER,
        "decided_by_name": DECIDER_NAME,
        "decided_at": _clip(payload.get("decided_at"), 40) or _now(),
        "source": source,
        "paper_only": True,
        "decision_status": "done",
    }


def _ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_decisions(ledger=None):
    path = Path(ledger or DEFAULT_LEDGER)
    rows = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_decision(payload, ledger=None):
    """校验后追加一条 decision；重复 decision_id → D1DecisionError。"""
    normalized = validate_decision(payload)
    path = Path(ledger or DEFAULT_LEDGER)
    _ensure_parent(path)
    existing = read_decisions(path)
    if any(r.get("decision_id") == normalized["decision_id"] for r in existing):
        raise D1DecisionError("duplicate_decision", "decision_id 已存在：%s" % normalized["decision_id"])
    line = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return normalized


def latest_for(code, ledger=None):
    code = _clip(code, 32)
    for row in reversed(read_decisions(ledger)):
        if str(row.get("code")) == code:
            return row
    return None


def summary(ledger=None):
    rows = read_decisions(ledger)
    by_tier = {t: 0 for t in TIERS}
    for r in rows:
        t = str(r.get("tier") or "")
        if t in by_tier:
            by_tier[t] += 1
    return {
        "schema": "d1_decision_summary/v1",
        "total": len(rows),
        "by_tier": by_tier,
        "latest": rows[-1] if rows else None,
        "ledger": str(Path(ledger or DEFAULT_LEDGER)),
        "readonly": False,
    }
