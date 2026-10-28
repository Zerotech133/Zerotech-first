# -*- coding: utf-8 -*-
"""
K-STOCK-267b · v6b 学院方案对齐重构(部署修复)（量子双刀框架 → 可回测规则）
老田 2026-10-13 02:58 指令：①为什么选出来了还有门槛禁止开仓？②平均仓位管理没做对；
③研究学院所有选股/止盈止损方案，出新的解决方案；④必须一年以上回测。

【学院依据】work/academy-source-evidence-v0.1/d-drive-quantum-double-blade-study-2026-10-12.md
  ④评分和仓位层：热度档位→总仓位映射（连续调节，非二值封锁）；周期仓位 5:3:2
  ⑤选标的和持有层：开仓时同时确定 持有周期 + 止盈 + 止损
  ⑥买卖验证层：买点=中枢区间+成交量触发（在支撑区买，不在追高后买）
  ⑦方法论约束：回测时间范围扩大（本版 3 年），样本内/外与归因如实报告

【v6 四条重构】
  R1 限价回踩入场（替代确认次收追高）：限价=信号日收盘价（中枢区），确认后 5 个交易日内
     触价成交（low<=限价，成交价=min(open,限价)），未成交作废——机制性消灭 runup 毒性；
  R2 仓位 5:3:2 分批：首仓 50% 槽位，持仓期再现同股新买点加 30%，浮盈 +5% 趋势确认加 20%；
  R3 开仓即定三出口（学院⑤）：结构止损=入场价 -8%；分批止盈=+15% 减半、余仓高点回撤 8%
     移动止盈；卖点信号(1卖/2卖/3卖)全出；最长持有 120 交易日强制复核离场；
  R4 市场环境调总仓位（学院④，永不禁开仓）：池等权指数 MA20 上=80% / MA20下MA60上=50% /
     MA60下=30%——只是把可用额度从 80→50→30 连续调节，选出的票按计划照常进场。
数据口径：hfq(263a) + seen-ever(263b)；对照 v5-B5(-7.83%/-10.98%) 与买入持有。
⚠️ 如实声明：池为 2026-10-12 快照，2023-2024 段存在宇宙前视（待按日快照池重刷）。
"""

import sys
import os
import json
import time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "chanlun_rotation_v6b_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402
import logging

D0 = "20231010"  # 3 年回测（数据自 2023-04，预热 6 个月）
WARMUP = 250
MAX_POS = 10
COST = 0.0015
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}
# R1 限价入场
ENTRY_WINDOW = 10  # v6b: 5→10 日
LIMIT_BUF = 1.03  # v6b: 限价=信号价×1.03（缓冲）
STALE_GUARD = 1.10  # v6b: 挂单时现价>限价×1.10 → 信号过期不追
SLOT_FRAC = 0.15  # v6b: 每标的全槽 = equity×cap×15%
# R2 5:3:2
T1, T2, T3 = 0.5, 0.3, 0.2
# R3 止盈止损
STOP_PCT = -0.08  # 结构止损
TP1_PCT = 0.15  # 分批止盈触发
TRAIL_PCT = 0.08  # 余仓移动止盈（高点回撤）
MAX_HOLD = 120  # 交易日
# R4 regime 调总仓位
CAP_HIGH, CAP_MID, CAP_LOW = 0.80, 0.50, 0.30
AMT_MIN = 1e5  # 流动性门槛（沿用）

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
print(f"池 {len(stocks)} 只，可回测 {len(books)} 只", flush=True)

cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal_all if d >= D0]
print(f"交易日 {len(cal)} 天：{cal[0]} -> {cal[-1]}", flush=True)

# ---------- 池等权指数（R4 regime 基准）----------
idx_ret = {}
for day in cal:
    rs = [rets[s].get(day) for s in books]
    rs = [x for x in rs if x is not None and not pd.isna(x)]
    idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
idx = pd.Series(idx_ret).sort_index()
idx_nav = (1 + idx).cumprod()
ma20 = idx_nav.rolling(20).mean()
ma60 = idx_nav.rolling(60).mean()


def regime_cap(day):
    # PIT：用 D-1 收盘的指数状态定 D 日额度（限价单盘中成交，不能用当日收盘）
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return CAP_MID
    v = idx_nav.iloc[i - 1]
    m20, m60 = ma20.iloc[i - 1], ma60.iloc[i - 1]
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
        bars = [
            {
                "dt": int(r.trade_date),
                "o": float(r.adj_open),
                "h": float(r.adj_high),
                "l": float(r.adj_low),
                "c": float(r.adj_close),
            }
            for r in seg.tail(WARMUP).itertuples()
        ]
        for sg in (chan_analyze(bars) or {}).get("signals") or []:
            out[(s, sg["type"])] = int(sg["dt"])
    return out


def amt20(day, s):
    b = books[s]
    seg = b[b.trade_date <= day]
    if len(seg) < 20:
        return None
    return float(seg["amount"].tail(20).mean())


