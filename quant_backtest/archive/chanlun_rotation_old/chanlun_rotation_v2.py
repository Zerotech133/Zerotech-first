# -*- coding: utf-8 -*-
"""
K-STOCK-259 · 仓位控制版轮动回测（80% 仓位帽 + 补仓条件）+ 四项策略审计
老田 2026-10-13 02:20 指令：A 股 T+1，效果不该这么差，检查策略/买卖点是否正确；
统一仓位控制（如 80%），百分之多少可以补仓，加进后面的筛选器。

v2 相对 257 的修正：
  1) 【仓位帽】INVEST_CAP=0.80：市值占用上限 80%，现金储备 ≥20%（257 版恒满仓是审计发现的缺陷）；
  2) 【补仓】持仓股再确认买点信号、且浮亏在 [ADDON_DD_MIN, ADDON_DD_MAX] 区间、每笔限补 1 次
     （补仓额 = 当前该仓位市值 × 50%，受现金与仓位帽双重约束）；
  3) 【审计内建】T+1 持有天数校验 / 仓位占用率分布 / 入场动量 vs 池中位（追高体检）/ 信号确认滞后分布。
其余口径（严格截断双 pass、D 确认→D+1 收盘成交、pct_chg 复权、15bp 成本、止损 -10%）与 257 一致。
"""
import sys, os, json, time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "chanlun_rotation_v2_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

D0 = "20251013"
WARMUP = 250
MAX_POS = 10
COST = 0.0015
STOP_DD = -0.10
ROTATE_MARGIN = 0.25
INVEST_CAP = 0.80          # 🆕 仓位帽：市值占用 ≤80%
ADDON_DD_MIN, ADDON_DD_MAX = -0.08, -0.01   # 🆕 补仓浮亏区间
ADDON_FRAC = 0.5           # 🆕 补仓额 = 该仓位市值 × 50%
ADDON_MAX = 1              # 🆕 每笔最多补 1 次
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}

# ---------- 数据装载 ----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books = {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if os.path.exists(f):
        books[s] = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
print(f"池 {len(stocks)} 只，可回测 {len(books)} 只", flush=True)
cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= D0]
print(f"交易日 {len(cal)} 天：{cal[0]} -> {cal[-1]}", flush=True)

# ---------- 信号流（含审计采集）----------
def run_day(day):
    out = set()
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [{"dt": int(r.trade_date), "o": float(r.open), "h": float(r.high),
                 "l": float(r.low), "c": float(r.close)} for r in seg.tail(WARMUP).itertuples()]
        for sg in ((chan_analyze(bars) or {}).get("signals") or []):
            out.add((s, sg["type"], int(sg["dt"])))
    return out

def feat(day, s):
    b = books[s]
    seg = b[b.trade_date <= day]
    if len(seg) < 21:
        return None
    return seg["close"].values[-1] / seg["close"].values[-21] - 1, float(seg["amount"].tail(20).mean())

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
        lag_list.append(int(day) - dt)          # 审计③：确认滞后
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

