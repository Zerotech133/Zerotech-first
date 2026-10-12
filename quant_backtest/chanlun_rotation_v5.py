# -*- coding: utf-8 -*-
"""
K-STOCK-265 · v5 = v4(hfq + seen-ever) + 回撤引擎 + runup 入场帽
老田 2026-10-13 02:52 指令：胜率 23% 回撤 -36% 不行，一次性把回撤引擎加进去。

【回撤引擎（组合级三级风控，盯高水位线 dd=equity/HWM-1）】
  G1 dd<=-5%  ：入场额度减半(slot×0.5)，个股止损收紧 -10%→-6%
  G2 dd<=-10% ：禁止新开仓，砍掉动量分最差的一半持仓（次日执行）
  G3 dd<=-15% ：全部清仓（成本 15bp），冷却 10 个交易日后以 G1 半仓恢复；
                dd 回升到 >=-3% 才解除警戒归零（滞回，防反复打脸）
【runup 入场帽（263 探针4 实证）】确认日收盘价相对信号日价涨幅 >15% 一票否决（毒性桶 -5.91%/胜率20%）。
【对照臂】A5=仅runup帽 / B5=runup帽+回撤引擎 / C5=仅回撤引擎；v4 基线 A=-31.50%/23.4%。
其余口径与 v4 完全一致（hfq、seen-ever、80%帽、T+1、15bp）。
"""
import sys, os, json, time
import pandas as pd

BASE = r"E:/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "chanlun_rotation_v5_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

D0 = "20251013"
WARMUP = 250
MAX_POS = 10
COST = 0.0015
STOP_DD = -0.10
STOP_DD_TIGHT = -0.06      # G1 收紧止损
ROTATE_MARGIN = 0.25
INVEST_CAP = 0.80
ADDON_DD_MIN, ADDON_DD_MAX = -0.08, -0.01
ADDON_FRAC = 0.5
ADDON_MAX = 1
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}
RUNUP_CAP = 0.15           # 🆕 入场帽：确认日收盘/信号日价 -1 > 15% 否决
# 🆕 回撤引擎参数
G1_DD, G2_DD, G3_DD = -0.05, -0.10, -0.15
GUARD_RELEASE_DD = -0.03
GUARD_COOLDOWN = 10
SLOT_SHRINK = 0.5

# ---------- 数据装载 + 后复权 ----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, adj_close = {}, {}
adj_err_max = 0.0
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    for col in ("open", "high", "low", "close"):
        b["adj_" + col] = b[col] * kfac
    adr = b["adj_close"].pct_change() * 100
    err = (adr - b["pct_chg"]).abs().iloc[1:]
    adj_err_max = max(adj_err_max, float(err.max()))
    books[s] = b
    adj_close[s] = b.set_index("trade_date")["adj_close"]
print(f"池 {len(stocks)} 只，可回测 {len(books)} 只；hfq自证 max|adj收益-pct_chg|={adj_err_max:.4f}", flush=True)

cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= D0]
print(f"交易日 {len(cal)} 天：{cal[0]} -> {cal[-1]}", flush=True)

# ---------- 信号流（hfq + seen-ever）----------
def run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [{"dt": int(r.trade_date), "o": float(r.adj_open), "h": float(r.adj_high),
                 "l": float(r.adj_low), "c": float(r.adj_close)} for r in seg.tail(WARMUP).itertuples()]
        for sg in ((chan_analyze(bars) or {}).get("signals") or []):
            out[(s, sg["type"])] = int(sg["dt"])
    return out

def feat(day, s):
    b = books[s]
    seg = b[b.trade_date <= day]
    if len(seg) < 21:
        return None
    return seg["adj_close"].values[-1] / seg["adj_close"].values[-21] - 1, float(seg["amount"].tail(20).mean())

t0 = time.time()
prev = run_day("20251009")
seen_ever = set(prev)
new_by_day = {}
lag_list, entry_mom_list, n_runup_blocked = [], [], 0
import datetime as _dtmod
def _lag_days(day, sig_dt):
    return (_dtmod.datetime.strptime(str(day), "%Y%m%d") - _dtmod.datetime.strptime(str(sig_dt), "%Y%m%d")).days
