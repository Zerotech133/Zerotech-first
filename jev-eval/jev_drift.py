#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JEV 判定漂移监控（M7，K-ORG-160）。

数据：news.db news 表 jev 列（近 14 天）→ 周窗口分布对比（当前 7 天 vs 前 7 天）。
维度：bias(利多/利空/中性) / scope(六市场) / event_type(15 类，仅统计携带该键的新判定)
      / conf 分桶(低<0.7 / 中0.7-0.9 / 高≥0.9，阈值对齐 K-ORG-157 实证)。
度量：Jensen-Shannon 散度 JS(p||q)=0.5·KL(p||m)+0.5·KL(q||m)，m=(p+q)/2。
告警：JS 超阈值（默认 0.05）且两窗口样本均 ≥ 最小样本（默认 100）
      → v2_alert INSERT（status=OPEN，level=WARN，alert_id 按 维度+ISO周 确定性生成，
      幂等防重复推送）→ jev_gate 轮询窗口内拾取，经 Jev 紧急度降噪后决定是否推飞书
      （K-ORG-160：告警进 jev_gate，沿用其 2h 窗口 + Jev 降噪 + 飞书卡链路）。

借鉴：开源概念漂移监控（Evidently AI 分布漂移检测 / 线上模型健康监控）——
      分类器输出分布漂移是「模型/prompt/数据源」三因退化的哨兵信号。

用法: python jev_drift.py [--dry] [--js-threshold 0.05] [--min-n 100]
      --dry 只打印不落库（演练/阈值校准）；计划任务常驻：--once 单轮默认。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jev_common as jc  # noqa: E402

NEWS_DB = os.path.join(HERE, "..", "news_collector", "news.db")
CST = timezone(timedelta(hours=8))

_BIAS_CN = {"bull": "利多", "bear": "利空", "neutral": "中性"}
_SCOPE_CN = {
    "cn": "A股",
    "hk": "港股",
    "us": "美股",
    "commodity": "商品",
    "crypto": "加密",
    "macro": "宏观",
}
# K-ORG-157 实证对齐：0.7 待观察线 / 0.9+ 右偏档
_EVENT_CN = {
    "earnings": "业绩",
    "policy": "政策",
    "rate": "利率",
    "trade": "贸易",
    "supply": "供给",
    "demand": "需求",
    "price": "价格",
    "ma": "并购",
    "equity": "股权",
    "credit": "债务",
    "risk": "风险",
    "tech": "技术",
    "macro": "宏观",
    "geo": "地缘",
    "other": "其他",
}
_CONF_BUCKETS = [("low", 0.0, 0.7), ("mid", 0.7, 0.9), ("high", 0.9, 1.0)]
_CONF_CN = {"low": "低置信<70%", "mid": "中置信70-90%", "high": "高置信≥90%"}
# 维度→类别中文名（含未知桶标签）
_CN_MAPS = {
    "bias": dict(_BIAS_CN, **{"?": "未知"}),
    "scope": dict(_SCOPE_CN, **{"?": "未知"}),
    "event_type": _EVENT_CN,
    "conf": _CONF_CN,
}
# 维度→告警核查提示（按根因画像差异化）
_HINTS = {
    "scope": "优先核查云/本地判定通道占比变化（typesafe 451/额度/断供→本地回落无scope），其次市场结构变化",
    "bias": "核查方向：prompt/模型切换、数据源变化、市场结构性事件、词表/口径调整",
    "event_type": "核查方向：prompt/模型切换、数据源事件结构变化、新词表/口径生效",
    "conf": "核查方向：模型切换/温度参数变化、判定任务复杂度变化、prompt 置信口径调整",
}

NS_DRIFT = uuid.UUID("6f3c1b0e-9d6a-4f5e-b7d2-3a8c0e1f2b4d")


def _now():
    return datetime.now(CST)


def _parse_ts(v):
    """created_at 解析（%Y-%m-%d %H:%M，CST 本地时间），坏值返回 None（豁免 C2批2）。"""
    try:
        t = datetime.strptime(str(v)[:16], "%Y-%m-%d %H:%M")
        return t.replace(tzinfo=CST)
    except Exception:  # noqa: BLE001
        return None