t0 = time.time()
prev = run_day("20231009")
seen_ever = set(prev)
# 事件流：{确认日: [(stock, type, sig_dt, sig_price)]}
events_by_day = {}
n_fresh = 0
for day in cal:
    cur = run_day(day)
    fresh = [k for k in cur if k not in seen_ever]
    seen_ever |= set(cur)
    prev = cur
    evs = []
    for s, tp in fresh:
        dt = cur[(s, tp)]
        p_sig = adj_close[s].get(str(dt))
        if p_sig is None or pd.isna(p_sig):
            continue
        evs.append((s, tp, dt, float(p_sig)))
        n_fresh += 1
    events_by_day[day] = evs
    if (day := day) and len(events_by_day) % 120 == 0:
        print(
            f"  …{len(events_by_day)}/{len(cal)}（{round(time.time() - t0)}s）",
            flush=True,
        )
print(f"信号流 {round(time.time() - t0)}s；fresh 事件 {n_fresh} 个", flush=True)


# ---------- 组合模拟 v6 ----------
def simulate(use_regime=True):
    cash, pos = 1.0, {}
    equity, trades, inv_hist = [], [], []
    # 待成交限价单：{stock: {limit, expire_di, tp, sig_dt}}
    pend_orders = {}
    nav_peak = {}  # stock -> 持仓期 adj 收盘高点（移动止盈）
    for di, day in enumerate(cal):
        # 1) 持仓逐日收益（pct_chg 官方复权）
        for s in pos:
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                for t in pos[s]["tr"]:
                    t["val"] *= 1 + r
        eqv = cash + sum(t["val"] for p in pos.values() for t in p["tr"])
        # 2) R3 出场检查（用 adj 收盘）
        for s in list(pos):
            p = pos[s]
            ac = adj_close[s].get(day)
            if ac is not None and not pd.isna(ac):
                nav_peak[s] = max(nav_peak.get(s, ac), float(ac))
            val = sum(t["val"] for t in p["tr"])
            cost = sum(t["cost"] for t in p["tr"])
            price_ret = val / cost - 1 if cost else 0
            exit_how = None
            if any(
                e[0] == s and e[1] in SELL_TYPES for e in events_by_day.get(day, [])
            ):
                exit_how = "sell_signal"
            elif price_ret <= STOP_PCT:
                exit_how = "stop"
            elif p["tp1_done"] is False and price_ret >= TP1_PCT:
                # 分批止盈：减半（按市值比例处理各 tranche）
                half = val / 2
                cash += half * (1 - COST)
                kept = val - half
                scale = kept / val
                for t in p["tr"]:
                    t["val"] *= scale
                    t["cost"] *= scale
                trades.append(
                    {
                        "stock": s,
                        "exit_day": day,
                        "pnl": TP1_PCT,
                        "entry_day": p["entry_day"],
                        "how": "tp1_half",
                    }
                )
                p["tp1_done"] = True
            elif (
                p["tp1_done"]
                and nav_peak[s] > 0
                and float(ac or 0) <= nav_peak[s] * (1 - TRAIL_PCT)
            ):
                exit_how = "trail"
            elif di - cal.index(p["entry_day"]) >= MAX_HOLD:
                exit_how = "max_hold"
            if exit_how:
                cash += val * (1 - COST)
                trades.append(
                    {
                        "stock": s,
                        "exit_day": day,
                        "pnl": price_ret,
                        "entry_day": p["entry_day"],
                        "how": exit_how,
                    }
                )
                del pos[s]
                nav_peak.pop(s, None)
        eqv = cash + sum(t["val"] for p in pos.values() for t in p["tr"])
        cap = regime_cap(day) if use_regime else CAP_HIGH
        deployed = eqv - cash
        # 3) R2 加仓（同股新买点 +30%；浮盈+5% 趋势确认 +20%）
        for s in list(pos):
            p = pos[s]
            slot0 = p["slot"]
            fresh_buy = [
                e for e in events_by_day.get(day, []) if e[0] == s and e[1] in BUY_TYPES
            ]
            if len(p["tr"]) == 1 and fresh_buy and p["adds"] == 0:
                amt = min(cash, slot0 * T2)
                if amt > 0.005 and deployed + amt <= eqv * cap:
                    cash -= amt
                    p["tr"].append({"val": amt * (1 - COST), "cost": amt * (1 - COST)})
                    p["adds"] = 1
                    deployed += amt
            elif p["adds"] == 1:
                val = sum(t["val"] for t in p["tr"])
                cost = sum(t["cost"] for t in p["tr"])
                if val / cost - 1 >= 0.05:
                    amt = min(cash, slot0 * T3)
                    if amt > 0.005 and deployed + amt <= eqv * cap:
                        cash -= amt
                        p["tr"].append(
                            {"val": amt * (1 - COST), "cost": amt * (1 - COST)}
                        )
                        p["adds"] = 2
                        deployed += amt
        # 4) R1 限价单成交检查
        for s in list(pend_orders):
            o = pend_orders[s]
            if di > o["expire_di"]:
                del pend_orders[s]
                continue
            if s in pos:
                del pend_orders[s]
                continue
            b = books[s]
            row = b[b.trade_date == day]
            if row.empty:
                continue
            lo, op = float(row["adj_low"].iloc[0]), float(row["adj_open"].iloc[0])
            if lo <= o["limit"]:
                fill_px = min(op, o["limit"])
                slot = eqv * cap * SLOT_FRAC
                amt = min(cash, slot * T1)
                if amt > 0.005 and deployed + amt <= eqv * cap:
                    cash -= amt
                    pos[s] = {
                        "tr": [{"val": amt * (1 - COST), "cost": amt * (1 - COST)}],
                        "entry_day": day,
                        "slot": slot,
                        "adds": 0,
                        "tp1_done": False,
                        "type": o["tp"],
                        "sig_dt": o["sig_dt"],
                    }
                    nav_peak[s] = fill_px
                    trades.append(
                        {
                            "stock": s,
                            "exit_day": None,
                            "entry_day": day,
                            "how": "open",
                            "pnl": None,
                        }
                    )
                    deployed += amt
                del pend_orders[s]
        # 5) 新信号 → 挂限价单（R1：限价=信号日收盘价）
        for s, tp, dt, p_sig in events_by_day.get(day, []):
            if tp in BUY_TYPES and s not in pos and s not in pend_orders:
                if amt20(day, s) is None or amt20(day, s) < AMT_MIN:
                    continue
                limit = p_sig * LIMIT_BUF
                p_now = adj_close[s].get(day)
                if (
                    p_now is not None
                    and not pd.isna(p_now)
                    and float(p_now) > limit * STALE_GUARD
                ):
                    continue  # v6b: 确认时已跑飞，不追
                pend_orders[s] = {
                    "limit": limit,
                    "expire_di": di + ENTRY_WINDOW,
                    "tp": tp,
                    "sig_dt": dt,
                }
        eqv = cash + sum(t["val"] for p in pos.values() for t in p["tr"])
        equity.append(
            {"day": day, "equity": eqv, "inv": (eqv - cash) / eqv if eqv else 0}
        )
        inv_hist.append((eqv - cash) / eqv if eqv else 0)
    return equity, trades, inv_hist