for i, day in enumerate(cal):
    cur = run_day(day)
    fresh = [k for k in cur if k not in seen_ever]
    seen_ever |= set(cur)
    prev = cur
    buys, sells = [], []
    for (s, tp) in fresh:
        dt = cur[(s, tp)]
        lag_list.append(_lag_days(day, dt))
        if tp in SELL_TYPES:
            sells.append((s, tp))
        elif tp in BUY_TYPES:
            ft = feat(day, s)
            p_sig = adj_close[s].get(str(dt))
            p_now = adj_close[s].get(day)
            runup = (p_now / p_sig - 1) if (p_sig and p_now and not pd.isna(p_sig) and not pd.isna(p_now)) else None
            buys.append({"stock": s, "type": tp, "mom20": ft[0] if ft else None,
                         "amt20": ft[1] if ft else None, "runup_d": runup})
    pool_buys = buys
    blocked = [x for x in buys if x["runup_d"] is not None and x["runup_d"] > RUNUP_CAP]
    n_runup_blocked += len(blocked)          # 仅统计；帽在 simulate 内按臂开关
    buys.sort(key=lambda x: (x["mom20"] is None, -(x["mom20"] or -9)))
    new_by_day[day] = {"buy": buys, "sell": sells}
    if (i + 1) % 60 == 0:
        print(f"  …{i+1}/{len(cal)}（{round(time.time()-t0)}s）", flush=True)
print(f"信号流 {round(time.time()-t0)}s；runup帽拦掉候选 {n_runup_blocked} 个", flush=True)
rets = {s: b.set_index("trade_date")["pct_chg"] / 100.0 for s, b in books.items()}

# ---------- 组合模拟 v5（+回撤引擎）----------
def simulate(mode="full", addon=False, guard_on=False, runup_filter=True):
    cash, pos = 1.0, {}
    equity, trades, rot, inv_ratio, guard_hist = [], [], 0, [], []
    pend_buy, pend_sell, pend_add = [], [], []
    hwm, guard, cooldown = 1.0, 0, 0
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
        # ===== 回撤引擎状态机（盯 HWM）=====
        if guard_on:
            hwm = max(hwm, eqv)
            dd = eqv / hwm - 1
            if dd <= G3_DD and cooldown <= 0 and guard < 3:
                guard = 3
                cooldown = GUARD_COOLDOWN
                for s, p in list(pos.items()):     # G3：清仓
                    cash += p["val"] * (1 - COST)
                    trades.append({"stock": s, "exit_day": day, "pnl": p["val"] / p["cost"] - 1,
                                   "entry_day": p["entry_day"], "how": "guard_liq"})
                    del pos[s]
                pend_buy, pend_sell, pend_add = [], [], []
            elif dd <= G2_DD:
                guard = max(guard, 2)
            elif dd <= G1_DD:
                guard = max(guard, 1)
            elif dd >= GUARD_RELEASE_DD and cooldown <= 0:
                guard = 0
            if cooldown > 0:
                cooldown -= 1
                if cooldown == 0 and guard == 3:
                    guard = 1                       # 冷却结束：半仓恢复
            guard_hist.append({"day": day, "guard": guard, "dd": round(dd, 4)})
        # ===== 补仓 =====
        for s in pend_add:
            if s in pos and pos[s]["adds"] < ADDON_MAX:
                allowed = eqv * INVEST_CAP - (eqv - cash)
                amt = min(cash, pos[s]["val"] * ADDON_FRAC, max(0, allowed))
                if amt > 0.005:
                    cash -= amt
                    pos[s]["val"] += amt * (1 - COST)
                    pos[s]["cost"] += amt
                    pos[s]["adds"] += 1
        # ===== 卖出 =====
        for s in pend_sell:
            if s in pos:
                cash += pos[s]["val"] * (1 - COST)
                trades.append({"stock": s, "exit_day": day, "pnl": pos[s]["val"] / pos[s]["cost"] - 1,
                               "entry_day": pos[s]["entry_day"], "how": pos[s].get("how", "signal")})
                del pos[s]
        # ===== 买入（受 guard 降额/禁止）=====
        slot_scale = SLOT_SHRINK if guard >= 1 else 1.0
        entry_blocked = (guard >= 2)
        for o in pend_buy:
            if entry_blocked or len(pos) >= MAX_POS or o["stock"] in pos:
                continue
            eqv = cash + sum(p["val"] for p in pos.values())
            allowed = eqv * INVEST_CAP - (eqv - cash)
            slot = eqv * INVEST_CAP / MAX_POS * slot_scale
            spend = min(cash, allowed, slot)
            if spend <= 0.005:
                continue
            cash -= spend
            pos[o["stock"]] = {"val": spend * (1 - COST), "cost": spend * (1 - COST),
                               "entry_day": day, "score": o.get("mom20"), "adds": 0, "how": "entry"}
        pend_buy, pend_sell, pend_add = [], [], []
        # ===== 收盘观察 =====
        day_buys = {o["stock"] for o in new_by_day[day]["buy"]}
        stop_level = STOP_DD_TIGHT if (guard_on and guard >= 1) else STOP_DD
        for s, p in list(pos.items()):
            dd_p = p["val"] / p["cost"] - 1
            if any(ss == s for ss, _ in new_by_day[day]["sell"]):
                pend_sell.append(s); pos[s]["how"] = "sell_signal"
            elif dd_p <= stop_level:
                pend_sell.append(s); pos[s]["how"] = "stop"
            elif addon and s in day_buys and ADDON_DD_MIN <= dd_p <= ADDON_DD_MAX:
                pend_add.append(s)
        if guard_on and guard >= 2 and len(pos) >= 2:      # G2：砍最差一半
            worst = sorted(pos, key=lambda k: pos[k]["score"] or -9)
            for s in worst[:len(worst) // 2]:
                if s not in pend_sell:
                    pend_sell.append(s); pos[s]["how"] = "guard_cut"
        # ===== 入场/换股 =====
        cands = [o for o in new_by_day[day]["buy"] if o["mom20"] is not None and o["amt20"] >= 1e5]
        if runup_filter:
            cands = [c for c in cands if not (c["runup_d"] is not None and c["runup_d"] > RUNUP_CAP)]
        for s in pend_sell:
            cands = [c for c in cands if c["stock"] != s]
        for c in cands[:MAX_POS]:
            if len(pos) < MAX_POS and c["stock"] not in pos and c["stock"] not in [x["stock"] for x in pend_buy]:
                if entry_blocked:
                    continue
                pend_buy.append(c)
                entry_mom_list.append(c["mom20"])
            elif mode == "full" and not entry_blocked and len(pos) + len(pend_buy) >= MAX_POS and pos:
                worst = min(pos, key=lambda k: pos[k]["score"] or -9)
                if c["mom20"] > (pos[worst]["score"] or -9) * (1 + ROTATE_MARGIN) and worst not in pend_sell:
                    pend_sell.append(worst); pos[worst]["how"] = "rotate_out"; rot += 1
                    pend_buy.append(c)
        eqv = cash + sum(p["val"] for p in pos.values())
        equity.append({"day": day, "equity": eqv, "inv": (eqv - cash) / eqv if eqv else 0})
    return equity, trades, rot, inv_ratio, guard_hist

def stats(equity, trades, rot, inv, label, guard_hist=None):
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
         "n_rotations": rot, "avg_invested_pct": round(sum(inv) / len(inv) * 100, 1)}
    if guard_hist:
        gs = [g["guard"] for g in guard_hist]
        r["guard_days"] = {"G1": gs.count(1), "G2": gs.count(2), "G3": gs.count(3),
                           "G0": gs.count(0)}
        r["guard_liq_events"] = len({t["exit_day"] for t in trades if t["how"] == "guard_liq"})
        r["guard_liq_trades"] = sum(1 for t in trades if t["how"] == "guard_liq")
        r["guard_cut_trades"] = sum(1 for t in trades if t["how"] == "guard_cut")
    print(label, json.dumps(r, ensure_ascii=False), flush=True)
    return r

