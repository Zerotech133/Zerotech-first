# -*- coding: utf-8 -*-
"""
K-STOCK-257 · 交易视角轮动回测（换股机制）
老田 2026-10-13 02:12 指令：静态持有 d180 算收益不合理——真实交易中途一定会换股。
本脚本从「正常交易视角」建模：
  - 视角日 D0=2025-10-13，可交易宇宙=缠论自选池（今日快照 76 只，⚠️ 宇宙前视已在文档声明）；
  - 每个交易日严格截断双 pass 产出「当天新确认信号」（与 241/253 同一逻辑）；
  - 信号驱动交易：卖点信号/止损 → 平仓；买点信号 → SCORE_V1 打分 → 空位即买、满仓且分差>阈值换掉最弱持仓（=换股）；
  - 收益核算用官方 pct_chg（pre_close 已除权调整，复权口径，绕开因子湖断档）；
  - 成本：单边 15bp（佣金+滑点+印花简化）；
  - 基线：同池同打分的「买入持有不动」组合——两者之差=换股机制的价值。
执行约定（保守，避免同 bar 前视）：D 日收盘确认的信号 → D+1 日收盘成交（持仓当日仍承担 D+1 日收益，新仓从 D+2 起计收益）。
"""

import sys
import os
import json
import time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "chanlun_rotation_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

D0 = "20251013"  # 视角日
WARMUP = 250  # 结构识别预热（与实时链路同参）
MAX_POS = 10  # 组合持仓上限（=老田"每组选最优十只"）
COST = 0.0015  # 单边成本 15bp
STOP_DD = -0.10  # 个股止损线（相对入场成本）
ROTATE_MARGIN = 0.25  # 换股分差阈值：新分 > 最弱持仓分×(1+margin) 才换
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}

# ---------- 数据装载 ----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
print(f"池子 {len(stocks)} 只，装载行情…", flush=True)

books = {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    df = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    books[s] = df
print(f"实际可回测 {len(books)} 只", flush=True)

# 交易日历（池内并集）
cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= D0]
print(f"交易日 {len(cal)} 天：{cal[0]} -> {cal[-1]}", flush=True)


# ---------- 信号流：逐日严格截断，相邻日差分 ----------
def run_day(day):
    """返回 {(stock,type,dt)}：对每只喂 ≤day 的数据跑 analyze"""
    out = set()
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [
            {
                "dt": int(r.trade_date),
                "o": float(r.open),
                "h": float(r.high),
                "l": float(r.low),
                "c": float(r.close),
            }
            for r in seg.tail(WARMUP).itertuples()
        ]
        sigs = (chan_analyze(bars) or {}).get("signals") or []
        for sg in sigs:
            out.add((s, sg["type"], int(sg["dt"])))
    return out


def feat(day, s):
    """D 日可见特征：mom20 / amt20（amount 千元，1 亿元=1e5 千元）"""
    b = books[s]
    seg = b[b.trade_date <= day]
    if len(seg) < 21:
        return None
    c = seg["close"].values
    mom20 = c[-1] / c[-21] - 1
    amt20 = float(seg["amount"].tail(20).mean())
    return mom20, amt20


t0 = time.time()
prev = run_day("20251009")  # D0 前一交易日基线
new_by_day = {}  # day -> {"buy": [(stock,type,score|None)], "sell": [...]}
for i, day in enumerate(cal):
    cur = run_day(day)
    fresh = cur - prev
    prev = cur
    buys, sells = [], []
    for s, tp, dt in fresh:
        if tp in SELL_TYPES:
            sells.append((s, tp))
        elif tp in BUY_TYPES:
            ft = feat(day, s)
            buys.append(
                {"stock": s, "type": tp, "mom20": ft[0], "amt20": ft[1]}
                if ft
                else {"stock": s, "type": tp, "mom20": None, "amt20": None}
            )
    buys.sort(key=lambda x: (x["mom20"] is None, -(x["mom20"] or -9)))
    new_by_day[day] = {"buy": buys, "sell": sells}
    if (i + 1) % 30 == 0:
        print(f"  …{i + 1}/{len(cal)} 日已算（{round(time.time() - t0)}s）", flush=True)