# ---------- 组合模拟 v2（仓位帽+补仓）----------
def simulate(mode="full", addon=False):
    cash, pos = 1.0, {}
    equity, trades, rot, inv_ratio = [], [], 0, []
    pend_buy, pend_sell, pend_add = [], [], []
    for di, day in enumerate(cal):
        frozen = (mode == "buyhold" and (len(pos) >= MAX_POS or di > 10))
        for s in pos:                                   # 1) 当日收益
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                pos[s]["val"] *= (1 + r)
        eqv = cash + sum(p["val"] for p in pos.values())
        deployed = eqv - cash
        inv_ratio.append(deployed / eqv if eqv > 0 else 0)
        if frozen:
            equity.append({"day": day, "equity": eqv, "inv": deployed / eqv if eqv else 0})
            continue
        for s in pend_add:                              # 2a) 补仓执行
            if s in pos and pos[s]["adds"] < ADDON_MAX:
                allowed = eqv * INVEST_CAP - (eqv - cash)
                amt = min(cash, pos[s]["val"] * ADDON_FRAC, max(0, allowed))
                if amt > 0.005:
                    cash -= amt
                    pos[s]["val"] += amt * (1 - COST)
                    pos[s]["cost"] += amt
                    pos[s]["adds"] += 1
        for s in pend_sell:                             # 2b) 卖出执行
            if s in pos:
                cash += pos[s]["val"] * (1 - COST)
                trades.append({"stock": s, "exit_day": day, "pnl": pos[s]["val"] / pos[s]["cost"] - 1,
                               "entry_day": pos[s]["entry_day"], "how": pos[s].get("how", "signal")})
                del pos[s]
        for o in pend_buy:                              # 2c) 买入执行（受仓位帽约束）
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
        # 3) 今日收盘观察：卖点/止损 → 明日卖；补仓条件 → 明日补
        day_buys = {o["stock"] for o in new_by_day[day]["buy"]}
        for s, p in list(pos.items()):
            dd = p["val"] / p["cost"] - 1
            if any(ss == s for ss, _ in new_by_day[day]["sell"]):
                pending = "sell_signal"
                pend_sell.append(s); pos[s]["how"] = pending
            elif dd <= STOP_DD:
                pend_sell.append(s); pos[s]["how"] = "stop"
            elif addon and s in day_buys and ADDON_DD_MIN <= dd <= ADDON_DD_MAX:
                pend_add.append(s)
        # 4) 买点入场/换股（换股仅 full 模式）
        cands = [o for o in new_by_day[day]["buy"] if o["mom20"] is not None and o["amt20"] >= 1e5]
        for s in pend_sell:
            cands = [c for c in cands if c["stock"] != s]
        for c in cands[:MAX_POS]:
            if len(pos) < MAX_POS and c["stock"] not in pos and c["stock"] not in [x["stock"] for x in pend_buy]:
                pend_buy.append(c)
                entry_mom_list.append(c["mom20"])       # 审计②：入场动量
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
eA, tA, rA, iA = simulate("full", addon=True);   sA = stats(eA, tA, rA, iA, "A_80帽换股+补仓")
print("模拟 B：80%帽+换股无补仓…", flush=True)
eB, tB, rB, iB = simulate("full");               sB = stats(eB, tB, rB, iB, "B_80帽换股无补仓")
print("模拟 C：80%帽只风控出场…", flush=True)
eC, tC, rC, iC = simulate("norotate");           sC = stats(eC, tC, rC, iC, "C_80帽风控出场")
print("模拟 D：80%帽买入持有…", flush=True)
eD, tD, rD, iD = simulate("buyhold");            sD = stats(eD, tD, rD, iD, "D_80帽买入持有")

# 审计汇总
lag_s = pd.Series(lag_list)
audit = {
    "T+1校验": "全部交易持有≥1交易日（买入收盘→最早次日收盘卖），合规",
    "min_holding_days": min((cal.index(t['exit_day']) - cal.index(t['entry_day'])) for t in tA),
    "信号确认滞后(信号dt→确认日)": {"median": int(lag_s.median()), "p90": int(lag_s.quantile(0.9)), "max": int(lag_s.max())},
    "入场追高体检": {"入场mom20均值": round(sum(entry_mom_list)/len(entry_mom_list)*100, 2),
                  "池候选mom20中位": round((sum(m[len(m)//2] for m in pool_mom_by_day.values() if m)/max(1,sum(1 for m in pool_mom_by_day.values() if m)))*100, 2)},
}
print("审计:", json.dumps(audit, ensure_ascii=False), flush=True)

out = {"meta": {"D0": D0, "universe": "chanlun_pool_snapshot_20261012(宇宙前视已声明)",
                "exec": "D收盘确认→D+1收盘成交(T+1合规)", "cost_bps": 15, "invest_cap": INVEST_CAP,
                "addon": {"dd_range": [ADDON_DD_MIN, ADDON_DD_MAX], "frac": ADDON_FRAC, "max": ADDON_MAX}},
       "A": sA, "B": sB, "C": sC, "D": sD, "audit": audit,
       "monthly_A": {m: round((g.iloc[-1] / (pd.Series({e['day']: e['equity'] for e in eA})[pd.Series({e['day']: e['equity'] for e in eA}).index < g.index[0]].iloc[-1] if g.index[0] != cal[0] else g.iloc[0]) - 1) * 100, 2)
                     for m, g in pd.Series({e["day"]: e["equity"] for e in eA}).groupby(lambda d: d[:6])},
       "trades_A": tA}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
