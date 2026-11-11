# -*- coding: utf-8 -*-
"""1000 万账户执行手法对比：同一批 v3 信号，不同下单方式 × 冲击参数的净值对照。

老田 2026-11-10：「用其他手法测试，要有对比」。
上一轮（bt10m_1y_10m.py）发现 1000 万规模下唯一的真问题是流动性冲击
（最大单笔占当日成交额 4.74%）。本轮把「怎么下单」参数化，同批信号互相对比：

  M0  开盘全量市价（基线，= 引擎现行口径）
  M1  开盘 + 流动性帽 1%：单笔 > 当日成交额 1% 缩量到帽；卖出同理，
      超出部分次日继续卖（挂单工作流）
  M2  开盘/尾盘拆单 50/50：两腿各按一半参与率计冲击（近似 TWAP）
  M3  全部尾盘（收盘价）执行：改执行时点（同日更晚，不是隔日）

冲击模型（平方根定律）：impact_bp = k × sqrt(参与率)，参与率 = 订单金额/当日成交额。
k ∈ {50, 100, 200} bp 三档做敏感性。k=0 时 M0 即上一轮的「纯显性费率」口径。

共同口径（与 bt10m_1y_10m.py 一致）：
  复权价记账、原始价现金/手数、100 股取整、真实费率（佣金2.5bp+印花5bp卖+过户0.1bp）、
  买入 fill=T+1 日开盘（M3 为 T+1 收盘）、卖出 fill=触发次日开盘（M3 为当日收盘）、
  止盈 fill=entry×1.2×0.997（限价挂单，不另计冲击）。

用法：python bt10m_10m_exec.py
产出：reports/bt10m_10m_exec.json + 终端对照表
"""
import json
import math
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "bt10m_10m_exec.json")

CAPITAL = 10_000_000
D0, D1 = "20251110", "20261109"
TP_PCT, TP_SLIP = 0.20, 0.003
LOT = 100
LIQ_CAP = 0.01          # M1 流动性帽：单笔 ≤ 当日成交额 1%
KS = [50, 100, 200]     # 冲击系数 bp


def fee_buy(amt):
    return max(5.0, amt * 0.00025) + amt * 0.00001


def fee_sell(amt):
    return max(5.0, amt * 0.00025) + amt * 0.0005 + amt * 0.00001


def impact_bp(k, participation):
    """平方根冲击（bp），participation = 订单金额 / 当日成交额。"""
    return k * math.sqrt(max(participation, 0.0))


def load_pairs():
    """引擎交易清单 → 配对 + 价格字段（复权/原始各一套）。"""
    p = {"preset": "v3", "d0": D0, "d1": D1, "cost": 0.0061}
    payload, err = eng.run_job(p, "simexec_v3")
    assert not err, err
    trades = payload["trades"]
    days = [e["day"] for e in payload["equity_curve"]]
    eq_engine = payload["equity_curve"][-1]["equity"]
    hmap = {}
    for d, plist in (payload.get("holdings") or {}).items():
        for h in plist:
            hmap[(d, h["stock"])] = h.get("wgt_pct")
    q, buys, sells = {}, [], []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q.get(t["stock"])
            if lst:
                sells.append({"stock": t["stock"], "sday": t["day"],
                              "how": t["how"], "bday": lst.pop(0)})
    for s in trades:
        pass
    # 买入记录（按引擎顺序）
    q2 = {}
    for t in trades:
        if t["side"] == "BUY":
            q2.setdefault(t["stock"], []).append(t["day"])
    sell_map = {}
    for t in trades:
        if t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q2.get(t["stock"])
            if lst:
                sell_map[(t["stock"], lst.pop(0))] = (t["day"], t["how"])
    buy_recs = []
    for t in trades:
        if t["side"] == "BUY":
            d, s = t["day"], t["stock"]
            ea, er = eng.px(s, d, "open"), eng.px_raw(s, d, "open")
            ca = eng.px(s, d, "close")
            cr = eng.px_raw(s, d, "close")
            if not (ea and er and ca and cr):
                continue
            wgt = hmap.get((d, s))
            if not wgt:
                continue
            buy_recs.append({"day": d, "stock": s, "wgt": wgt,
                             "entry_adj": ea, "open_raw": er, "close_raw": cr})
    return days, eq_engine, hmap, sell_map, buy_recs


def day_amount(s, d):
    b = eng._book(s)
    if b is None:
        return None
    row = b.loc[b["trade_date"] == d]
    if row.empty or not row["amount"].iloc[0]:
        return None
    return float(row["amount"].iloc[0]) * 1000  # 千元→元


