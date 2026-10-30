# -*- coding: utf-8 -*-
"""质量门框架 G1-G4（可扩展）。

G1 数据覆盖与时效；G2 可溯源；G3 口径一致；G4 交付完整（在报告渲染后由 pipeline 校验）。
"""
from dataclasses import dataclass
from typing import Any, Dict, List

from .types import CheckResult, Snapshot


@dataclass
class GateConfig:
    min_bars: int = 120
    min_valuation_days: int = 60
    min_fundamental_periods: int = 2
    min_events: int = 1
    max_stale_days: int = 10


def _gap_days(a: str, b: str) -> int:
    try:
        from datetime import datetime
        d1 = datetime.strptime(str(a)[:8], "%Y%m%d")
        d2 = datetime.strptime(str(b)[:8], "%Y%m%d")
        return abs((d2 - d1).days)
    except Exception:
        return 9999


def g1(snap: Snapshot, feat: Dict[str, Any], cfg: GateConfig) -> CheckResult:
    tech = feat.get("technical", {})
    val = feat.get("valuation", {})
    fun = feat.get("fundamental", {})
    ev = feat.get("events", {})
    checks = [
        tech.get("bars", 0) >= cfg.min_bars,
        val.get("days", 0) >= cfg.min_valuation_days,
        fun.get("periods", 0) >= cfg.min_fundamental_periods,
        ev.get("count", 0) >= cfg.min_events,
    ]
    issues = []
    names = ["bars>=%d" % cfg.min_bars, "valuation_days>=%d" % cfg.min_valuation_days,
             "fundamental_periods>=%d" % cfg.min_fundamental_periods, "events>=%d" % cfg.min_events]
    for ok, name in zip(checks, names):
        if not ok:
            issues.append("覆盖不足：" + name)
    stale = _gap_days(snap.as_of or tech.get("last_date", ""), tech.get("last_date", "")) if snap.as_of else 0
    if stale > cfg.max_stale_days:
        issues.append("数据滞后 %d 天" % stale)
    score = round(sum(checks) / len(checks) * 80 + (20 if stale <= cfg.max_stale_days else 0), 1)
    return CheckResult("G1", passed=all(checks) and stale <= cfg.max_stale_days, score=score, issues=issues)


def g2(snap: Snapshot) -> CheckResult:
    issues = []
    with_source = [e for e in snap.events if str(e.get("source", "")).strip()]
    if snap.events and len(with_source) < len(snap.events):
        issues.append("%d 条事件缺少 source" % (len(snap.events) - len(with_source)))
    bad_fund = [r for r in snap.fundamentals if not str(r.get("end_date", "")).strip()]
    if bad_fund:
        issues.append("%d 条财务记录缺少 end_date" % len(bad_fund))
    total = len(snap.events) + len(snap.fundamentals) + len(snap.valuation)
    ok = total > 0 and not issues
    return CheckResult("G2", passed=ok, score=100.0 if ok else 60.0, issues=issues)


def g3(snap: Snapshot, feat: Dict[str, Any]) -> CheckResult:
    tech = feat.get("technical", {})
    val = feat.get("valuation", {})
    issues = []
    if tech.get("last_date") and val.get("last_date") and str(tech["last_date"]) != str(val["last_date"]):
        issues.append("行情最后日期(%s) 与估值最后日期(%s) 不一致" % (tech["last_date"], val["last_date"]))
    if val.get("ps") is not None and val.get("ps") <= 0:
        issues.append("PS 非正数")
    if tech.get("last_close") in (None, 0):
        issues.append("最新收盘价缺失")
    ok = not issues
    return CheckResult("G3", passed=ok, score=100.0 if ok else 50.0, issues=issues)


def g4(rendered_markdown: str, result: Dict[str, Any]) -> CheckResult:
    required = ["结论摘要", "财务", "估值", "风险", "不构成投资建议"]
    issues = [s for s in required if s not in rendered_markdown]
    v = result.get("verdict") or {}
    for k in ("rating", "score", "triggers", "invalidations"):
        if k not in v:
            issues.append("verdict 缺少 %s" % k)
    return CheckResult("G4", passed=not issues, score=100.0 if not issues else 50.0, issues=issues)


def run(snap: Snapshot, feat: Dict[str, Any], cfg: GateConfig | None = None) -> List[CheckResult]:
    cfg = cfg or GateConfig()
    return [g1(snap, feat, cfg), g2(snap), g3(snap, feat)]
