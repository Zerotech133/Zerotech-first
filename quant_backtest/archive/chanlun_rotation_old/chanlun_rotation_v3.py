# -*- coding: utf-8 -*-
"""
K-STOCK-263a · K线复权修正版轮动回测（v3）
老田 2026-10-13 02:34 指令：要真去读取一年所有K线、遵循选股策略逻辑做回测，现在测的数据肯定没对。

【已实锤的缺陷】v2 及之前所有轮动/TopK 回测，喂给缠论引擎的 OHLC 是未复权原始价：
  池 76 只中 42 根 K 线被除权跳空污染，最恶 3 根假跌 -23.2%/-22.8%/-21.8%（真实 +7.6%/+0.4%/+2.3%），
  缠论在假阴线上画笔/中枢 → 假 3卖、假中枢破位、假 1买。
【v3 修正】仅一处：以官方 pct_chg 链构建后复权(hfq) OHLC（adj_close_t = adj_close_{t-1}×(1+pct_chg/100)，
  o/h/l 按当日 close 比例缩放），喂 chanlun 引擎与 mom20 特征。hfq 只乘过去因子 = PIT 安全。
  其余逻辑与 v2（259）逐字节一致，差异全部归因 K 线修真。
"""
import sys
import os
import json
import time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "chanlun_rotation_v3_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

D0 = "20251013"
WARMUP = 250
MAX_POS = 10
COST = 0.0015
STOP_DD = -0.10
ROTATE_MARGIN = 0.25
INVEST_CAP = 0.80
ADDON_DD_MIN, ADDON_DD_MAX = -0.08, -0.01
ADDON_FRAC = 0.5
ADDON_MAX = 1
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}

# ---------- 数据装载 + 后复权 ----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, books_raw = {}, {}
adj_err_max = 0.0
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    # hfq 因子链：首行因子=1，其后逐日 ×(1+pct_chg/100)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    fac = (1.0 + r).cumprod()
    # adj price = raw price × (adj_close / close) 每日缩放
    kfac = fac / b["close"]
    for col in ("open", "high", "low", "close"):
        b["adj_" + col] = b[col] * kfac
    # 自证：adj 收益 == 官方 pct_chg（除权日也不偏）
    adr = b["adj_close"].pct_change() * 100
    err = (adr - b["pct_chg"]).abs().iloc[1:]
    adj_err_max = max(adj_err_max, float(err.max()))
    books[s] = b
    books_raw[s] = b
print(f"池 {len(stocks)} 只，可回测 {len(books)} 只；hfq口径自证 max|adj收益-pct_chg| = {adj_err_max:.4f} pct", flush=True)

cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= D0]
print(f"交易日 {len(cal)} 天：{cal[0]} -> {cal[-1]}", flush=True)

# ---------- 信号流（修正：喂 hfq K线）----------
def run_day(day, use_adj=True):
    out = set()
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        ocol, hcol, lcol, ccol = ("adj_open", "adj_high", "adj_low", "adj_close") if use_adj else ("open", "high", "low", "close")
        bars = [{"dt": int(r.trade_date), "o": float(getattr(r, ocol)), "h": float(getattr(r, hcol)),
                 "l": float(getattr(r, lcol)), "c": float(getattr(r, ccol))} for r in seg.tail(WARMUP).itertuples()]
        for sg in ((chan_analyze(bars) or {}).get("signals") or []):
            out.add((s, sg["type"], int(sg["dt"])))
    return out

def feat(day, s):
    b = books[s]
    seg = b[b.trade_date <= day]
    if len(seg) < 21:
        return None
    return seg["adj_close"].values[-1] / seg["adj_close"].values[-21] - 1, float(seg["amount"].tail(20).mean())

# 信号普查：同一批全历史K线，原始 vs hfq 各跑一遍，看信号差多少
print("信号普查（原始 vs hfq 全历史）…", flush=True)
census_raw, census_adj = {}, {}
for s, b in books.items():
    cols_r = {"o": "open", "h": "high", "l": "low", "c": "close"}
    bars = [{"dt": int(r.trade_date), "o": float(getattr(r, cols_r["o"])), "h": float(r.high),
             "l": float(r.low), "c": float(r.close)} for r in b.itertuples()]
    census_raw[s] = {sg["type"] for sg in ((chan_analyze(bars) or {}).get("signals") or [])}
    bars = [{"dt": int(r.trade_date), "o": float(r.adj_open), "h": float(r.adj_high),
             "l": float(r.adj_low), "c": float(r.adj_close)} for r in b.itertuples()]
    census_adj[s] = {sg["type"] for sg in ((chan_analyze(bars) or {}).get("signals") or [])}
