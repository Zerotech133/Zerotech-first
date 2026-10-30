# -*- coding: utf-8 -*-
"""DecisionPolicy（2026-10-30 WO-D1-EVIDENCE，规则门外置）。

L4/证据/置信度规则不交给 LLM；模型只给建议，合法性由这里判。
"""
from __future__ import annotations

import hashlib

CONF_FLOOR = 0.5
BUY_SELL = {"BUY", "SELL", "STRONG_BUY", "STRONG_SELL"}
_DOWN = {"STRONG_BUY": "BUY", "BUY": "HOLD", "STRONG_SELL": "SELL", "SELL": "HOLD"}


def decision_id(symbol, date, run_id):
    raw = "%s|%s|%s" % (symbol, date, run_id)
    return "D1-%s-%s" % (str(date).replace("-", ""), hashlib.sha256(raw.encode()).hexdigest()[:10])


def _rec(dec):
    return str((dec or {}).get("recommendation") or "").strip().upper()


def apply_policy(dec, bundle, run_id="", date=""):
    """返回 {decision, decision_id, watch_reason, policy_checks, blocked}。"""
    d = dict(dec or {})
    raw = _rec(d)
    conf = d.get("confidence")
    checks = []
    watch_reason = None

    def fail(rule, detail):
        checks.append({"rule": rule, "pass": False, "detail": detail})

    if not bundle or not bundle.is_sufficient():
        fail("evidence_sufficient", "EvidenceBundle 不足，禁止把文本摘要当证据")
        d["recommendation"] = "WATCH"
        watch_reason = "no_evidence"
    elif bundle.l4_status in ("missing", "unresolved") and raw in BUY_SELL:
        fail("l4_lock", "L4=%s，禁止 BUY/SELL" % bundle.l4_status)
        d["recommendation"] = "WATCH"
        watch_reason = ("upstream_l4_missing" if bundle.l4_status == "missing"
                        else "l4_unresolved")
    elif raw in BUY_SELL and isinstance(conf, (int, float)) and conf < CONF_FLOOR:
        fail("confidence_floor", "confidence<%s，强制降档" % CONF_FLOOR)
        d["recommendation"] = _DOWN.get(raw, "WATCH")
        watch_reason = "confidence_floor"
    else:
        checks.append({"rule": "policy_gate", "pass": True, "detail": "ok"})

    d["confidence_raw"] = conf
    return {
        "decision": d,
        "decision_id": decision_id(bundle.symbol if bundle else "", date, run_id),
        "watch_reason": watch_reason,
        "policy_checks": checks,
        "blocked": bool(watch_reason),
    }