def sim(method, k, days, sell_map, buy_recs):
    """单方法 × 单冲击系数的 1000 万账户模拟。"""
    cash = float(CAPITAL)
    pos = {}                     # stock -> list of tranches {sh, entry_raw, entry_adj}
    last_ratio = {}
    stat = {"impact_yuan": 0.0, "fees": 0.0, "turnover": 0.0,
            "n_buy": 0, "n_downsize": 0, "n_sell_carry": 0,
            "deploy_sum": 0.0, "eq": [], "part_ratios": []}
    carry = []                   # M1 卖出剩余份额（次日继续卖）
    buys_by_day = {}
    for b in buy_recs:
        buys_by_day.setdefault(b["day"], []).append(b)
    for d in days:
        # ---- 卖出 ----
        still_carry = []
        for c in carry:  # 昨日没卖完的尾巴，今天继续（开盘或收盘按 method）
            s, sh_left, entry_raw, entry_adj = c
            x_adj = eng.px(s, d, "open")
            x_raw = eng.px_raw(s, d, "open")
            if method == "M3_close":
                x_adj = eng.px(s, d, "close")
                x_raw = eng.px_raw(s, d, "close")
            if not x_adj or not x_raw:
                still_carry.append(c)
                continue
            amt_full = sh_left * x_raw
            da = day_amount(s, d) or amt_full * 100
            sh_now = sh_left
            if method == "M1_liqcap1":
                cap_amt = LIQ_CAP * da
                sh_now = math.floor(min(amt_full, cap_amt) / x_raw / LOT) * LOT
                if sh_now <= 0:
                    still_carry.append(c)
                    continue
            part = (sh_now * x_raw) / da
            imp = impact_bp(k, part) / 10000.0
            ratio = x_adj / entry_adj
            gross = sh_now * entry_raw * ratio
            cash += gross * (1 - imp) - fee_sell(gross)
            stat["impact_yuan"] += gross * imp
            stat["fees"] += fee_sell(gross)
            stat["turnover"] += gross
            stat["part_ratios"].append(part)
            if method == "M1_liqcap1" and sh_now < sh_left:
                still_carry.append((s, sh_left - sh_now, entry_raw, entry_adj))
                stat["n_sell_carry"] += 1
        carry = still_carry
        for (s, bd), (sd, how) in list(sell_map.items()):
            if sd != d:
                continue
            po = pos.pop(s, None)
            if not po:
                sell_map.pop((s, bd))
                continue
            if how == "take_profit":
                # 限价挂单：按目标价成交，无市场冲击
                sh_left = sum(t["sh"] for t in po)
                entry_raw = po[0]["entry_raw"]
                entry_adj = po[0]["entry_adj"]
                ratio = (1 + TP_PCT) * (1 - TP_SLIP)
                gross = sh_left * entry_raw * ratio
                cash += gross - fee_sell(gross)
                stat["fees"] += fee_sell(gross)
                stat["turnover"] += gross
                sell_map.pop((s, bd))
                continue
            x_adj = eng.px(s, d, "open")
            x_raw = eng.px_raw(s, d, "open")
            if method == "M3_close":
                x_adj = eng.px(s, d, "close")
                x_raw = eng.px_raw(s, d, "close")
            if not x_adj or not x_raw:
                pos[s] = po  # 停牌顺延，明天再卖
                continue
            da = day_amount(s, d) or 1e12
            sh_left = sum(t["sh"] for t in po)
            sh_now = sh_left
            if method == "M1_liqcap1":
                cap_amt = LIQ_CAP * da
                sh_now = math.floor(min(sh_left * x_raw, cap_amt) / x_raw / LOT) * LOT
            if sh_now <= 0:
                # M1 全部顺延
                carry.append((s, sh_left, po[0]["entry_raw"], po[0]["entry_adj"]))
                stat["n_sell_carry"] += 1
                sell_map.pop((s, bd))
                continue
            part = (sh_now * x_raw) / da
            imp = impact_bp(k, part) / 10000.0
            ratio = x_adj / po[0]["entry_adj"]
            gross = sh_now * po[0]["entry_raw"] * ratio
            cash += gross * (1 - imp) - fee_sell(gross)
            stat["impact_yuan"] += gross * imp
            stat["fees"] += fee_sell(gross)
            stat["turnover"] += gross
            stat["part_ratios"].append(part)
            rest = sh_left - sh_now
            if rest > 0:
                # 剩余份额按 tranches 比例折算后留给明天
                frac = rest / sh_left
                pos2 = []
                for t in po:
                    sh2 = int(t["sh"] * frac)
                    if sh2 > 0:
                        pos2.append({"sh": sh2, "entry_raw": t["entry_raw"],
                                     "entry_adj": t["entry_adj"]})
                if pos2:
                    carry.append((s, rest, po[0]["entry_raw"], po[0]["entry_adj"]))
                    stat["n_sell_carry"] += 1
            sell_map.pop((s, bd))
        # ---- 买入 ----
        for b in buys_by_day.get(d, []):
            s = b["stock"]
            da = day_amount(s, d)
            if not da:
                continue
            eq_prev = stat["eq"][-1] if stat["eq"] else CAPITAL
            amt = b["wgt"] / 100.0 * eq_prev
            o_raw, c_raw = b["open_raw"], b["close_raw"]
            if method == "M1_liqcap1":
                amt = min(amt, LIQ_CAP * da)
            tranches = []
            if method == "M2_split50":
                legs = [(0.5, o_raw, b["entry_adj"]), (0.5, c_raw, b["entry_adj"])]
            elif method == "M3_close":
                legs = [(1.0, c_raw, b["entry_adj"])]
            else:
                legs = [(1.0, o_raw, b["entry_adj"])]
            ok = True
            for w, px_leg, ea in legs:
                amt_leg = amt * w
                sh = math.floor(amt_leg / px_leg / LOT) * LOT
                if sh <= 0:
                    continue
                spend = sh * px_leg
                part = spend / da
                imp = impact_bp(k, part) / 10000.0
                cost = spend * (1 + imp) + fee_buy(spend)
                if cash < cost:
                    sh = math.floor(cash * 0.999 / px_leg / LOT) * LOT
                    if sh <= 0:
                        ok = False
                        continue
                    spend = sh * px_leg
                    part = spend / da
                    imp = impact_bp(k, part) / 10000.0
                    cost = spend * (1 + imp) + fee_buy(spend)
                    stat["n_downsize"] += 1
                cash -= cost
                stat["impact_yuan"] += spend * imp
                stat["fees"] += fee_buy(spend)
                stat["turnover"] += spend
                stat["part_ratios"].append(part)
                tranches.append({"sh": sh, "entry_raw": px_leg, "entry_adj": ea})
            if tranches:
                pos.setdefault(s, []).extend(tranches)
                stat["n_buy"] += 1
        # ---- 盯市 ----
        mv = 0.0
        for s, ts in pos.items():
            ca = eng.px(s, d, "close")
            if ca:
                for t in ts:
                    last_ratio[s] = ca / t["entry_adj"]
            for t in ts:
                mv += t["sh"] * t["entry_raw"] * last_ratio.get(s, 1.0)
        eq_now = cash + mv
        stat["eq"].append(eq_now)
        stat["deploy_sum"] += mv / eq_now if eq_now else 0
    return stat