print("A5：仅 runup 帽…", flush=True)
eA, tA, rA, iA, _ = simulate(guard_on=False, runup_filter=True);   sA = stats(eA, tA, rA, iA, "A5_runup帽")
print("B5：runup 帽 + 回撤引擎…", flush=True)
eB, tB, rB, iB, gB = simulate(guard_on=True, runup_filter=True);   sB = stats(eB, tB, rB, iB, "B5_runup帽+回撤引擎", gB)
print("C5：仅回撤引擎（无 runup 帽，隔离归因）…", flush=True)
eC, tC, rC, iC, gC = simulate(guard_on=True, runup_filter=False);  sC = stats(eC, tC, rC, iC, "C5_仅回撤引擎", gC)

lag_s = pd.Series(lag_list)
audit = {
    "信号确认滞后(真实日历日)": {"median": int(lag_s.median()), "p90": int(lag_s.quantile(0.9)), "max": int(lag_s.max())},
    "runup帽拦截候选数": n_runup_blocked,
}
print("审计:", json.dumps(audit, ensure_ascii=False), flush=True)

out = {"meta": {"D0": D0, "exec": "D收盘确认→D+1收盘成交(T+1)", "cost_bps": 15, "invest_cap": INVEST_CAP,
                "kline_basis": "hfq(官方pct_chg因子链) + seen-ever",
                "runup_cap": RUNUP_CAP,
                "guard": {"G1": G1_DD, "G2": G2_DD, "G3": G3_DD, "release": GUARD_RELEASE_DD,
                          "cooldown": GUARD_COOLDOWN, "slot_shrink": SLOT_SHRINK, "tight_stop": STOP_DD_TIGHT}},
       "A5": sA, "B5": sB, "C5": sC, "audit": audit,
       "trades_B5": tB, "trades_C5": tC, "guard_hist_B5": gB, "guard_hist_C5": gC}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