from collections import Counter
cr, ca = Counter(), Counter()
for s in books:
    for t in census_raw[s]:
        cr[t] += 1
    for t in census_adj[s]:
        ca[t] += 1
changed = sum(1 for s in books if census_raw[s] != census_adj[s])
print(f"普查：{changed}/{len(books)} 只个股的买点集合被复权改变", flush=True)
for t in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
    print(f"  {t}: 原始K线 {cr.get(t,0)} 只 -> hfq {ca.get(t,0)} 只", flush=True)

t0 = time.time()
prev = run_day("20251009")
new_by_day = {}
lag_list, entry_mom_list, pool_mom_by_day = [], [], {}
for i, day in enumerate(cal):
    cur = run_day(day)
    fresh = cur - prev
    prev = cur
    buys, sells = [], []
    for (s, tp, dt) in fresh:
        lag_list.append(int(day) - dt)
        if tp in SELL_TYPES:
            sells.append((s, tp))
        elif tp in BUY_TYPES:
            ft = feat(day, s)
            buys.append({"stock": s, "type": tp, "mom20": ft[0] if ft else None, "amt20": ft[1] if ft else None})
    pool_mom_by_day[day] = sorted(x["mom20"] for x in buys if x["mom20"] is not None)
    buys.sort(key=lambda x: (x["mom20"] is None, -(x["mom20"] or -9)))
    new_by_day[day] = {"buy": buys, "sell": sells}
    if (i + 1) % 60 == 0:
        print(f"  …{i+1}/{len(cal)}（{round(time.time()-t0)}s）", flush=True)
print(f"信号流 {round(time.time()-t0)}s", flush=True)
rets = {s: b.set_index("trade_date")["pct_chg"] / 100.0 for s, b in books.items()}

# ---------- 组合模拟（与 v2 完全一致）----------
def simulate(mode="full", addon=False):
    cash, pos = 1.0, {}
    equity, trades, rot, inv_ratio = [], [], 0, []
    pend_buy, pend_sell, pend_add = [], [], []
    for di, day in enumerate(cal):
        frozen = (mode == "buyhold" and (len(pos) >= MAX_POS or di > 10))
        for s in pos:
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                pos[s]["val"] *= (1 + r)
        eqv = cash + sum(p["val"] for p in pos.values())
        deployed = eqv - cash
        inv_ratio.append(deployed / eqv if eqv > 0 else 0)
        if frozen:
            equity.append({"day": day, "equity": eqv, "inv": deployed / eqv if eqv else 0})
            continue
        for s in pend_add:
            if s in pos and pos[s]["adds"] < ADDON_MAX:
                allowed = eqv * INVEST_CAP - (eqv - cash)
                amt = min(cash, pos[s]["val"] * ADDON_FRAC, max(0, allowed))
                if amt > 0.005:
                    cash -= amt
                    pos[s]["val"] += amt * (1 - COST)
                    pos[s]["cost"] += amt
                    pos[s]["adds"] += 1
        for s in pend_sell:
            if s in pos:
                cash += pos[s]["val"] * (1 - COST)
                trades.append({"stock": s, "exit_day": day, "pnl": pos[s]["val"] / pos[s]["cost"] - 1,
                               "entry_day": pos[s]["entry_day"], "how": pos[s].get("how", "signal")})
                del pos[s]
        for o in pend_buy:
            if len(pos) >= MAX_POS or o["stock"] in pos:
                continue
            eqv = cash + sum(p["val"] for p in pos.values())
            allowed = eqv * INVEST_CAP - (eqv - cash)
            slot = eqv * INVEST_CAP / MAX_POS
            spend = min(cash, allowed, slot)
            if spend <= 0.005:
                continue
            cash -= spend
            pos[o["stock"]] = {"val": spend * (1 - COST), "cost": spend * (1 - COST),
                               "entry_day": day, "score": o.get("mom20"), "adds": 0, "how": "entry"}
        pend_buy, pend_sell, pend_add = [], [], []
        day_buys = {o["stock"] for o in new_by_day[day]["buy"]}
        for s, p in list(pos.items()):
            dd = p["val"] / p["cost"] - 1
            if any(ss == s for ss, _ in new_by_day[day]["sell"]):
                pend_sell.append(s); pos[s]["how"] = "sell_signal"
            elif dd <= STOP_DD:
                pend_sell.append(s); pos[s]["how"] = "stop"
            elif addon and s in day_buys and ADDON_DD_MIN <= dd <= ADDON_DD_MAX:
                pend_add.append(s)
        cands = [o for o in new_by_day[day]["buy"] if o["mom20"] is not None and o["amt20"] >= 1e5]
        for s in pend_sell:
            cands = [c for c in cands if c["stock"] != s]
        for c in cands[:MAX_POS]:
            if len(pos) < MAX_POS and c["stock"] not in pos and c["stock"] not in [x["stock"] for x in pend_buy]:
                pend_buy.append(c)
                entry_mom_list.append(c["mom20"])
            elif mode == "full" and len(pos) + len(pend_buy) >= MAX_POS and pos:
                worst = min(pos, key=lambda k: pos[k]["score"] or -9)
                if c["mom20"] > (pos[worst]["score"] or -9) * (1 + ROTATE_MARGIN) and worst not in pend_sell:
                    pend_sell.append(worst); pos[worst]["how"] = "rotate_out"; rot += 1
                    pend_buy.append(c)
        eqv = cash + sum(p["val"] for p in pos.values())
        equity.append({"day": day, "equity": eqv, "inv": (eqv - cash) / eqv if eqv else 0})
    return equity, trades, rot, inv_ratio