def main():
    t0 = time.time()
    days, eq_engine, hmap, sell_map, buy_recs = load_pairs()
    rows = []
    for method in ("M0_open_full", "M1_liqcap1", "M2_split50", "M3_close"):
        for k in KS:
            st = sim(method, k, days, dict(sell_map), buy_recs)
            nav = st["eq"][-1] / CAPITAL * 100
            peak, mdd = st["eq"][0], 0.0
            for v in st["eq"]:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            n_pr = len(st["part_ratios"])
            rows.append({
                "method": method, "k_bp": k,
                "nav": round(nav, 2),
                "mdd": round(mdd * 100, 2),
                "impact_yuan_wan": round(st["impact_yuan"] / 10000, 1),
                "impact_bp_of_capital": round(st["impact_yuan"] / CAPITAL * 10000, 1),
                "part_median_pct": round(statistics.median(st["part_ratios"]) * 100, 2) if n_pr else None,
                "part_p90_pct": round(sorted(st["part_ratios"])[int(n_pr * 0.9)] * 100, 2) if n_pr else None,
                "n_buy": st["n_buy"], "n_downsize": st["n_downsize"],
                "n_sell_carry": st["n_sell_carry"],
                "avg_deploy_pct": round(st["deploy_sum"] / len(days) * 100, 1),
                "fees_yuan_wan": round(st["fees"] / 10000, 1),
            })
            print("%-12s k=%3d  NAV %7.2f%%  回撤 %6.2f%%  冲击 %6.1f 万(%5.1fbp)  参与率中位 %5.2f%% p90 %5.2f%%  买 %d 缩量 %d 卖尾巴 %d  均仓 %.1f%%"
                  % (method, k, nav, mdd * 100, st["impact_yuan"] / 10000,
                     st["impact_yuan"] / CAPITAL * 10000,
                     rows[-1]["part_median_pct"] or 0, rows[-1]["part_p90_pct"] or 0,
                     st["n_buy"], st["n_downsize"], st["n_sell_carry"],
                     rows[-1]["avg_deploy_pct"]), flush=True)
    # M0 k=0 = 纯显性费率基线（对照上一轮）
    st0 = sim("M0_open_full", 0, days, dict(sell_map), buy_recs)
    ref = {"method": "M0_open_full", "k_bp": 0,
           "nav": round(st0["eq"][-1] / CAPITAL * 100, 2),
           "impact_yuan_wan": 0.0, "impact_bp_of_capital": 0.0,
           "fees_yuan_wan": round(st0["fees"] / 10000, 1)}
    rows.append(ref)
    print("%-12s k=  0  NAV %7.2f%%  冲击 0（纯显性费率基线）" % ("M0_open_full", ref["nav"]))
    res = {
        "window": "%s-%s" % (D0, D1), "capital": CAPITAL,
        "engine_flat_122bp_nav": round(eq_engine * 100, 2),
        "liq_cap": LIQ_CAP, "methods": rows,
        "secs": round(time.time() - t0, 1),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("产物：%s" % OUT)
    return res


if __name__ == "__main__":
    main()
