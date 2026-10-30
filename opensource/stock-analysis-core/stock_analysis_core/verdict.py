# -*- coding: utf-8 -*-
"""规则研判：可配置因子打分 + 评级切档 + 风险覆盖（默认参数仅示例，非投资建议）。"""
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .types import VerdictResult


@dataclass
class VerdictPolicy:
    weights: Dict[str, float] = field(default_factory=lambda: {
        "quality": 0.30, "growth": 0.20, "valuation": 0.20, "trend": 0.15, "event": 0.15,
    })
    cuts: Dict[str, float] = field(default_factory=lambda: {
        "buy": 80, "observe_up": 65, "observe": 50, "avoid": 35,
    })
    risk_debt_ratio: float = 85.0     # 负债率 ≥ 该值视为困境
    risk_negative_profit: bool = True  # 最近期亏损视为困境
    policy_version: str = "demo-1"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _quality(f: Dict[str, Any]) -> float:
    debt = f.get("debt_ratio") or 0
    gm = f.get("gross_margin") or 0
    np_ = f.get("net_profit") or 0
    base = 70.0 - max(0.0, debt - 40.0) * 0.8 + gm * 0.5
    if np_ < 0:
        base -= 35
    return _clamp(base)


def _growth(f: Dict[str, Any]) -> float:
    rev = f.get("revenue_yoy")
    np_ = f.get("net_profit_yoy")
    vals = [v for v in (rev, np_) if v is not None]
    if not vals:
        return 50.0
    m = sum(vals) / len(vals)
    return _clamp(50.0 + m * 200.0)


def _valuation(f: Dict[str, Any], policy: VerdictPolicy) -> float:
    pct = f.get("ps_pct_hist")
    if pct is None:
        return 50.0
    return _clamp(100.0 - pct)


def _trend(t: Dict[str, Any]) -> float:
    s = 50.0
    if t.get("ret_60") is not None:
        s += t["ret_60"] * 100
    if t.get("above_ma20"):
        s += 10
    return _clamp(s)


RISK_CATEGORIES = {"诉讼", "质押", "退市/ST", "问询/监管", "减持/增持"}
POSITIVE_CATEGORIES = {"回购", "业绩", "分红", "股权激励"}


def _event(events: Dict[str, Any]) -> float:
    cats = set(events.get("categories") or [])
    s = 50.0
    s -= 10 * len(cats & RISK_CATEGORIES)
    s += 5 * len(cats & POSITIVE_CATEGORIES)
    return _clamp(s)


def compute(snap, feat: Dict[str, Any], policy: VerdictPolicy | None = None) -> VerdictResult:
    p = policy or VerdictPolicy()
    fun = feat.get("fundamental", {})
    val = feat.get("valuation", {})
    tech = feat.get("technical", {})
    ev = feat.get("events", {})
    factors = {
        "quality": round(_quality(fun), 1),
        "growth": round(_growth(fun), 1),
        "valuation": round(_valuation(val, p), 1),
        "trend": round(_trend(tech), 1),
        "event": round(_event(ev), 1),
    }
    total = sum(factors[k] * p.weights.get(k, 0) for k in factors)
    total = round(total, 1)
    if total >= p.cuts["buy"]:
        rating = "买入"
    elif total >= p.cuts["observe_up"]:
        rating = "观察偏多"
    elif total >= p.cuts["observe"]:
        rating = "观察"
    elif total < p.cuts["avoid"]:
        rating = "回避"
    else:
        rating = "观察"
    horizon = "中期"
    confidence = round(_clamp(0.5 + (factors["quality"] - 50) / 200, 0.3, 0.85), 2)
    distress = (fun.get("debt_ratio") or 0) >= p.risk_debt_ratio or (
        p.risk_negative_profit and (fun.get("net_profit") or 0) < 0)
    if distress:
        rating = "事件观察"
        horizon = "事件驱动"
    signals = [
        "PS 历史分位 %s" % val.get("ps_pct_hist"),
        "60日收益 %s" % tech.get("ret_60"),
        "负债率 %s%%" % fun.get("debt_ratio"),
        "事件数 %s" % ev.get("count"),
    ]
    triggers = ["季报/预告验证业绩", "放量突破阶段高点或回踩均线企稳"]
    invalidations = ["跌破阶段低点", "出现监管立案/退市风险", "核心逻辑被证伪"]
    thesis = "综合评分 %.1f/100（质量%.0f/成长%.0f/估值%.0f/趋势%.0f/事件%.0f）" % (
        total, factors["quality"], factors["growth"], factors["valuation"], factors["trend"], factors["event"])
    return VerdictResult(rating=rating, score=total, confidence=confidence, horizon=horizon,
                         thesis=thesis, key_signals=signals, triggers=triggers,
                         invalidations=invalidations, factors=factors, policy_version=p.policy_version)