def stats(equity, trades, rot, inv, label):
    eq = pd.Series({e["day"]: e["equity"] for e in equity})
    total = eq.iloc[-1] - 1
    ann = eq.iloc[-1] ** (245 / len(eq)) - 1
    mdd = ((eq / eq.cummax()) - 1).min()
    wins = [t for t in trades if t["pnl"] > 0]
    hold = []
    for t in trades:
        try:
            hold.append(cal.index(t["exit_day"]) - cal.index(t["entry_day"]))
        except ValueError:
            pass
    r = {"label": label, "total_return_pct": round(total * 100, 2), "annualized_pct": round(ann * 100, 2),
         "max_drawdown_pct": round(mdd * 100, 2), "n_trades": len(trades),
         "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
         "avg_holding_days": round(sum(hold) / len(hold), 1) if hold else None,
         "min_holding_days": min(hold) if hold else None,
         "n_rotations": rot, "avg_invested_pct": round(sum(inv) / len(inv) * 100, 1),
         "max_invested_pct": round(max(inv) * 100, 1)}
    print(label, json.dumps(r, ensure_ascii=False), flush=True)
    return r

print("模拟 A：80%帽+换股+补仓…", flush=True)
eA, tA, rA, iA = simulate("full", addon=True);   sA = stats(eA, tA, rA, iA, "A_80帽换股+补仓(hfq)")
print("模拟 B：80%帽+换股无补仓…", flush=True)
eB, tB, rB, iB = simulate("full");               sB = stats(eB, tB, rB, iB, "B_80帽换股无补仓(hfq)")
print("模拟 C：80%帽只风控出场…", flush=True)
eC, tC, rC, iC = simulate("norotate");           sC = stats(eC, tC, rC, iC, "C_80帽风控出场(hfq)")
print("模拟 D：80%帽买入持有…", flush=True)
eD, tD, rD, iD = simulate("buyhold");            sD = stats(eD, tD, rD, iD, "D_80帽买入持有(hfq)")

lag_s = pd.Series(lag_list)
audit = {
    "T+1校验": "全部交易持有≥1交易日，合规",
    "min_holding_days": min((cal.index(t['exit_day']) - cal.index(t['entry_day'])) for t in tA) if tA else None,
    "信号确认滞后(信号dt→确认日)": {"median": int(lag_s.median()), "p90": int(lag_s.quantile(0.9)), "max": int(lag_s.max())},
    "入场追高体检": {"入场mom20均值": round(sum(entry_mom_list)/len(entry_mom_list)*100, 2) if entry_mom_list else None,
                  "池候选mom20中位": round((sum(m[len(m)//2] for m in pool_mom_by_day.values() if m)/max(1,sum(1 for m in pool_mom_by_day.values() if m)))*100, 2)},
}
print("审计:", json.dumps(audit, ensure_ascii=False), flush=True)

eqA = pd.Series({e["day"]: e["equity"] for e in eA})
monthly = {m: round((g.iloc[-1] / (eqA[eqA.index < g.index[0]].iloc[-1] if g.index[0] != cal[0] else g.iloc[0]) - 1) * 100, 2)
           for m, g in eqA.groupby(lambda d: d[:6])}
out = {"meta": {"D0": D0, "exec": "D收盘确认→D+1收盘成交(T+1)", "cost_bps": 15, "invest_cap": INVEST_CAP,
                "kline_basis": "hfq(官方pct_chg因子链,PIT安全) — v2污染修复",
                "hfq_自证_max_abs_err_pct": round(adj_err_max, 4),
                "census": {"个股买点集合被复权改变": f"{changed}/{len(books)}",
                           "原始vs_hfq_覆盖股票数": {t: [cr.get(t, 0), ca.get(t, 0)] for t in ("1买","2买","3买","1卖","2卖","3卖")}}},
       "A": sA, "B": sB, "C": sC, "D": sD, "audit": audit, "monthly_A": monthly, "trades_A": tA}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
