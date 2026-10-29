# -*- coding: utf-8 -*-
"""paper MVP 风控门（2026-10-30 WO-INV-PAPER-MVP）。

只读评估 D1 决策 + 当前纸面组合，输出 pass/reduce/block 与 target_weight：
- 单票 ≤10%、行业 ≤30%、单日回撤 ≥5% 禁止新开/加仓；
- parse_ok=false / status≠done / 价格缺失 → block；
- fallback/degraded 决策禁止加仓；
- confidence < 0.5 → 目标仓位减半；
- SELL/STRONG_SELL → 平仓（不反向做空）；HOLD/WATCH → 不动。
"""
from __future__ import annotations

import io
import json
import os

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_config.json")
VALID = ("STRONG_BUY", "BUY", "HOLD", "WATCH", "SELL", "STRONG_SELL")
BUYS = ("STRONG_BUY", "BUY")
SELLS = ("SELL", "STRONG_SELL")
EPS = 1e-9


def load_config(path=None):
    return json.load(io.open(path or DEFAULT_CONFIG, encoding="utf-8"))


def _positions(state):
    out = {}
    for sym, p in ((state or {}).get("positions") or {}).items():
        if isinstance(p, dict):
            out[sym] = float(p.get("weight") or 0.0)
    return out


def _industry_of(symbol, cfg):
    return (cfg.get("industries") or {}).get(symbol) or cfg.get("unknown_industry") or "未分类"


def evaluate(decision, state=None, cfg=None, day_pnl_pct=None):
    """返回 {verdict, action, target_weight, risk_level, reasons[], industry}。"""
    cfg = cfg or load_config()
    lim = cfg.get("limits") or {}
    targets = cfg.get("targets") or {}
    rec = str((decision or {}).get("recommendation") or "").upper()
    conf = float((decision or {}).get("confidence") or 0.0)
    price = (decision or {}).get("price_at_decision")
    symbol = str((decision or {}).get("symbol") or "")
    reasons = []
    industry = _industry_of(symbol, cfg)
    cur = _positions(state)
    cur_w = cur.get(symbol, 0.0)
    ind_w = sum(w for s, w in cur.items() if _industry_of(s, cfg) == industry)

    def done(verdict, action, target, risk, code, detail="", extra=None):
        r = {"verdict": verdict, "action": action,
             "target_weight": round(max(0.0, min(1.0, float(target))), 6),
             "risk_level": risk, "industry": industry,
             "reasons": reasons + ([{"code": code, "detail": detail}] if code else [])}
        if extra:
            r.update(extra)
        return r

    if (decision or {}).get("status") != "done" or not (decision or {}).get("parse_ok"):
        return done("block", "block", 0.0, "HIGH", "decision_invalid",
                    "status/parse_ok 不合格")
    if rec not in VALID:
        return done("block", "block", 0.0, "HIGH", "recommendation_invalid", rec)
    if not isinstance(price, (int, float)) or price <= 0:
        return done("block", "block", 0.0, "HIGH", "price_missing", str(price))

    if rec in SELLS:
        return done("pass", "close", 0.0, "MEDIUM", "sell_close", "平仓，不反向做空")

    if rec in ("HOLD", "WATCH"):
        return done("pass", "hold", cur_w, "LOW",
                    "hold", "HOLD/WATCH 不新增仓位")

    # BUY / STRONG_BUY
    if (decision or {}).get("fallback") or (decision or {}).get("degraded"):
        return done("block", "block", 0.0, "HIGH", "degraded_decision",
                    "fallback/degraded 决策禁止加仓")
    if day_pnl_pct is not None and float(day_pnl_pct) <= -float(lim.get("daily_drawdown_max", 0.05)):
        return done("block", "block", 0.0, "HIGH", "drawdown_limit",
                    "单日回撤 %.2f%% 触及上限" % (float(day_pnl_pct) * 100))

    original = float(targets.get(rec) or 0.0)
    target = original
    if conf < float(lim.get("min_confidence", 0.5)):
        target *= 0.5
        reasons.append({"code": "confidence_low",
                        "detail": "confidence %.2f < %.2f，仓位减半" % (
                            conf, float(lim.get("min_confidence", 0.5)))})

    single_max = float(lim.get("single_name_max", 0.10))
    room = single_max - cur_w
    if target > room:
        target = max(0.0, room)
        reasons.append({"code": "single_name_cap",
                        "detail": "单票上限 %.0f%%（当前 %.1f%%）" % (
                            single_max * 100, cur_w * 100)})
    if target <= EPS:
        return done("block", "block", 0.0, "HIGH", "single_name_cap_full", "单票仓位已满")

    industry_max = float(lim.get("industry_max", 0.30))
    iroom = industry_max - ind_w
    if target > iroom:
        target = max(0.0, iroom)
        reasons.append({"code": "industry_cap",
                        "detail": "行业 %s 上限 %.0f%%（当前 %.1f%%）" % (
                            industry, industry_max * 100, ind_w * 100)})
    if target <= EPS:
        return done("block", "block", 0.0, "HIGH", "industry_cap_full", "行业仓位已满")

    cash = 1.0 - sum(cur.values())
    if target > cash:
        target = max(0.0, cash)
        reasons.append({"code": "cash_cap", "detail": "现金不足"})
    if target <= EPS:
        return done("block", "block", 0.0, "HIGH", "cash_insufficient", "无可用现金")

    verdict = "pass" if abs(target - original) <= EPS and not any(
        r["code"] == "confidence_low" for r in reasons) else "reduce"
    risk = "LOW" if verdict == "pass" and conf >= 0.7 else "MEDIUM"
    return done(verdict, "buy", target, risk, "", "", {"confidence": conf, "original_target": original})
