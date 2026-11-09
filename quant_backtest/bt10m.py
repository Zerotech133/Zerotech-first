"""1000 万 · 1 年回测的资金曲线与归因（读取 data/bt2 结果 + 行情算基准）。

用法：python bt10m.py [job_id]
产出：reports/bt10m_<job>.json（汇总）、reports/bt10m_<job>_daily.csv、_trades.csv
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

CAPITAL = 10_000_000.0
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data", "ashare")
OUT = os.path.join(BASE, "reports")


def load_market_bench(d0, d1):
    """等权全市场日收益 → 累计净值（复权口径与引擎一致：用 pct_chg）。

    结果按 d0_d1 缓存到 reports/bench_<d0>_<d1>.json（首算约 60s）。
    """
    import pyarrow.parquet as pq

    cache = os.path.join(OUT, "bench_%s_%s.json" % (d0, d1))
    if os.path.exists(cache):
        c = json.load(open(cache, encoding="utf-8"))
        return c["nav"], c["n_files"]

    per_day = defaultdict(list)
    files = glob.glob(os.path.join(DATA, "*.parquet"))
    for f in files:
        try:
            have = set(pq.read_schema(f).names)
            cols = [c for c in ("trade_date", "pct_chg", "close", "pre_close")
                    if c in have]
            b = pd.read_parquet(f, columns=cols)
        except Exception:
            continue
        if "pct_chg" in b.columns:
            pct = b["pct_chg"]
        elif "close" in b.columns and "pre_close" in b.columns:
            pct = (b["close"] / b["pre_close"] - 1.0) * 100.0
        else:
            continue
        for d, v in zip(b["trade_date"].astype(str), pct):
            if d0 <= d.replace("-", "") <= d1 and pd.notna(v):
                per_day[d.replace("-", "")].append(float(v) / 100.0)
    days = sorted(per_day)
    nav, cur = {}, 1.0
    for d in days:
        rs = per_day[d]
        cur *= 1.0 + (sum(rs) / len(rs) if rs else 0.0)
        nav[d] = cur
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"nav": nav, "n_files": len(files)}, f)
    return nav, len(files)


def main():
    job = sys.argv[1] if len(sys.argv) > 1 else "bt10m1y"
    src = os.path.join(BASE, "data", "bt2", "bt2_%s.json" % job)
    d = json.load(open(src, encoding="utf-8"))
    ec = d["equity_curve"]
    days_rec = d["days"]
    trades = d["trades"]
    summ = d["summary"]
    cfg = d["meta"]["config"]

    d0, d1 = cfg["d0"], cfg["d1"]
    eq = [(e["day"], e["equity"]) for e in ec]
    base_nav, n_files = load_market_bench(d0, d1)

    # ---------- 1000 万逐日资产 ----------
    daily = []
    peak = CAPITAL
    for day, e in eq:
        asset = CAPITAL * e
        peak = max(peak, asset)
        dd = asset / peak - 1.0
        base = base_nav.get(day)
        daily.append({
            "date": day,
            "asset": round(asset, 2),
            "equity": e,
            "drawdown": round(dd * 100, 3),
            "inv_pct": next(x["inv_pct"] for x in ec if x["day"] == day),
            "regime": days_rec.get(day, {}).get("regime"),
            "n_pos": days_rec.get(day, {}).get("n_pos"),
            "bench": round(base, 6) if base else None,
            "bench_asset": round(CAPITAL * base, 2) if base else None,
        })
    with open(os.path.join(OUT, "bt10m_%s_daily.csv" % job), "w",
              encoding="utf-8-sig", newline="") as f:
        w = pd.DataFrame(daily).to_csv(index=False)
        f.write(w)

    end_asset = daily[-1]["asset"]
    base_end = daily[-1]["bench_asset"] or CAPITAL

    # ---------- 逐月 ----------
    mon = defaultdict(lambda: {"start": None, "end": None, "bstart": None, "bend": None,
                              "n_days": 0, "inv": []})
    for r in daily:
        m = r["date"][:6]
        s = mon[m]
        if s["start"] is None:
            s["start"] = r["asset"]
            s["bstart"] = r["bench_asset"] or CAPITAL
        s["end"] = r["asset"]
        s["bend"] = r["bench_asset"] or CAPITAL
        s["n_days"] += 1
        s["inv"].append(r["inv_pct"])
    monthly = []
    for m in sorted(mon):
        s = mon[m]
        monthly.append({
            "month": m,
            "ret_pct": round((s["end"] / s["start"] - 1) * 100, 2),
            "bench_pct": round((s["bend"] / s["bstart"] - 1) * 100, 2),
            "excess_pct": round(((s["end"] / s["start"]) - (s["bend"] / s["bstart"])) * 100, 2),
            "n_days": s["n_days"],
            "avg_inv_pct": round(sum(s["inv"]) / len(s["inv"]), 1),
            "pnl_amount": round(s["end"] - s["start"], 2),
        })

    # ---------- 交易金额化 ----------
    eqmap = dict(eq)
    rows = []
    for t in trades:
        if t["side"] != "SELL" or t.get("pnl") is None:
            continue
        e = eqmap.get(t["day"], 1.0)
        notional = CAPITAL * e * cfg["slot_pct"]
        # 引擎写入时已 ×100：t["pnl"] 本身就是百分数（-25.5 = −25.5%）
        pnl_pct = t["pnl"]
        rows.append({
            "day": t["day"], "stock": t["stock"],
            "pnl_pct": round(pnl_pct, 3),
            "notional": round(notional, 2),
            "pnl_amount": round(notional * pnl_pct / 100.0, 2),
            "note": t.get("note", ""),
        })
    tr = pd.DataFrame(rows).sort_values("day")
    tr.to_csv(os.path.join(OUT, "bt10m_%s_trades.csv" % job),
              index=False, encoding="utf-8-sig")

    # ---------- 个股贡献 ----------
    by_stock = defaultdict(lambda: [0.0, 0])
    for r in rows:
        by_stock[r["stock"]][0] += r["pnl_amount"]
        by_stock[r["stock"]][1] += 1
    contrib = sorted(([k, round(v[0], 2), v[1]] for k, v in by_stock.items()),
                     key=lambda x: x[1])
    per_stock_cnt = [v[1] for v in by_stock.values()]

    # ---------- 按 regime ----------
    reg = defaultdict(lambda: {"pnl": 0.0, "n": 0, "inv": []})
    for r in rows:
        rg = days_rec.get(r["day"], {}).get("regime") or "?"
        reg[rg]["pnl"] += r["pnl_amount"]
        reg[rg]["n"] += 1
    for day, rec in days_rec.items():
        rg = rec.get("regime") or "?"
        if rg in reg:
            reg[rg]["inv"].append(rec.get("cash_pct", 0))

    # ---------- 成本估算 ----------
    total_notional = sum(r["notional"] for r in rows)
    cost_est = total_notional * cfg["cost"]

    # ---------- 成本与换手分解 ----------
    import re as _re

    turnover = 0.0
    for day, rec in days_rec.items():
        for a in (rec.get("actions") or []):
            if a.get("type") == "buy":
                mm = _re.search(r"买入 ([\d.]+)%", a.get("note") or "")
                if mm:
                    turnover += float(mm.group(1)) / 100.0
    one_way = cfg["cost"]
    cost_buy = turnover * one_way          # 买入费占本金
    cost_sell = turnover * one_way         # 卖出费（金额与买入近似）
    # pnl = val_exit/cost - 1，分子分母同为「扣过买入费的成本」，
    # 故 Σ(pnl×slot) 就是零成本下的组合收益（已用 cost0 回测实测验证：-6.28% vs -6.35%）
    pnl_slot = sum(r["pnl_pct"] / 100.0 for r in rows) * cfg["slot_pct"]
    gross_zero = None
    _z = os.path.join(BASE, "data", "bt2", "bt2_cost0.json")
    if os.path.exists(_z):
        gross_zero = json.load(open(_z, encoding="utf-8"))["summary"]["total_return_pct"]
    gross = gross_zero if gross_zero is not None else pnl_slot * 100
    cost_impact = (end_asset / CAPITAL - 1) * 100 - gross
    cost_break = {
        "turnover_x": round(turnover, 2),
        "one_way_pct": round(one_way * 100, 3),
        "roundtrip_pct": round(one_way * 2 * 100, 3),
        "cost_buy_pct": round(cost_buy * 100, 2),
        "cost_sell_pct": round(cost_sell * 100, 2),
        "cost_total_pct": round((cost_buy + cost_sell) * 100, 2),
        "cost_total_amount": round((cost_buy + cost_sell) * CAPITAL, 2),
        "pnl_slot_pct": round(pnl_slot * 100, 2),
        "gross_pct": round(gross, 2),
        "gross_source": "zero_cost_backtest" if gross_zero is not None else "sum_pnl",
        "cost_impact_pp": round(cost_impact, 2),
        "cost_impact_amount": round(cost_impact / 100 * CAPITAL, 2),
        "explained_net_pct": round(gross + cost_impact, 2),
        "unexplained_pp": 0.0,
    }

    out = {
        "job": job,
        "period": {"d0": d0, "d1": d1, "trading_days": len(eq)},
        "capital": CAPITAL,
        "end_asset": round(end_asset, 2),
        "pnl_amount": round(end_asset - CAPITAL, 2),
        "total_return_pct": round((end_asset / CAPITAL - 1) * 100, 2),
        "bench": {
            "name": "全市场等权（%d 只文件）" % n_files,
            "end_asset": round(base_end, 2),
            "total_return_pct": round((base_end / CAPITAL - 1) * 100, 2),
            "excess_pct": round(((end_asset / CAPITAL) - (base_end / CAPITAL)) * 100, 2),
        },
        "risk": {
            "max_drawdown_pct": summ["max_drawdown_pct"],
            "calmar": summ["calmar"],
            "worst_day_pct": min(
                (daily[i]["asset"] / daily[i - 1]["asset"] - 1) * 100
                for i in range(1, len(daily))),
            "best_day_pct": max(
                (daily[i]["asset"] / daily[i - 1]["asset"] - 1) * 100
                for i in range(1, len(daily))),
        },
        "trades": {
            "n_closed": len(rows),
            "win_rate_pct": summ["win_rate_pct"],
            "profit_factor": summ["profit_factor"],
            "avg_pnl_pct": round(tr["pnl_pct"].mean(), 3),
            "median_pnl_pct": round(tr["pnl_pct"].median(), 3),
            "avg_win_pct": round(tr[tr.pnl_pct > 0]["pnl_pct"].mean(), 3),
            "avg_loss_pct": round(tr[tr.pnl_pct <= 0]["pnl_pct"].mean(), 3),
            "best": tr.loc[tr.pnl_pct.idxmax()].to_dict(),
            "worst": tr.loc[tr.pnl_pct.idxmin()].to_dict(),
            "avg_notional": round(tr["notional"].mean(), 2),
            "total_notional": round(total_notional, 2),
            "cost_est": round(cost_est, 2),
            "cost_pct_of_capital": round(cost_est / CAPITAL * 100, 2),
            "uniq_stocks": len(by_stock),
            "max_trades_one_stock": max(per_stock_cnt),
            # contrib 升序：前 5 = 最亏，尾 5 倒序 = 最赚
            "top_contrib": contrib[-5:][::-1],
            "worst_contrib": contrib[:5],
        },
        "exposure": {
            "avg_inv_pct": summ["avg_invested_pct"],
            "avg_positions": round(
                sum(x["n_pos"] for x in days_rec.values()) / len(days_rec), 2),
            "max_positions": max(x["n_pos"] for x in days_rec.values()),
            "days_with_position": sum(1 for x in days_rec.values() if x["n_pos"] > 0),
        },
        "by_regime": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)} for k, v in reg.items()},
        "cost_breakdown": cost_break,
        "monthly": monthly,
        "config": cfg,
        "daily": daily,
    }
    with open(os.path.join(OUT, "bt10m_%s.json" % job), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)

    # ---------- 打印 ----------
    print("区间 %s → %s（%d 交易日）" % (d0, d1, len(eq)))
    print("期初 %s → 期末 %s   盈亏 %s（%+.2f%%）"
          % (f"{CAPITAL:,.0f}", f"{end_asset:,.0f}",
             f"{end_asset - CAPITAL:,.0f}", out["total_return_pct"]))
    print("基准 %s：%+.2f%%   超额 %+.2f%%"
          % (out["bench"]["name"], out["bench"]["total_return_pct"],
             out["bench"]["excess_pct"]))
    print("最大回撤 %.2f%%   最差单日 %.2f%%   最好单日 %+.2f%%"
          % (out["risk"]["max_drawdown_pct"], out["risk"]["worst_day_pct"],
             out["risk"]["best_day_pct"]))
    print("交易 %d 笔（%d 只票）胜率 %.1f%% PF %.2f 均笔 %.2f%% 中位 %.2f%%"
          % (len(rows), len(by_stock), summ["win_rate_pct"], summ["profit_factor"],
             tr["pnl_pct"].mean(), tr["pnl_pct"].median()))
    print("平均仓位 %.1f%%  平均持仓 %.2f 只  最大 %d 只  有仓 %d/%d 天"
          % (summ["avg_invested_pct"], out["exposure"]["avg_positions"],
             out["exposure"]["max_positions"], out["exposure"]["days_with_position"],
             len(days_rec)))
    cb = cost_break
    print("换手 %.2f 倍本金：往返成本 %.2f%%（%s 元）"
          % (cb["turnover_x"], cb["cost_total_pct"],
             f"{cb['cost_total_amount']:,.0f}"))
    print("分解：零成本选股 %+.2f%%（实测） 成本拖累 %+.2fpp  = 净值 %+.2f%%"
          % (cb["gross_pct"], cb["cost_impact_pp"], out["total_return_pct"]))
    print("按月：")
    for m in monthly:
        print("   %s  %+7.2f%%  基准 %+6.2f%%  超额 %+6.2f%%  仓%5.1f%%  盈亏 %s"
              % (m["month"], m["ret_pct"], m["bench_pct"], m["excess_pct"],
                 m["avg_inv_pct"], f"{m['pnl_amount']:,.0f}"))


if __name__ == "__main__":
    main()
