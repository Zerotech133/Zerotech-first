# -*- coding: utf-8 -*-
"""
K-STOCK-267c · 学院组合制（正常仓位逻辑终版臂）v2
v1 缺陷：忘了初始建仓，全程空仓。v2 修正：
  D0 起对池内所有个股等权分批建仓（未上市/无数据者首个有数据交易日自动进场）；
  止损/卖点退出资金，在新买点出现或 regime 升档时自动回场；regime 减档等比例减仓。
"""
import sys, os, json, time
import pandas as pd

BASE = r"E:/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "chanlun_rotation_v6c2_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

D0 = "20231010"
WARMUP = 250
COST = 0.0015
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}
STOP_PCT = -0.08
CAP_HIGH, CAP_MID, CAP_LOW = 0.80, 0.50, 0.30

# ---------- 数据装载 + hfq ----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, adj_close, rets = {}, {}, {}
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
    books[s] = b
    adj_close[s] = b.set_index("trade_date")["adj_close"]
    rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0

cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal_all if d >= D0]
row_idx = {s: b.set_index("trade_date") for s, b in books.items()}

# ---------- 池等权指数 + regime ----------
idx_ret = {}
for day in cal:
    rs = [rets[s].get(day) for s in books]
    rs = [x for x in rs if x is not None and not pd.isna(x)]
    idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
ma20 = idx_nav.rolling(20).mean()
ma60 = idx_nav.rolling(60).mean()
def regime_cap(day):
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return CAP_MID
    v, m20, m60 = idx_nav.iloc[i-1], ma20.iloc[i-1], ma60.iloc[i-1]
    if pd.isna(m60):
        return CAP_MID
    if v >= m20:
        return CAP_HIGH
    if v >= m60:
        return CAP_MID
    return CAP_LOW

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

t0 = time.time()
prev = run_day("20231009")
seen_ever = set(prev)
sell_days, buy_days = {}, {}
for day in cal:
    cur = run_day(day)
    fresh = [k for k in cur if k not in seen_ever]
    seen_ever |= set(cur)
    prev = cur
    sell_days[day] = {s for (s, tp) in fresh if tp in SELL_TYPES}
    buy_days[day] = {s for (s, tp) in fresh if tp in BUY_TYPES}
    if len(sell_days) % 180 == 0:
        print(f"  …{len(sell_days)}/{len(cal)}（{round(time.time()-t0)}s）", flush=True)
print(f"信号流 {round(time.time()-t0)}s", flush=True)

# ---------- 组合制模拟 v2 ----------
def simulate(use_regime=True, use_sell_signal=True, use_stop=True, stop_pct=STOP_PCT):
    cash, pos = 1.0, {}
    pending_init = set(books)          # 初始建仓名单：有数据即进
    equity, trades, inv_hist = [], [], []
    trim_state = False
    n_trim = n_refill = 0
    SLOT0 = CAP_HIGH / len(books)      # 初始等权槽位（满配下每只 ~1.05%）
    for di, day in enumerate(cal):
        for s in pos:
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                pos[s]["val"] *= (1 + r)
        eqv = cash + sum(p["val"] for p in pos.values())
        # 个股出口：卖点信号 / 结构止损
        for s in list(pos):
            p = pos[s]
            out = False
            if use_sell_signal and s in sell_days.get(day, set()):
                out = True; how = "sell_signal"
            elif use_stop and p["val"] / p["cost"] - 1 <= stop_pct:
                out = True; how = "stop"
            if out:
                cash += p["val"] * (1 - COST)
                trades.append({"stock": s, "exit_day": day, "pnl": p["val"] / p["cost"] - 1,
                               "entry_day": p["entry_day"], "how": how})
                del pos[s]
                pending_init.add(s)            # 退出后回到待(重)进场名单，等新买点
        eqv = cash + sum(p["val"] for p in pos.values())
        cap = regime_cap(day) if use_regime else CAP_HIGH
        deployed = eqv - cash
        target = eqv * cap
        # regime 减档：等比例减仓；升档/有富余：回补
        if deployed > target * 1.01:
            ratio = 1 - target / deployed
            for s in list(pos):
                p = pos[s]
                cut = p["val"] * ratio
                cash += cut * (1 - COST)
                p["val"] -= cut; p["cost"] *= (1 - ratio)
                if p["val"] < 0.0005 * eqv:
                    cash += p["val"] * (1 - COST)
                    del pos[s]
            trim_state = True; n_trim += 1
        # 进场：初始名单（有数据即进）+ 新买点名单；容量允许即进（永不禁开仓）
        want_in = {s for s in pending_init if s in row_idx and day in row_idx[s].index}
        want_in |= {s for s in buy_days.get(day, set()) if s in books} - set(pos)
        for s in sorted(want_in):
            if s in pos or deployed >= target * 0.999:
                continue
            amt = min(cash, eqv * SLOT0)
            if amt <= 0.002:
                continue
            cash -= amt
            pos[s] = {"val": amt * (1 - COST), "cost": amt * (1 - COST), "entry_day": day}
            pending_init.discard(s)
            trades.append({"stock": s, "exit_day": None, "entry_day": day, "how": "open", "pnl": None})
            deployed += amt
        # 已持仓富余回补（升档后恢复满配）
        deployed = eqv - cash
        if deployed < target * 0.99 and pos:
            add_total = min(cash, target - deployed)
            tw = sum(p["val"] for p in pos.values())
            for s, p in pos.items():
                amt = add_total * p["val"] / tw
                if amt > 0.005:
                    cash -= amt; p["val"] += amt * (1 - COST); p["cost"] += amt * (1 - COST)
            if add_total > 0.005:
                trim_state = False; n_refill += 1
        eqv = cash + sum(p["val"] for p in pos.values())
        equity.append({"day": day, "equity": eqv, "inv": (eqv - cash) / eqv if eqv else 0})
        inv_hist.append((eqv - cash) / eqv if eqv else 0)
    print(f"  trim次数={n_trim} 回补次数={n_refill} 最终持仓={len(pos)}", flush=True)
    return equity, trades, inv_hist

