# -*- coding: utf-8 -*-
"""对倒检测·时序回放（P3 第二块，教材"查回路"的席位时序版）。

教材出处：《公安大数据挖掘与分析教程》可视化七法之"查找回路：
分析资金流、物流、信息流等，是否存在回路"+长歌案"人物层级刻画"。
量化映射（ts_top_inst 只有大义向，无逐笔方向，全部是【代理口径】）：

  回放对象：seat_syndicate 输出的团伙（密度>=0.6 的紧团伙 + 大连通块
  内的券商系簇），取其共现票在窗口内的逐日席位行为。

  三个代理信号（每票聚合）：
  A. 同日对向：同一天 团伙内 X 大买 + Y 大卖（净额同日对开）
  B. 隔日接力：D 日 X 大买 → D+1 Y 大卖（换手接力）
  C. 团伙净锁：窗口内团伙在该票的合计净买 占该票总成交额比例高
     且价格未涨（吸而不拉）

  输出对倒嫌疑分（0-100，三信号加权）——举证型线索，非认定。
  认定权在监管与人（audit 三字段）。

审计：
  assumptions: ①龙虎榜前5席位=该票当日主力行为（可见部分）；
               ②同日买卖对开/隔日接力≈左手倒右手的代理；
               ③换手类上榜票天然高频出现 A/B 信号。
  falsifier:  对照组——随机等规模席位组在同一批票上的 A/B 信号率
              与团伙无差异 → 信号是票的属性非团伙协同。
  cost_if_wrong: 高换手票被误标"对倒嫌疑"——输出仅供线索排序，
              不得作为操纵认定或交易依据。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
SYN = os.path.join(HERE, "reports", "seat_syndicate.json")
OUT = os.path.join(HERE, "reports", "seat_replay.json")

W_A, W_B, W_C = 40, 35, 25          # 三信号权重（满分 100）


def replay(days: int = 30):
    import tdx_read as tdxr

    syn = json.load(open(SYN, encoding="utf-8"))
    # 回放团伙：紧小团伙 + 大块里按券商前缀聚簇（东亚前海/甬兴/国联民生系）
    gangs = []
    for g in syn.get("gangs") or []:
        if g.get("n_seats", 0) >= 3 and g.get("density", 0) >= 0.5:
            gangs.append(g["seats"])
    big = next((g["seats"] for g in syn.get("gangs") or []
                if g.get("n_seats", 0) > 50), [])
    for prefix in ("东亚前海", "甬兴", "国联民生", "东方财富拉萨"):
        clan = [s for s in big if prefix in s]
        if len(clan) >= 3:
            gangs.append(clan)

    start = (dt.date.today() - dt.timedelta(days=days * 3)).strftime("%Y%m%d")
    ti = tdxr.read_range(
        "ts_top_inst", start=start,
        columns=["ts_code", "trade_date", "exalter", "buy", "sell", "net_buy"])
    ti = ti.dropna(subset=["exalter"]).copy()
    ti["exalter"] = ti["exalter"].astype(str).str.strip()
    ti["d"] = ti["trade_date"].astype(str).str[:8]
    # 上游隔日重复披露防护（2026-11-13 判例：600261 国联民生宁波
    # buy=73810572.78 同数值挂在 11-05 与 11-06 两天，45/9498=0.47%）——
    # (code,exalter,buy,sell,net_buy) 完全相同的行只保留首次出现，
    # 否则 B 接力信号被虚增（同笔钱既算 D 日买又算 D+1 日买）。
    ti = ti.drop_duplicates(
        subset=["ts_code", "exalter", "buy", "sell", "net_buy"], keep="first")

    # 成交额（判吸而不拉）
    tl = tdxr.read_range("ts_top_list", start=start,
                         columns=["ts_code", "trade_date", "amount"])
    amt = {}
    if tl is not None and not tl.empty:
        tl["d"] = tl["trade_date"].astype(str).str[:8]
        for r in tl.itertuples(index=False):
            amt[(r.ts_code, r.d)] = float(r.amount or 0)

    out_gangs = []
    for seats in gangs:
        sset = set(seats)
        g = ti[ti["exalter"].isin(sset)]
        if g.empty:
            continue
        # 团伙涉票
        stocks = defaultdict(lambda: {"rows": [], "days": set()})
        for r in g.itertuples(index=False):
            stocks[r.ts_code]["rows"].append(r)
            stocks[r.ts_code]["days"].add(r.d)
        scored = []
        for code, s in stocks.items():
            if len(s["days"]) < 2:
                continue
            rows = sorted(s["rows"], key=lambda r: (r.d, r.exalter))
            by_day = defaultdict(list)
            for r in rows:
                by_day[r.d].append(r)
            days_sorted = sorted(by_day)
            # A 同日对向
            sig_a = 0
            for d, rs in by_day.items():
                buys = [r for r in rs if (r.net_buy or 0) > 20e4]
                sells = [r for r in rs if (r.net_buy or 0) < -20e4]
                if buys and sells:
                    mb = max(float(r.net_buy) for r in buys)
                    ms = min(float(r.net_buy) for r in sells)
                    sig_a += min(mb, -ms)
            # B 隔日接力
            sig_b = 0
            for i in range(len(days_sorted) - 1):
                d0, d1 = days_sorted[i], days_sorted[i + 1]
                b0 = [r for r in by_day[d0] if (r.net_buy or 0) > 20e4]
                s1 = [r for r in by_day[d1] if (r.net_buy or 0) < -20e4]
                if b0 and s1:
                    sig_b += min(max(float(r.net_buy) for r in b0),
                                 -min(float(r.net_buy) for r in s1))
            # C 团伙净锁 / 榜成交额
            net = sum(float(r.net_buy or 0) for r in rows)
            amt_sum = sum(amt.get((code, d), 0) for d in s["days"])
            sig_c = net / amt_sum if amt_sum > 0 else 0
            total_amt = max(sig_a + sig_b, 1)
            score = min(100, round(
                W_A * min(sig_a / 5e5, 1) + W_B * min(sig_b / 5e5, 1)
                + W_C * min(max(sig_c, 0) / 0.3, 1)))
            scored.append({
                "code": code, "n_days": len(s["days"]),
                "sig_a_same_day_wan": round(sig_a / 1e4, 0),
                "sig_b_relay_wan": round(sig_b / 1e4, 0),
                "sig_c_net_ratio": round(sig_c, 3),
                "gang_net_wan": round(net / 1e4, 0),
                "suspicion": score,
            })
        scored.sort(key=lambda x: -x["suspicion"])
        out_gangs.append({
            "n_seats": len(seats), "seats": seats,
            "n_stocks": len(scored),
            "top_suspect": scored[:10],
        })
    out_gangs.sort(key=lambda g: -(g["top_suspect"][0]["suspicion"]
                                   if g["top_suspect"] else 0))
    doc = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basis": syn.get("basis"), "window_days": days,
        "signals": {
            "A_same_day_opposite": "同日团伙内大买+大卖（万，净额口径）",
            "B_next_day_relay": "隔日买→卖接力（万）",
            "C_net_lock_ratio": "团伙净买/榜成交额（吸而不拉）",
        },
        "weights": {"A": W_A, "B": W_B, "C": W_C},
        "gangs": out_gangs,
        "audit": {
            "assumptions": "榜前5席位=主力可见行为；同日对开/隔日接力为"
                           "左手倒右手代理；阈值 20万净额",
            "falsifier": "随机等规模席位组对照：A/B 信号率与团伙无差"
                         "→ 信号是高换手票的属性",
            "cost_if_wrong": "高换手票误标嫌疑——仅线索排序，非操纵认定",
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("seat_replay: %d 团伙 -> %s" % (len(out_gangs), OUT))
    for g in out_gangs[:3]:
        t = g["top_suspect"][0] if g["top_suspect"] else None
        if t:
            print("  %d席位团 最高嫌疑 %s（%d分，A=%s万 B=%s万 C=%.2f）"
                  % (g["n_seats"], t["code"], t["suspicion"],
                     t["sig_a_same_day_wan"], t["sig_b_relay_wan"],
                     t["sig_c_net_ratio"]))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="对倒检测·时序回放（P3-2）")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    # rc 语义（KG 链软步骤约定）：0=正常；3=依赖缺失（seat_syndicate
    # 无档/无团伙），wrapper 只记日志不阻断链；2=数据/运行错误。
    if not os.path.exists(SYN):
        print("seat_syndicate.json 缺档（先跑 seat_syndicate.py）")
        sys.exit(3)
    syn = json.load(open(SYN, encoding="utf-8"))
    if not syn.get("gangs"):
        print("seat_syndicate 无团伙可回放")
        sys.exit(3)
    sys.exit(replay(a.days))
