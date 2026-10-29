# -*- coding: utf-8 -*-
"""组合计划器（2026-10-30 WO-INV-POOL-COMBO）。

输入：每日股票池（3 只）+ 每只的多角色分析/ D1 决策；
输出：哪些票可组合买入、哪些观察/回避、建议权重、买点区间、止损/目标、卖点。

规则（paper MVP）：
- 仅 BUY/STRONG_BUY 且 parse_ok/status 合格、非 fallback/degraded 可入选；
- 单票 ≤10%、行业 ≤30%、总仓位 ≤ min(60%, 10%×入选数)；
- 未入选/无决策：观察（给触发价）或待分析；SELL/STRONG_SELL：回避/卖出区；
- 买点=现价±2%/1%；止损=现价-8%；目标=现价+8%/+15%；
- 不接实盘，仅生成计划。
"""
from __future__ import annotations

from datetime import datetime

REC_WEIGHT = {"STRONG_BUY": 1.0, "BUY": 0.7, "HOLD": 0.2, "WATCH": 0.0,
              "SELL": -0.5, "STRONG_SELL": -0.8}
DEFAULT_LIMITS = {"single_name_max": 0.10, "industry_max": 0.30,
                  "max_total": 0.60, "stop_pct": 0.08,
                  "target1_pct": 0.08, "target2_pct": 0.15}
MAX_POSITIONS = 3


def _price(dec):
    v = (dec or {}).get("price_at_decision")
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def _eligible(dec):
    if not dec or dec.get("status") != "done" or not dec.get("parse_ok"):
        return False
    if dec.get("fallback") or dec.get("degraded"):
        return False
    return str(dec.get("recommendation") or "").upper() in ("BUY", "STRONG_BUY")


def _industry(pool_item):
    return (pool_item or {}).get("industry") or "未分类"


def _buy_plan(price, rec, conf, limits):
    return {
        "action": "buy",
        "buy_zone": [round(price * 0.98, 2), round(price * 1.01, 2)],
        "stop_loss": round(price * (1 - limits["stop_pct"]), 2),
        "target1": round(price * (1 + limits["target1_pct"]), 2),
        "target2": round(price * (1 + limits["target2_pct"]), 2),
        "confidence": conf, "recommendation": rec,
    }


def _watch_plan(price, rec, limits):
    return {
        "action": "watch",
        "watch_trigger": {"pullback": round(price * 0.97, 2),
                          "breakout": round(price * 1.02, 2)},
        "stop_loss": round(price * (1 - limits["stop_pct"]), 2),
        "recommendation": rec,
    }


def _sell_plan(price):
    return {"action": "avoid_or_sell",
            "sell_zone": [round(price * 0.99, 2), round(price * 1.01, 2)]}


def compose(pool, decisions, limits=None, max_positions=MAX_POSITIONS, now=None,
            analyses=None):
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    now = now or datetime.now()
    analyses = analyses or {}
    decs = {}
    for k, v in (decisions or {}).items():
        decs[str(k).split(".")[0]] = v
    rows = []
    for item in pool or []:
        code = str(item.get("code") or "")
        d = decs.get(code) or {}
        rec = str(d.get("recommendation") or "").upper()
        conf = float(d.get("confidence") or 0.0)
        price = _price(d)
        row = {"code": code, "name": item.get("name") or d.get("name") or "",
               "industry": _industry(item), "tier": item.get("tier") or "",
               "recommendation": rec, "confidence": conf, "price": price,
               "score": round(REC_WEIGHT.get(rec, 0.0) * conf, 4),
               "parse_ok": bool(d.get("parse_ok")), "status": d.get("status"),
               "analyses": (analyses or {}).get(code) or {}}
        if not d:
            row.update({"action": "pending_analysis", "weight": 0.0,
                        "note": "无当日分析结果；先跑 run_shadow --codes"})
        elif not row["parse_ok"] or row["status"] != "done":
            row.update({"action": "pending_analysis", "weight": 0.0,
                        "note": "分析未完成/解析失败"})
        elif rec in ("SELL", "STRONG_SELL") and price:
            row.update(_sell_plan(price)); row.update({"weight": 0.0,
                                                       "note": "空头信号，不组合"})
        elif rec in ("WATCH", "HOLD") and price:
            row.update(_watch_plan(price, rec, limits))
            row.update({"weight": 0.0, "note": "暂不建仓，等触发价"})
        elif _eligible(d) and price:
            row.update({"eligible": True})
        elif price:
            row.update(_watch_plan(price, rec or "—", limits))
            row.update({"weight": 0.0, "note": "信号不足，观察"})
        else:
            row.update({"action": "pending_analysis", "weight": 0.0,
                        "note": "缺价格/信号"})
        rows.append(row)

    selected = sorted([r for r in rows if r.get("eligible")],
                      key=lambda r: -r["score"])[:max_positions]
    # 权重：每票 10%，总仓位 ≤ min(60%, 10%×入选数)；行业上限 30%
    ind_used = {}
    for r in selected:
        single = limits["single_name_max"]
        room_ind = limits["industry_max"] - ind_used.get(r["industry"], 0.0)
        w = max(0.0, min(single, room_ind))
        r.update(_buy_plan(r["price"], r["recommendation"], r["confidence"], limits))
        r["weight"] = round(w, 4)
        r["note"] = "组合买入候选"
        ind_used[r["industry"]] = ind_used.get(r["industry"], 0.0) + w
    total = sum(r["weight"] for r in selected)
    if total > limits["max_total"]:
        # 按比例压缩
        scale = limits["max_total"] / total
        for r in selected:
            r["weight"] = round(r["weight"] * scale, 4)
        total = sum(r["weight"] for r in selected)
    cash = round(max(0.0, 1.0 - total), 4)
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "paper_plan",
        "trading_permitted": False,
        "pool_size": len(rows),
        "selected": selected,
        "watch": [r for r in rows if r.get("action") == "watch"],
        "avoid": [r for r in rows if r.get("action") == "avoid_or_sell"],
        "pending": [r for r in rows if r.get("action") == "pending_analysis"],
        "total_weight": round(total, 4),
        "cash_weight": cash,
        "limits": {k: limits[k] for k in ("single_name_max", "industry_max",
                                          "max_total", "stop_pct",
                                          "target1_pct", "target2_pct")},
        "rows": rows,
    }


