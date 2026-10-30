# -*- coding: utf-8 -*-
"""G1-G4 质量门。"""
import os

from . import config
import logging


def _g(issues, passed, score):
    return {"passed": bool(passed), "score": round(score, 1), "issues": issues[:12]}


def g1_data(snap):
    issues = []
    if snap.get("required_missing"):
        issues.append("必需域缺失：" + "、".join(snap["required_missing"]))
    cov = snap.get("coverage", 0)
    cov_ok = cov >= config.COVERAGE_MIN
    if not cov_ok:
        issues.append("覆盖率 %.1f%% < 硬门槛 %d%%" % (cov, config.COVERAGE_MIN))
    elif cov < config.COVERAGE_TARGET:
        # 诸葛亮裁示（2026-10-26）：目标 90%，80-90 区间标黄告警、不直接否决
        issues.append("覆盖率 %.1f%% < 目标 %d%%（告警，不否决）" % (cov, config.COVERAGE_TARGET))
    # P0M：数据时效告警（不否决，但评级置信度会下调）
    lag = snap.get("data_lag") or {}
    if lag.get("trade_days") is not None and lag["trade_days"] >= 1:
        issues.append("行情数据落后 %d 个交易日（as_of %s，今日 %s）"
                      % (lag["trade_days"], lag.get("last_trade_date"), lag.get("today")))
    # 时效：仅对状态 ok 的域检查（missing 已显式标 N/A，不误报滞后）
    for dom in ("chips", "margin"):
        d = snap["domains"].get(dom) or {}
        if d.get("rows") and d.get("status") == "ok" and d.get("as_of") and snap.get("last_trade_date"):
            try:
                from datetime import date
                a = date.fromisoformat(d["as_of"].replace("/", "-"))
                b = date.fromisoformat(snap["last_trade_date"].replace("/", "-"))
                if (b - a).days > 7:
                    issues.append("%s 数据滞后 %d 天" % (dom, (b - a).days))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/gates.py", exc)
    passed = bool(cov_ok and not snap.get("required_missing"))
    return _g(issues, passed, cov)


def g2_facts(snap, events, verdict):
    issues = []
    if not events:
        issues.append("事件库为空（公告未抓取/未命中）")
    no_url = [e for e in events if not e.get("url")]
    if no_url:
        issues.append("%d 条事件缺原文 URL" % len(no_url))
    if not verdict.get("thesis"):
        issues.append("结论缺少论点")
    # 关键数字存在性
    if (verdict.get("confidence") or 0) <= 0:
        issues.append("置信度缺失")
    score = 100 - len(issues) * 15
    return _g(issues, score >= 70, score)


def g3_consistency(snap, verdict, views, events):
    issues = []
    if verdict.get("rating") not in config.RATINGS:
        issues.append("评级不在 taxonomy 内")
    if not (2 <= len(verdict.get("scenarios") or []) <= 5):
        issues.append("情景数量异常")
    if verdict.get("rating") == "买入" and (verdict.get("score") or 0) < 1.5:
        issues.append("买入评级与规则分不一致")
    if views:
        issues += ["AI：" + i for i in (views.get("issues") or [])]
    if "退市风险" in (verdict.get("risks") and " ".join(verdict["risks"]) or ""):
        pass
    score = 100 - len(issues) * 12
    return _g(issues, score >= 70, score)


def g4_delivery(artifacts, outdir):
    issues = []
    for k, p in (artifacts or {}).items():
        if k == "charts":
            continue
        if not p or not os.path.exists(p) or os.path.getsize(p) < 500:
            issues.append("交付物缺失/过小：%s" % k)
    report = artifacts.get("report")
    if report and os.path.exists(report):
        txt = open(report, encoding="utf-8").read()
        if "不构成投资建议" not in txt:
            issues.append("缺少免责声明")
        if "数据口径" not in txt:
            issues.append("缺少数据口径")
    score = 100 - len(issues) * 20
    return _g(issues, score >= 80, score)