def stats(equity, trades, inv, label):
    eq = pd.Series({e["day"]: e["equity"] for e in equity})
    total = eq.iloc[-1] - 1
    ann = eq.iloc[-1] ** (245 / len(eq)) - 1
    mdd = ((eq / eq.cummax()) - 1).min()
    closed = [
        t for t in trades if t["exit_day"] is not None and t["how"] not in ("tp1_half",)
    ]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    hold = []
    for t in closed:
        try:
            hold.append(cal.index(t["exit_day"]) - cal.index(t["entry_day"]))
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_rotation_v6.py", exc)
    r = {
        "label": label,
        "total_return_pct": round(total * 100, 2),
        "annualized_pct": round(ann * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "calmar": round(ann / abs(mdd), 2) if mdd else None,
        "n_closed": len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "avg_holding_days": round(sum(hold) / len(hold), 1) if hold else None,
        "avg_invested_pct": round(sum(inv) / len(inv) * 100, 1),
    }
    hows = {}
    for t in closed:
        hows[t["how"]] = hows.get(t["how"], 0) + 1
    r["exit_mix"] = hows
    r["n_tp1"] = sum(1 for t in trades if t["how"] == "tp1_half")
    print(label, json.dumps(r, ensure_ascii=False), flush=True)
    return r


print("V6 主臂：学院方案全要素（regime 调仓位）…", flush=True)
eV, tV, iV = simulate(use_regime=True)
sV = stats(eV, tV, iV, "V6_学院方案")
print("V6 对照：无 regime（恒 80% 额度）…", flush=True)
eN, tN, iN = simulate(use_regime=False)
sN = stats(eN, tN, iN, "V6_无regime对照")

# 买入持有对照（首日等权全池）
dr = pd.Series(
    {
        d: sum(
            x
            for x in [rets[s].get(d) for s in books]
            if x is not None and not pd.isna(x)
        )
        / max(1, len(books))
        for d in cal
    }
)
nav_bh = (1 + dr).cumprod()
bh_total = (nav_bh.iloc[-1] - 1) * 100
bh_mdd = ((nav_bh / nav_bh.cummax()) - 1).min() * 100
print(f"对照_池等权买入持有: total={bh_total:.2f}% mdd={bh_mdd:.2f}%", flush=True)

out = {
    "meta": {
        "D0": D0,
        "exec": "R1限价回踩(5日有效,min(open,limit))",
        "cost_bps": 15,
        "tranche": [T1, T2, T3],
        "stop": STOP_PCT,
        "tp1": TP1_PCT,
        "trail": TRAIL_PCT,
        "max_hold": MAX_HOLD,
        "caps": [CAP_HIGH, CAP_MID, CAP_LOW],
        "kline": "hfq+seen-ever",
        "universe_caveat": "池=2026-10-12快照,2023-2025段宇宙前视",
    },
    "V6": sV,
    "V6_noregime": sN,
    "buyhold": {"total_pct": round(bh_total, 2), "mdd_pct": round(bh_mdd, 2)},
    "equity_V6": {e["day"]: round(e["equity"], 6) for e in eV},
}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