print(f"信号流完成 {round(time.time() - t0)}s", flush=True)

# ---------- 日收益矩阵 ----------
rets = {s: b.set_index("trade_date")["pct_chg"] / 100.0 for s, b in books.items()}


# ---------- 组合模拟 ----------
def simulate(mode="full"):
    """mode: full=卖点+止损+换股 | norotate=卖点+止损、不满仓换股 | buyhold=初始Top10拿到底"""
    cash, pos = 1.0, {}  # pos: stock -> {"val","cost","entry_day","score"}
    equity, trades, rot = [], [], 0
    pending_buy, pending_sell = [], []  # D 日收盘确认 → D+1 收盘执行
    for di, day in enumerate(cal):
        frozen = mode == "buyhold" and (len(pos) >= MAX_POS or di > 10)
        # 1) 当日持仓承担今日收益
        for s in pos:
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                pos[s]["val"] *= 1 + r
        if frozen:
            equity.append(
                {
                    "day": day,
                    "equity": cash + sum(p["val"] for p in pos.values()),
                    "n_pos": len(pos),
                }
            )
            continue
        # 2) 执行昨日收盘排队的单（以今日收盘价成交）
        for s in pending_sell:
            if s in pos:
                cash += pos[s]["val"] * (1 - COST)
                trades.append(
                    {
                        "stock": s,
                        "exit_day": day,
                        "pnl": pos[s]["val"] / pos[s]["cost"] - 1,
                        "entry_day": pos[s]["entry_day"],
                        "how": pos[s].get("how", "signal"),
                    }
                )
                del pos[s]
        for o in pending_buy:
            if len(pos) >= MAX_POS or o["stock"] in pos:
                continue
            spend = min(
                cash,
                (cash + sum(p["val"] for p in pos.values()))
                / max(1, (MAX_POS - len(pos))),
            )
            if spend <= 0:
                continue
            cash -= spend
            pos[o["stock"]] = {
                "val": spend * (1 - COST),
                "cost": spend * (1 - COST),
                "entry_day": day,
                "score": o.get("score"),
                "how": o.get("how", "entry"),
            }
        pending_buy, pending_sell = [], []
        # 3) 今日收盘观察：止损/卖点 → 明日卖
        for s, p in list(pos.items()):
            dd = p["val"] / p["cost"] - 1
            if any(ss == s for ss, _ in new_by_day[day]["sell"]):
                pending_sell.append(s)
                pos[s]["how"] = "sell_signal"
            elif dd <= STOP_DD:
                pending_sell.append(s)
                pos[s]["how"] = "stop"
        # 4) 买点 → 打分排序 → 空位买入 / 满仓换股（仅 full 模式）
        cands = []
        for o in new_by_day[day]["buy"]:
            if o["mom20"] is not None and o["amt20"] >= 1e5:  # 日均成交 ≥1 亿元
                cands.append(o)
        if cands and not pending_sell:  # 有同票待卖时不重复排
            for s in pending_sell:
                cands = [c for c in cands if c["stock"] != s]
        for c in cands[:MAX_POS]:
            if (
                len(pos) < MAX_POS
                and all(c["stock"] != pp for pp in pos)
                and c["stock"] not in [x["stock"] for x in pending_buy]
            ):
                pending_buy.append({**c, "how": "entry"})
            elif mode == "full" and len(pos) + len(pending_buy) >= MAX_POS and pos:
                worst = min(
                    pos,
                    key=lambda k: (
                        pos[k]["score"] if pos[k]["score"] is not None else -9
                    ),
                )
                wsc = pos[worst]["score"] or -9
                if c["mom20"] > wsc * (1 + ROTATE_MARGIN) and worst not in pending_sell:
                    pending_sell.append(worst)
                    pos[worst]["how"] = "rotate_out"
                    rot += 1
                    pending_buy.append({**c, "how": "rotate_in"})
        equity.append(
            {
                "day": day,
                "equity": cash + sum(p["val"] for p in pos.values()),
                "n_pos": len(pos),
            }
        )
    return equity, trades, rot