def render_markdown(plan):
    L = ["# 每日股票池组合计划（paper）", "",
         "> %s｜池 %d 只｜总仓位 %.1f%%｜现金 %.1f%%｜不接实盘" % (
             plan["generated_at"], plan["pool_size"],
             plan["total_weight"] * 100, plan["cash_weight"] * 100), ""]
    if plan["selected"]:
        L.append("## 可组合买入")
        L.append("")
        L.append("| 代码 | 名称 | 行业 | 建议 | 置信 | 权重 | 买点区间 | 止损 | 目标1 | 目标2 |")
        L.append("|---|---|---|---|---:|---:|---|---:|---:|---:|")
        for r in plan["selected"]:
            L.append("| %s | %s | %s | %s | %.2f | %.1f%% | %s~%s | %s | %s | %s |" % (
                r["code"], r["name"], r["industry"], r["recommendation"],
                r["confidence"], r["weight"] * 100,
                r["buy_zone"][0], r["buy_zone"][1], r["stop_loss"],
                r["target1"], r["target2"]))
    else:
        L.append("## 可组合买入")
        L.append("")
        L.append("今日无满足条件的 BUY/STRONG_BUY；不建仓，等下一次池内信号。")
    for title, key in (("观察（等触发价）", "watch"), ("回避 / 卖出区", "avoid"),
                       ("待分析", "pending")):
        rows = plan.get(key) or []
        if not rows:
            continue
        L.append("")
        L.append("## " + title)
        L.append("")
        for r in rows:
            extra = ""
            if r.get("watch_trigger"):
                extra = "；回踩 %s / 突破 %s" % (r["watch_trigger"]["pullback"],
                                                 r["watch_trigger"]["breakout"])
            if r.get("sell_zone"):
                extra = "；卖出区 %s~%s" % tuple(r["sell_zone"])
            L.append("- `%s` %s：%s（%s）%s" % (
                r["code"], r["name"], r.get("recommendation") or "—",
                r.get("note") or "", extra))
    L.append("")
    L.append("## 多角度明细")
    for r in plan.get("rows") or []:
        L.append("")
        L.append("### `%s` %s（%s）" % (r["code"], r["name"], r["industry"]))
        ana = r.get("analyses") or {}
        for seat, label in (("T1", "T1 多头技术"), ("T2", "T2 空头/风险"),
                            ("N1", "N1 情绪"), ("F1", "F1 基本面")):
            if ana.get(seat):
                L.append("- **%s**：%s" % (label, str(ana[seat]).replace("\n", " ")[:420]))
        if r.get("recommendation"):
            L.append("- **D1 裁决**：%s（confidence %.2f，价格 %s）" % (
                r["recommendation"], r["confidence"], r.get("price")))
    L.append("")
    L.append("> 规则：单票≤10% / 行业≤30% / 总仓≤60%；买点=现价附近，止损-8%，目标+8%/+15%；"
             "仅为 paper 计划，不构成实盘指令。")
    return "\n".join(L)
