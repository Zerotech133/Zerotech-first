# -*- coding: utf-8 -*-
"""1000 万真实资金口径模拟：把 v3 在最近滚动年（20251110→20261109）的
交易清单灌进一个带 A 股现实约束的账户，找「归一化回测看不见」的问题。

老田 2026-11-10：「基于 1 年的历史数据给你 1000 万跑，找问题」。

模拟与引擎的差异（每一条都是一个待检验的问题）：
  P1 手数取整   引擎按金额比例建仓（可买小数股），实盘 100 股/手
  P2 费率       引擎平摊 122bp/30bp，实盘=佣金2.5bp(min5元)+印花5bp(卖出)
                +过户费0.1bp 双边，真实显性成本 ≈10bp 往返（冲击另列）
  P3 流动性     单笔 10% 槽位 = 100 万级订单 vs 当日成交额，占比过高 = 冲击风险
  P4 涨跌停     v3 的 runup_cap=0.05 在买入路径先于 limit_check → 开盘>+5%
                的机会直接放弃，涨停顺延在买入侧是死代码；卖出侧跌停顺延
                活跃，阈值 0.905 对创业板/科创板（20%板）会把 −9.5%~-19.7%
                的可交易开盘也误顺延
  P5 资金轨迹   同日多信号现金约束、最高占用、现金碎片

记账口径（已与引擎对账验证）：
  价格收益一律用复权价（px），现金/手数用原始价（px_raw）——分红送转由
  复权比价自然吸收。买入 fill=买入日开盘，卖出 fill=卖出日开盘
  （trail/time/3b_fail/exit_open 全走 T+1_open 块），take_profit
  fill=entry×(1+tp_pct)×(1−tp_slip)（与跳空无关）。引擎 rec 的 pnl 是
  毛收益（fill/entry−1），成本只在现金流两端各乘 (1−c)。

用法：python bt10m_1y_10m.py
产出：reports/bt10m_10m_sim.json + 终端问题清单
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
OUT = os.path.join(BASE, "reports", "bt10m_10m_sim.json")

CAPITAL = 10_000_000          # 1000 万
D0, D1 = "20251110", "20261109"
COST_FLAT = 0.0061            # 引擎固化 122bp 往返（单边 61bp）
TP_PCT, TP_SLIP = 0.20, 0.003 # v3 止盈口径
LOT = 100                     # A 股一手


def fee_buy(amt):
    """买入显性费用：佣金 max(5, 2.5bp) + 过户费 0.1bp。"""
    return max(5.0, amt * 0.00025) + amt * 0.00001


def fee_sell(amt):
    """卖出显性费用：佣金 max(5, 2.5bp) + 印花税 5bp + 过户费 0.1bp。"""
    return max(5.0, amt * 0.00025) + amt * 0.0005 + amt * 0.00001


def board_is_20pct(stock):
    return stock.split(".")[0].startswith(("300", "301", "688", "689"))


def main():
    t0 = time.time()
    # ---- 1. 取 v3 交易清单与逐日快照 ----
    p = {"preset": "v3", "d0": D0, "d1": D1, "cost": COST_FLAT}
    payload, err = eng.run_job(p, "sim10m_v3")
    assert not err, err
    trades = payload["trades"]
    days = [e["day"] for e in payload["equity_curve"]]
    eq_engine = payload["equity_curve"][-1]["equity"]
    hmap = {}
    for d, plist in (payload.get("holdings") or {}).items():
        for h in plist:
            hmap[(d, h["stock"])] = h.get("wgt_pct")

    # FIFO 配对
    q, pairs = {}, []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q.get(t["stock"])
            if lst:
                pairs.append({"stock": t["stock"], "bday": lst.pop(0),
                              "sday": t["day"], "how": t["how"],
                              "pnl_engine": float(t["pnl"])})

    # ---- 2. 复权价重建成交并与引擎逐笔对账 ----
    recon_dev = []
    for pr in pairs:
        s, bd, sd = pr["stock"], pr["bday"], pr["sday"]
        ea = eng.px(s, bd, "open")       # 复权开盘（收益记账）
        er = eng.px_raw(s, bd, "open")   # 原始开盘（现金/手数）
        if ea is None or er is None:
            pr["recon_ok"] = False
            continue
        if pr["how"] == "take_profit":
            xa = ea * (1 + TP_PCT) * (1 - TP_SLIP)
        else:
            xa = eng.px(s, sd, "open")
        if xa is None:
            pr["recon_ok"] = False
            continue
        pr.update(entry_adj=ea, entry_raw=er, exit_adj=xa, recon_ok=True)
        recon_dev.append(abs((xa / ea - 1) * 100 - pr["pnl_engine"]))
    ok = [x for x in pairs if x.get("recon_ok")]
    print("对账：%d/%d 笔重建成功，毛收益 vs 引擎 pnl 最大偏差 %.4fpp（均值 %.4fpp）"
          % (len(ok), len(pairs), max(recon_dev), statistics.mean(recon_dev)),
          flush=True)

    # ---- 3. P4a 买入侧：全部事件口径的 runup帽 放弃统计 + 20% 板暴露 ----
    ev_cache = os.path.join(BASE, "data", "sig_events",
                            "sig_events_market_v3_%s_%s.json" % (D0, D1))
    events = json.load(open(ev_cache, encoding="utf-8"))["events"]
    bds_by_stock = {}
    for pr in pairs:
        bds_by_stock.setdefault(pr["stock"], set()).add(pr["bday"])
    n_att = n_abandon5 = n_open_gt9 = n_20pct_gt9 = 0
    abandon_examples = []
    for e in events:
        if e.get("type") != "3买" or e.get("score") is None:
            continue
        s = e["stock"]
        b = eng._book(s)
        if b is None:
            continue
        td = list(b["trade_date"])
        if e["day"] not in td:
            continue
        i = td.index(e["day"])
        if i + 1 >= len(td):
            continue
        att, prev = td[i + 1], td[i]
        o = eng.px_raw(s, att, "open")
        pc = eng.px_raw(s, prev, "close")
        if not o or not pc:
            continue
        n_att += 1
        r = o / pc - 1
        if r > 0.05:
            n_abandon5 += 1
            if len(abandon_examples) < 5:
                abandon_examples.append("%s %s 开盘+%+.1f%%" % (s, att, r * 100))
        if r >= 0.095:
            n_open_gt9 += 1
            if board_is_20pct(s):
                n_20pct_gt9 += 1

    # ---- 4. P4b 卖出侧：20% 板持仓期间 开盘≤昨收−9.5% 的误顺延暴露 ----
    expose20 = []
    for pr in ok:
        s, bd, sd = pr["stock"], pr["bday"], pr["sday"]
        if not board_is_20pct(s):
            continue
        b = eng._book(s)
        td = list(b["trade_date"])
        for j in range(td.index(bd), min(td.index(sd), td.index(bd) + 60)):
            if j == 0:
                continue
            d, prev = td[j], td[j - 1]
            o, pc = eng.px_raw(s, d, "open"), eng.px_raw(s, prev, "close")
            if o and pc and o / pc - 1 <= -0.095:
                expose20.append({"stock": s, "day": d,
                                 "gap": round((o / pc - 1) * 100, 1)})

    # ---- 5. 1000 万账户模拟（手数取整 + 真实费率，复权价记账）----
    cash_f = cash_r = float(CAPITAL)
    pos_f, pos_r = {}, {}      # stock -> dict(sh, entry_raw, entry_adj)
    last_ratio = {}            # 停牌顺延：最近复权比价
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

    stat = {"lot_leftover": [], "buy_amt": [], "liq_buy": [], "liq_sell": [],
            "min_cash": CAPITAL, "max_deploy_pct": 0.0, "shortfalls": [],
            "eq_f": [], "eq_r": [], "fees_paid": 0.0, "turnover": 0.0}
    amt_by_day = {}   # 当日买入金额（real 版）
    for d in days:
        amt_by_day.clear()
        # 卖出（开盘）
        for (s, bd), (sd, how) in list(sell_map.items()):
            if sd != d:
                continue
            pr_key = (s, bd)
            # real 版
            po = pos_r.pop(s, None)
            if po:
                if how == "take_profit":
                    xr = po["entry_adj"] * (1 + TP_PCT) * (1 - TP_SLIP)
                else:
                    xr = eng.px(s, d, "open")
                ratio = xr / po["entry_adj"] if xr else last_ratio.get(s, 1.0)
                last_ratio[s] = ratio
                proceeds = po["sh"] * po["entry_raw"] * ratio
                f = fee_sell(proceeds)
                cash_r += proceeds - f
                stat["fees_paid"] += f
                stat["turnover"] += proceeds
                bb = eng._book(s)
                if bb is not None:
                    row = bb.loc[bb["trade_date"] == d]
                    if not row.empty and row["amount"].iloc[0]:
                        stat["liq_sell"].append(proceeds / (float(row["amount"].iloc[0]) * 1000))
            # flat 版（引擎口径：入场先吃 1−c，出场再吃 1−c）
            pf = pos_f.pop(s, None)
            if pf:
                if how == "take_profit":
                    xr = pf["entry_adj"] * (1 + TP_PCT) * (1 - TP_SLIP)
                else:
                    xr = eng.px(s, d, "open")
                ratio = xr / pf["entry_adj"] if xr else last_ratio.get(s, 1.0)
                last_ratio[s] = ratio
                cash_f += pf["sh"] * pf["entry_raw"] * (1 - COST_FLAT) * ratio * (1 - COST_FLAT)
            sell_map.pop(pr_key)
        # 买入（开盘）
        for t in [x for x in trades if x["side"] == "BUY" and x["day"] == d]:
            s = t["stock"]
            ea = eng.px(s, d, "open")
            er = eng.px_raw(s, d, "open")
            if not ea or not er:
                continue
            wgt = hmap.get((d, s))
            if not wgt:
                continue
            eq_prev_f = stat["eq_f"][-1] if stat["eq_f"] else CAPITAL
            eq_prev_r = stat["eq_r"][-1] if stat["eq_r"] else CAPITAL
            # flat 版
            amt_f = wgt / 100.0 * eq_prev_f
            sh_f = math.floor(amt_f / er / LOT) * LOT
            spend_f = sh_f * er
            if sh_f > 0 and cash_f >= spend_f * (1 + COST_FLAT):
                cash_f -= spend_f * (1 + COST_FLAT)
                pos_f[s] = {"sh": sh_f, "entry_raw": er, "entry_adj": ea}
            # real 版
            amt_r = wgt / 100.0 * eq_prev_r
            sh_r = math.floor(amt_r / er / LOT) * LOT
            spend_r = sh_r * er
            need = spend_r + fee_buy(spend_r)
            if sh_r > 0 and cash_r >= need:
                cash_r -= need
                pos_r[s] = {"sh": sh_r, "entry_raw": er, "entry_adj": ea}
                stat["fees_paid"] += fee_buy(spend_r)
                stat["turnover"] += spend_r
                amt_by_day[s] = spend_r
                stat["lot_leftover"].append(amt_r - spend_r)
                stat["buy_amt"].append(spend_r)
                bb = eng._book(s)
                if bb is not None:
                    row = bb.loc[bb["trade_date"] == d]
                    if not row.empty and row["amount"].iloc[0]:
                        ratio = spend_r / (float(row["amount"].iloc[0]) * 1000)
                        stat["liq_buy"].append(ratio)
                        if ratio > stat.get("liq_max", (0,))[0]:
                            stat["liq_max"] = (ratio, d, s)
            elif sh_r > 0:
                # 实盘做法：现金不够就缩量（引擎本来也 min(cash,…)），不放弃
                cash_eff = cash_r * 0.999  # 留出费用余量
                sh2 = math.floor(cash_eff / er / LOT) * LOT
                spend2 = sh2 * er
                need2 = spend2 + fee_buy(spend2)
                if sh2 > 0 and cash_r >= need2 and spend2 >= 0.3 * spend_r:
                    cash_r -= need2
                    pos_r[s] = {"sh": sh2, "entry_raw": er, "entry_adj": ea}
                    stat["fees_paid"] += fee_buy(spend2)
                    stat["turnover"] += spend2
                    amt_by_day[s] = spend2
                    stat["lot_leftover"].append(0.0)
                    stat["buy_amt"].append(spend2)
                    stat["downsized"] = stat.get("downsized", 0) + 1
                else:
                    stat["shortfalls"].append({"day": d, "stock": s,
                                               "need": round(need), "have": round(cash_r)})
        # 收盘盯市
        mv_f = 0.0
        for s, po in pos_f.items():
            c = eng.px(s, d, "close")
            if c:
                last_ratio[s] = c / po["entry_adj"]
            mv_f += po["sh"] * po["entry_raw"] * (1 - COST_FLAT) * last_ratio.get(s, 1.0)
        mv_r = 0.0
        for s, po in pos_r.items():
            c = eng.px(s, d, "close")
            if c:
                last_ratio[s] = c / po["entry_adj"]
            mv_r += po["sh"] * po["entry_raw"] * last_ratio.get(s, 1.0)
        eq_f_now = cash_f + mv_f
        eq_r_now = cash_r + mv_r
        stat["eq_f"].append(eq_f_now)
        stat["eq_r"].append(eq_r_now)
        stat["min_cash"] = min(stat["min_cash"], cash_r)
        if eq_r_now > 0:
            stat["max_deploy_pct"] = max(stat["max_deploy_pct"], mv_r / eq_r_now * 100)

    liq_b = [x for x in stat["liq_buy"] if x]
    liq_s = [x for x in stat["liq_sell"] if x]
    res = {
        "window": "%s-%s" % (D0, D1), "capital": CAPITAL,
        "nav": {
            "engine_flat_122bp": round(eq_engine * 100, 2),
            "sim_flat_122bp_lots": round(stat["eq_f"][-1] / CAPITAL * 100, 2),
            "sim_real_fees": round(stat["eq_r"][-1] / CAPITAL * 100, 2),
        },
        "recon_max_dev_pp": round(max(recon_dev), 4),
        "p1_lots": {
            "n_buys": len(stat["buy_amt"]),
            "avg_leftover_yuan": round(statistics.mean(stat["lot_leftover"]), 0) if stat["lot_leftover"] else None,
            "max_leftover_yuan": round(max(stat["lot_leftover"]), 0) if stat["lot_leftover"] else None,
        },
        "p2_fees": {
            "total_fees_yuan": round(stat["fees_paid"]),
            "fees_bp_of_turnover": round(stat["fees_paid"] / stat["turnover"] * 10000, 1) if stat["turnover"] else None,
            "turnover_yuan_wan": round(stat["turnover"] / 10000),
            "avg_buy_yuan_wan": round(statistics.mean(stat["buy_amt"]) / 10000, 1) if stat["buy_amt"] else None,
        },
        "p3_liquidity": {
            "buy_ratio_median_pct": round(statistics.median(liq_b) * 100, 3) if liq_b else None,
            "buy_ratio_p90_pct": round(sorted(liq_b)[int(len(liq_b) * 0.9)] * 100, 3) if liq_b else None,
            "buy_ratio_max_pct": round(max(liq_b) * 100, 2) if liq_b else None,
            "buy_ratio_max_where": ("%s %s" % (stat["liq_max"][1], stat["liq_max"][2])
                                    if stat.get("liq_max") else None),
            "buy_gt_1pct": sum(1 for x in liq_b if x > 0.01),
            "sell_gt_1pct": sum(1 for x in liq_s if x > 0.01),
        },
        "p4_limit": {
            "attempts_all": n_att,
            "abandoned_by_runup5": n_abandon5,
            "abandon_examples": abandon_examples,
            "opens_gt_9p5": n_open_gt9,
            "opens_gt9_on_20pct_board": n_20pct_gt9,
            "sell_false_defer_exposure_20pct": expose20,
        },
        "p5_cash": {
            "min_cash_yuan": round(stat["min_cash"]),
            "max_deployed_pct_of_equity": round(stat["max_deploy_pct"], 1),
            "downsized_buys": stat.get("downsized", 0),
            "cash_shortfalls": stat["shortfalls"],
        },
        "secs": round(time.time() - t0, 1),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("产物：%s" % OUT)
    return res


if __name__ == "__main__":
    main()