def stats(equity, trades, inv, label):
    eq = pd.Series({e["day"]: e["equity"] for e in equity})
    total = eq.iloc[-1] - 1
    ann = eq.iloc[-1] ** (245 / len(eq)) - 1
    mdd = ((eq / eq.cummax()) - 1).min()
    closed = [t for t in trades if t["exit_day"] is not None]
    wins = [t for t in closed if t["pnl"] > 0]
    r = {"label": label, "total_return_pct": round(total * 100, 2), "annualized_pct": round(ann * 100, 2),
         "max_drawdown_pct": round(mdd * 100, 2), "calmar": round(ann / abs(mdd), 2) if mdd else None,
         "n_exits": len(closed),
         "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
         "avg_invested_pct": round(sum(inv) / len(inv) * 100, 1)}
    hows = {}
    for t in closed:
        hows[t["how"]] = hows.get(t["how"], 0) + 1
    r["exit_mix"] = hows
    print(label, json.dumps(r, ensure_ascii=False), flush=True)
    return r

print("C1：组合制+regime调仓+个股风控 …", flush=True)
e1, t1, i1 = simulate(True, True);   s1 = stats(e1, t1, i1, "C1_组合制_full")
print("C2：组合制无regime（恒80%，隔离归因）…", flush=True)
e2, t2, i2 = simulate(False, True);  s2 = stats(e2, t2, i2, "C2_组合制无regime")
print("C3：组合制+regime+卖点出场·无硬止损（决定性臂）…", flush=True)
e3, t3, i3 = simulate(True, True, use_stop=False); s3 = stats(e3, t3, i3, "C3_无硬止损")
print("C4：组合制无regime无硬止损（卖点only）…", flush=True)
e4, t4, i4 = simulate(False, True, use_stop=False); s4 = stats(e4, t4, i4, "C4_卖点only")
print("C5：regime+灾难止损-25%·无卖点（防黑天鹅非防波动）…", flush=True)
e5, t5, i5 = simulate(True, use_sell_signal=False, use_stop=True, stop_pct=-0.25); s5 = stats(e5, t5, i5, "C5_灾难止损25")
print("C6：纯regime管理的买入持有（零个股干预）…", flush=True)
e6, t6, i6 = simulate(True, use_sell_signal=False, use_stop=False); s6 = stats(e6, t6, i6, "C6_纯regime")
print("C7：无regime纯买入持有对照（复算）…", flush=True)
e7, t7, i7 = simulate(False, use_sell_signal=False, use_stop=False); s7 = stats(e7, t7, i7, "C7_纯买入持有")

dr = pd.Series({d: sum(x for x in [rets[s].get(d) for s in books] if x is not None and not pd.isna(x)) / max(1, len(books)) for d in cal})
nav_bh = (1 + dr).cumprod()
print(f"对照_池等权买入持有: total={(nav_bh.iloc[-1]-1)*100:.2f}% mdd={((nav_bh/nav_bh.cummax())-1).min()*100:.2f}%", flush=True)

out = {"meta": {"D0": D0, "cost_bps": 15, "stop": STOP_PCT, "caps": [CAP_HIGH, CAP_MID, CAP_LOW],
                "design": "组合制v2:池等权全持有(无数据首日自动进)+regime 80/50/30连续调仓+个股止损/卖点; 永不禁开仓"},
       "C1": s1, "C2": s2, "C3": s3, "C4": s4, "C5": s5, "C6": s6, "C7": s7,
       "equity_C1": {e["day"]: round(e["equity"], 6) for e in e1},
       "trades_C1": t1, "trades_C3": t3}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
