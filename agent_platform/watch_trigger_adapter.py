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
        profile=payload["profile"], data_dir=data_dir,
    )