def stats(equity, trades, rot, label):
    eq = pd.Series({e["day"]: e["equity"] for e in equity})
    total = eq.iloc[-1] - 1
    n = len(eq)
    ann = (eq.iloc[-1]) ** (245 / n) - 1
    peak = eq.cummax()
    mdd = ((eq / peak) - 1).min()
    wins = [t for t in trades if t["pnl"] > 0]
    hold = []
    for t in trades:
        try:
            hold.append(cal.index(t["exit_day"]) - cal.index(t["entry_day"]))
        except ValueError:
            pass
    r = {
        "label": label,
        "total_return_pct": round(total * 100, 2),
        "annualized_pct": round(ann * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "n_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_holding_days": round(sum(hold) / len(hold), 1) if hold else None,
        "n_rotations": rot,
    }
    print(label, json.dumps(r, ensure_ascii=False), flush=True)
    return r


print("模拟：A 轮动组合…", flush=True)
eqR, trR, rotR = simulate("full")
stR = stats(eqR, trR, rotR, "A_轮动(卖点+止损+换股)")

print("模拟：B 风控出场不满仓换…", flush=True)
eqB, trB, rotB = simulate("norotate")
stB = stats(eqB, trB, rotB, "B_风控出场不换股(卖点+止损,空位才补)")

print("模拟：C 真·买入持有…", flush=True)
eqC, trC, rotC = simulate("buyhold")
stC = stats(eqC, trC, rotC, "C_买入持有不动(初始Top10拿到底)")

# D 宇宙基线：76 只全池等权买入持有（无成本）
eqD = 1.0
for day in cal:
    rs = [rets[s].get(day) for s in books]
    rs = [r for r in rs if r is not None and not pd.isna(r)]
    eqD *= 1 + sum(rs) / len(rs)
stD = {
    "label": "D_宇宙等权全持有(76只无成本)",
    "total_return_pct": round((eqD - 1) * 100, 2),
    "annualized_pct": round((eqD ** (245 / len(cal)) - 1) * 100, 2),
}
print("D_宇宙等权:", json.dumps(stD, ensure_ascii=False), flush=True)

# 十档节点对比
H = (5, 10, 20, 30, 40, 50, 60, 120, 180, 360)
nodes = []
for h in H:
    if h - 1 < len(cal):
        nodes.append(
            {
                "h": h,
                "day": cal[h - 1],
                "A_rotate_pct": round((eqR[h - 1]["equity"] - 1) * 100, 2),
                "B_norotate_pct": round((eqB[h - 1]["equity"] - 1) * 100, 2),
                "C_buyhold_pct": round((eqC[h - 1]["equity"] - 1) * 100, 2),
            }
        )
print("十档节点:", json.dumps(nodes, ensure_ascii=False), flush=True)

# 月度收益（轮动）
eqs = pd.Series({e["day"]: e["equity"] for e in eqR})
monthly = {}
for m, g in eqs.groupby(eqs.index.str[:6]):
    monthly[m] = round(
        (
            g.iloc[-1]
            / (
                g.iloc[0]
                if g.index[0] == cal[0]
                else eqs[eqs.index < g.index[0]].iloc[-1]
            )
            - 1
        )
        * 100,
        2,
    )

out = {
    "meta": {
        "D0": D0,
        "universe": "chanlun_pool_snapshot_20261012(76只,宇宙前视已声明)",
        "exec": "信号D收盘确认→D+1收盘成交",
        "cost_bps": 15,
        "max_pos": MAX_POS,
        "stop_dd": STOP_DD,
        "rotate_margin": ROTATE_MARGIN,
        "score": "SCORE_V1=mom20降序+amt20≥1亿",
    },
    "A_rotate": stR,
    "B_norotate": stB,
    "C_buyhold": stC,
    "D_universe_ew": stD,
    "horizon_nodes": nodes,
    "monthly_rotate": monthly,
    "trades_rotate": trR,
}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
