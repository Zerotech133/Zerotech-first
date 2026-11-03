# -*- coding: utf-8 -*-
"""watch trigger adapter（2026-10-30 WO-WATCH-TRIGGER-20261030）。

把盯盘侧 selection/evidence + 交易信号 enrichment 成 trading_bot_flow 的
pending 人工确认。只建确认，不执行；缺 symbol/price/action 一律 fail-closed。

背景：QUANT_UPSTREAM_REPORT 只有 report_id/evidence_hash 等元数据，没有
symbol/price，不能直接触发交易；本适配器要求显式 signal 补充，禁止臆造。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from agent_platform import trading_bot_flow as flow  # noqa: E402


class WatchTriggerError(ValueError):
    pass


_CODE = re.compile(r"(\d{6})")


def normalize_symbol(value, market=""):
    """6位代码 + 可选市场 sh/sz/bj；只做格式归一，不猜标的。"""
    m = _CODE.search(str(value or ""))
    if not m:
        raise WatchTriggerError("TRIGGER_SYMBOL_REQUIRED")
    code = m.group(1)
    mk = str(market or "").lower()
    if not mk:
        raw = str(value or "").lower()
        if "bj" in raw or code.startswith(("4", "8", "92")):
            mk = "bj"
        elif code.startswith("6"):
            mk = "sh"
        else:
            mk = "sz"
    return mk + code


def _action(value):
    s = str(value or "").strip().upper()
    mp = {"BUY": "BUY", "LONG": "BUY", "看多": "BUY",
          "SELL": "SELL", "SHORT": "SELL", "看空": "SELL",
          "WATCH": "WATCH", "HOLD": "WATCH", "观察": "WATCH"}
    if s not in mp:
        raise WatchTriggerError("TRIGGER_ACTION_REQUIRED")
    return mp[s]


def _num(value, field, required=True):
    if value is None or value == "":
        if required:
            raise WatchTriggerError("TRIGGER_%s_REQUIRED" % field.upper())
        return None
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise WatchTriggerError("TRIGGER_%s_INVALID" % field.upper()) from exc
    if n <= 0:
        raise WatchTriggerError("TRIGGER_%s_INVALID" % field.upper())
    return n


def build_payload(signal, selection=None, evidence=None, report=None, profile="balanced"):
    """合成触发载荷；signal 必须显式给 action/price（或 price_at/trigger_price）。"""
    if not isinstance(signal, dict):
        raise WatchTriggerError("TRIGGER_SIGNAL_REQUIRED")
    selection = selection or {}
    evidence = evidence or {}
    report = report or {}
    if report and report.get("non_executable") is not True:
        raise WatchTriggerError("TRIGGER_REPORT_NOT_NON_EXECUTABLE")

    raw_symbol = (signal.get("symbol") or signal.get("code")
                  or selection.get("symbol") or selection.get("code")
                  or evidence.get("code") or evidence.get("symbol"))
    symbol = normalize_symbol(raw_symbol, selection.get("market") or signal.get("market") or "")
    action = _action(signal.get("action") or signal.get("side"))
    price = _num(signal.get("price") if signal.get("price") is not None
                 else signal.get("trigger_price"), "price", True)
    stop = _num(signal.get("stop") or signal.get("stop_loss"), "stop", False)
    target = _num(signal.get("target") or signal.get("target_price"), "target", False)
    confidence = signal.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.6
    except (TypeError, ValueError):
        confidence = 0.6
    report_id = str(signal.get("report_id") or report.get("report_id")
                    or evidence.get("event_id") or "")
    reason = (signal.get("reason") or report.get("report_kind")
              or "盯盘触发（已 enrichment）")
    return {
        "symbol": symbol,
        "name": signal.get("name") or selection.get("name") or "",
        "action": action,
        "price": price,
        "stop": stop,
        "target": target,
        "confidence": confidence,
        "reason": str(reason),
        "source": "WATCH_TOWER",
        "source_report_id": report_id,
        "profile": profile,
        "audience": signal.get("audience") or "external_user",
    }


def trigger(signal, selection=None, evidence=None, report=None,
            profile="balanced", data_dir=None):
    """建 pending 人工确认（不执行）；返回确认记录。"""
    payload = build_payload(signal, selection, evidence, report, profile)
    return flow.create_confirmation(
        symbol=payload["symbol"], name=payload["name"], action=payload["action"],
        price=payload["price"], stop=payload["stop"], target=payload["target"],
        confidence=payload["confidence"], reason=payload["reason"],
        source=payload["source"], source_report_id=payload["source_report_id"],
        profile=payload["profile"], audience=payload["audience"],
        source_decision_id=str(signal.get("decision_id") or ""),
        source_handoff_id=str(signal.get("handoff_id") or ""),
        source_watch_plan_id=str(signal.get("watch_plan_id") or ""),
        data_dir=data_dir,
    )


# ── IFS-HANDOFF：watch_plan.conditions → pending 人工确认（WO-IFS-HANDOFF-20261103）──
# 只建确认、不执行；conditions 缺 symbol/action/price、带执行字段、已过期一律 fail-closed。
WATCH_PLAN_EXECUTION_FIELDS = (
    "order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account",
)


def _plan_expired(plan):
    text = str((plan or {}).get("expires_at") or "").strip()
    if not text:
        return False
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        expires = datetime.fromisoformat(candidate)
    except ValueError:
        return True  # 时间非法按过期处理（fail-closed，不猜）
    if expires.tzinfo is None:
        expires = expires.astimezone()
    return expires <= datetime.now(expires.tzinfo)


def signal_from_watch_plan(plan):
    """watch_plan → trigger signal；只做字段映射与护栏，不发明数据。"""
    if not isinstance(plan, dict):
        raise WatchTriggerError("WATCH_PLAN_REQUIRED")
    plan_id = str(plan.get("plan_id") or "").strip()
    if not plan_id:
        raise WatchTriggerError("WATCH_PLAN_ID_REQUIRED")
    if _plan_expired(plan):
        raise WatchTriggerError("WATCH_PLAN_EXPIRED")
    conditions = plan.get("conditions")
    if not isinstance(conditions, dict) or not conditions:
        raise WatchTriggerError("WATCH_PLAN_CONDITIONS_REQUIRED")
    for key in WATCH_PLAN_EXECUTION_FIELDS:
        if key in conditions and conditions.get(key) not in (None, "", [], {}):
            raise WatchTriggerError("WATCH_PLAN_EXECUTION_FIELD_FORBIDDEN")
    raw_symbol = conditions.get("symbol") or conditions.get("code")
    if not str(raw_symbol or "").strip():
        raise WatchTriggerError("TRIGGER_SYMBOL_REQUIRED")
    action = conditions.get("action") or conditions.get("side")
    if not str(action or "").strip():
        raise WatchTriggerError("TRIGGER_ACTION_REQUIRED")
    price = conditions.get("price")
    if price is None:
        price = conditions.get("trigger_price")
    if price is None:
        raise WatchTriggerError("TRIGGER_PRICE_REQUIRED")
    confidence = conditions.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.6
    except (TypeError, ValueError):
        confidence = 0.6
    method = str(plan.get("method") or "").strip()
    reason = str(conditions.get("reason") or "").strip() or (
        "D1 watch_plan %s（%s）" % (plan_id, method or "-")
    )
    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    return {
        "symbol": raw_symbol,
        "name": conditions.get("name") or "",
        "action": action,
        "price": price,
        "stop": conditions.get("stop") if conditions.get("stop") is not None else conditions.get("stop_loss"),
        "target": conditions.get("target") if conditions.get("target") is not None else conditions.get("target_price"),
        "confidence": confidence,
        "reason": reason,
        "report_id": "D1H-%s" % plan_id,
        "source": plan.get("seat") or "WATCH_TOWER",
        "audience": "external_user",
        "decision_id": source.get("decision_id") or "",
        "handoff_id": source.get("receipt_id") or "",
        "watch_plan_id": plan_id,
    }


def trigger_watch_plan(plan, profile="balanced", data_dir=None):
    """watch_plan → pending confirmation（不执行/不下单）；重复调用按 report_id 幂等。"""
    signal = signal_from_watch_plan(plan)
    return trigger(signal, profile=profile, data_dir=data_dir)