def _load_rows(hours=24 * 14):
    """news.db jev 行 → [(dt, dict)]，坏行跳过。失败返回 []（链路不断只退化）。"""
    if not os.path.exists(NEWS_DB):
        jc.log(f"news.db 不存在（{NEWS_DB}），漂移监控跳过", level="warn")
        return []
    since = (_now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        try:
            rows = db.execute(
                "SELECT jev, created_at FROM news WHERE jev IS NOT NULL "
                "AND jev!='' AND created_at>=?",
                (since,),
            ).fetchall()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        jc.log("news.db 查询失败，漂移监控跳过: %s" % str(e)[:100], level="warn")
        return []
    out = []
    for j, created_at in rows:
        try:
            d = json.loads(j)
            t = _parse_ts(created_at)
        except Exception:  # noqa: BLE001
            continue  # 豁免（C2批2）：单条坏行跳过
        if t is None or not isinstance(d, dict):
            continue
        out.append((t, d))
    return out


def _bucket(row, dim):
    """行 → 维度类别键；不支持返回 None（不计入该维度分布）。"""
    d = row[1]
    if dim == "bias":
        b = d.get("bias")
        return b if b in _BIAS_CN else "?"
    if dim == "scope":
        s = d.get("scope")
        return s if s in _SCOPE_CN else "?"
    if dim == "event_type":
        et = d.get("event_type")
        return et if et in _EVENT_CN else None  # 无 event_type 的存量行不计
    if dim == "conf":
        c = d.get("conf")
        if not isinstance(c, (int, float)):
            return None
        for name, lo, hi in _CONF_BUCKETS:
            if lo <= c < hi:
                return name
        return "high" if c >= 0.9 else None
    return None


def _distrib(rows, dim):
    """窗口内维度分布（Counter），总计数 ≥1 才有意义。"""
    c = Counter()
    for r in rows:
        k = _bucket(r, dim)
        if k is not None:
            c[k] += 1
    return c


def _js_divergence(p: Counter, q: Counter):
    """Jensen-Shannon 散度（平滑防 log0）。p/q 键空间取并集。"""
    keys = set(p) | set(q)
    eps = 1e-9
    np_, nq = float(sum(p.values())), float(sum(q.values()))
    if np_ <= 0 or nq <= 0:
        return None, None
    pv = {k: p.get(k, 0) / np_ for k in keys}
    qv = {k: q.get(k, 0) / nq for k in keys}
    mv = {k: (pv[k] + qv[k]) / 2 for k in keys}
    kl1 = sum(pv[k] * math.log((pv[k] + eps) / (mv[k] + eps)) for k in keys)
    kl2 = sum(qv[k] * math.log((qv[k] + eps) / (mv[k] + eps)) for k in keys)
    return 0.5 * (kl1 + kl2), int(np_)


def _fmt_dist(c: Counter, cn_map=None):
    """分布文本：类别名(占比%)。"""
    n = float(sum(c.values()))
    if n <= 0:
        return "（无数据）"
    parts = []
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
        name = (cn_map or {}).get(k, k)
        parts.append("%s %.0f%%" % (name, v / n * 100))
    return " ".join(parts)


def _alert_id(dim, now):
    key = "jev-drift-%s-%04dW%02d" % (dim, now.isocalendar()[0], now.isocalendar()[1])
    return "alert-" + uuid.uuid5(NS_DRIFT, key).hex[:12]


def _insert_alert(dim, js, p, q, n_cur, n_base, now, dry):
    title = "JEV判定漂移·%s(JS=%.3f)" % (dim, js)
    cn_map = _CN_MAPS[dim]
    message = (
        "JEV 判定分布周窗口对比出现显著漂移（JS=%.3f，阈值 0.05）\n"
        "当前7天(n=%d): %s\n基线前7天(n=%d): %s\n%s"
        % (js, n_cur, _fmt_dist(p, cn_map), n_base, _fmt_dist(q, cn_map), _HINTS[dim])
    )
    if dry:
        print(
            "[DRY] %s | %s\n      %s"
            % (
                title,
                message.replace("\n", "\n      "),
                "alert_id=%s" % _alert_id(dim, now),
            )
        )
        return
    try:
        jc.db_run(
            "INSERT INTO v2_alert(alert_id,level,title,message,status,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (_alert_id(dim, now), "WARN", title, message, "OPEN", now.isoformat()),
        )
        jc.log("漂移告警已入 v2_alert", level="info", dim=dim, js=round(js, 3))
    except Exception as e:  # noqa: BLE001
        jc.log("漂移告警入库失败: %s" % str(e)[:100], level="warn", dim=dim)


def main(argv=None):
    ap = argparse.ArgumentParser(description="JEV 判定漂移监控（M7）")
    ap.add_argument("--dry", action="store_true", help="只打印不落库")
    ap.add_argument(
        "--js-threshold", type=float, default=0.05, help="JS 散度告警阈值（默认 0.05）"
    )
    ap.add_argument(
        "--min-n", type=int, default=100, help="两窗口最小样本数（默认 100）"
    )
    ap.add_argument("--window-days", type=int, default=7, help="对比窗口天数（默认 7）")
    args = ap.parse_args(argv)

    jc.set_module("jev_drift")
    now = _now()
    rows = _load_rows(hours=24 * args.window_days * 2)
    if not rows:
        jc.log("漂移监控：无可用数据（跳过）", level="warn")
        print("NO_DATA")
        return 0
    cut = now - timedelta(days=args.window_days)
    cur = [r for r in rows if r[0] >= cut]
    base = [r for r in rows if r[0] < cut]
    if not base:
        jc.log("漂移监控：基线窗口无数据（跳过，待数据积累）", level="warn")
        print("NO_BASELINE")
        return 0

    dims = ["bias", "scope", "conf"]
    if any("event_type" in r[1] for r in rows):
        dims.append("event_type")  # 存量无 event_type，待新判定积累后自动纳入

    alerted = 0
    for dim in dims:
        p, q = _distrib(cur, dim), _distrib(base, dim)
        js, n_cur = _js_divergence(p, q)
        n_base = int(sum(q.values()))
        if js is None:
            jc.log("漂移 %s：窗口空分布，跳过" % dim, level="info")
            continue
        if n_cur < args.min_n or n_base < args.min_n:
            jc.log(
                "漂移 %s：样本不足(%d/%d)，跳过" % (dim, n_cur, n_base), level="info"
            )
            continue
        flag = "DRIFT" if js >= args.js_threshold else "ok"
        cn_map = _CN_MAPS[dim]
        print(
            "%s %s JS=%.4f (n=%d/%d) 当前[%s] vs 基线[%s]"
            % (flag, dim, js, n_cur, n_base, _fmt_dist(p, cn_map), _fmt_dist(q, cn_map))
        )
        if js >= args.js_threshold:
            _insert_alert(dim, js, p, q, n_cur, n_base, now, args.dry)
            alerted += 1
    print("DRIFT_ALERTS=%d" % alerted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
