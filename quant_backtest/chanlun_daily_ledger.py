# -*- coding: utf-8 -*-
"""
K-STOCK-277 · 逐日回测账本生成器（回测中心数据管线）
老田 10-13 07:05 指令：从选股日起每个交易日形成日历、看到每天盈利、AI 每天是否买卖平仓、
展示借用智策 OS v2（BacktestPage KPI卡+收益曲线 / PnlCalendarPage 月历热力图）。

方案 = K-STOCK-269 终案：
  组合层：池等权永在场 + 每 10 交易日再平衡（含 15bp 成本）+ regime 连续调仓 80/50/30（D-1 判定）
  个股层：无硬止损；缠论卖点影子化（只记录不执行）
  信号层：缠论买卖点 seen-ever 口径逐日记录（hfq K线）

输出：data/daily_ledger/ledger_<D0>_<D1>.json
  meta / summary KPI / equity_curve / days{day:{pnl_pct, n_pos, wins, losses, cap, actions[]}}
  holdings{day:[{stock,pnl_pct,wgt_pct}]} / trades / monthly
"""

import sys
import os
import json
import time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT_DIR = os.path.join(BASE, "quant_backtest", "data", "daily_ledger")
D0_WIN, D1_WIN = "20251012", "20261012"
WARMUP = 250
COST = 0.0015
REBAL_EVERY = 10
CAP_HIGH, CAP_MID, CAP_LOW = 0.80, 0.50, 0.30
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}

sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

os.makedirs(OUT_DIR, exist_ok=True)

# ---------- 数据装载 + hfq（263a 铁律） ----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, rets, tiers = {}, {}, {}
for x in pool:
    tiers[x["stock"]] = x.get("_tier") or "-"
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
    rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0

cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal_idx_all = [d for d in cal_all if d >= "20231010"]  # 池指数预热段（MA60 需要）
cal_win = [d for d in cal_idx_all if D0_WIN <= d <= D1_WIN]
row_idx = {s: b.set_index("trade_date") for s, b in books.items()}
print(
    f"池 {len(books)} 只 · 窗口 {cal_win[0]}→{cal_win[-1]} 共 {len(cal_win)} 交易日",
    flush=True,
)

# ---------- 池等权指数 + regime（全历史算 MA，窗口内判定用 D-1） ----------
idx_ret = {}
for day in cal_idx_all:
    rs = [rets[s].get(day) for s in books]
    rs = [x for x in rs if x is not None and not pd.isna(x)]
    idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
ma20 = idx_nav.rolling(20).mean()
ma60 = idx_nav.rolling(60).mean()


def regime_cap(day):
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return CAP_MID, "mid", "首日默认"
    v, m20v, m60v = idx_nav.iloc[i - 1], ma20.iloc[i - 1], ma60.iloc[i - 1]
    if pd.isna(m60v):
        return CAP_MID, "mid", "MA60 未预热"
    if v >= m20v:
        return CAP_HIGH, "high", f"池指数≥MA20({m20v:.3f})"
    if v >= m60v:
        return CAP_MID, "mid", "MA60≤池指数<MA20"
    return CAP_LOW, "low", f"池指数<MA60({m60v:.3f})"


# ---------- 信号流（hfq + seen-ever，窗口起点前一天初始化） ----------
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


t0 = time.time()
prev_init_day = [d for d in cal_idx_all if d < D0_WIN][-1]
prev = run_day(prev_init_day)
seen_ever = set(prev)
sig_buy, sig_sell = {}, {}
for day in cal_win:
    cur = run_day(day)
    fresh = [k for k in cur if k not in seen_ever]
    seen_ever |= set(cur)
    prev = cur
    sig_buy[day] = {s for (s, tp) in fresh if tp in BUY_TYPES}
    sig_sell[day] = {s for (s, tp) in fresh if tp in SELL_TYPES}
    if len(sig_buy) % 60 == 0:
        print(
            f"  …信号流 {len(sig_buy)}/{len(cal_win)}（{round(time.time() - t0)}s）",
            flush=True,
        )
print(f"信号流 {round(time.time() - t0)}s", flush=True)

# ---------- 269 终案模拟 + 逐日账本 ----------
cash, pos = 1.0, {}
days, equity_curve, trades, holdings = {}, [], [], {}
last_cap = None
prev_eqv = 1.0


def deploy_target(eqv, cap):
    return eqv * cap


for di, day in enumerate(cal_win):
    actions = []
    # 1) 持仓按日收益更新
    for s in pos:
        r = rets[s].get(day)
        if r is not None and not pd.isna(r):
            pos[s]["val"] *= 1 + r
    eqv = cash + sum(p["val"] for p in pos.values())
    # 2) 影子信号（缠论 buy/sell，只记录）
    for s in sorted(sig_buy.get(day, set())):
        actions.append(
            {"type": "sig_buy", "stock": s, "note": "缠论买点确认（影子·不执行）"}
        )
    for s in sorted(sig_sell.get(day, set())):
        actions.append(
            {
                "type": "sig_sell",
                "stock": s,
                "note": "缠论卖点信号（影子·不执行·269实测负edge）",
            }
        )
    # 3) regime 判定（D-1 数据）→ 档位变化即调仓
    cap, regime_lv, regime_why = regime_cap(day)
    if last_cap is None:
        actions.append(
            {
                "type": "init",
                "note": f"选股日建仓：池 {len(books)} 只等权，regime={regime_lv}（{regime_why}），目标仓位 {cap:.0%}",
            }
        )
    elif cap > last_cap:
        actions.append(
            {
                "type": "regime_up",
                "note": f"仓位档 {last_cap:.0%}→{cap:.0%}（{regime_why}）→ 回补持仓",
            }
        )
    elif cap < last_cap:
        actions.append(
            {
                "type": "regime_down",
                "note": f"仓位档 {last_cap:.0%}→{cap:.0%}（{regime_why}）→ 等比例减仓",
            }
        )
    last_cap = cap
    # 4) 再平衡日：调到等权 × cap（含 regime 调整，一次完成）
    is_rebal = di % REBAL_EVERY == 0
    deployed = eqv - cash
    target = deploy_target(eqv, cap)
    n_sell = n_buy = 0
    legs = []  # 逐笔换手明细（277 补）
    if deployed > target * 1.005:  # 超配 → 等比例减
        ratio = 1 - target / deployed
        for s in list(pos):
            p = pos[s]
            cut = p["val"] * ratio
            cash += cut * (1 - COST)
            p["val"] -= cut
            p["cost"] *= 1 - ratio
            n_sell += 1
            legs.append(
                {
                    "stock": s,
                    "side": "SELL",
                    "how": "regime_trim",
                    "amt_pct": round(cut / max(eqv, 1e-9) * 100, 2),
                }
            )
            trades.append(
                {
                    "day": day,
                    "stock": s,
                    "side": "SELL",
                    "how": "regime_trim",
                    "note": f"regime 减仓 {ratio:.0%}",
                    "pnl": None,
                }
            )
        actions.append(
            {
                "type": "trim",
                "note": f"减仓至 {cap:.0%}：卖出 {n_sell} 只持仓的 {ratio:.0%}",
            }
        )
    if is_rebal or (di == 0):
        # 等权再平衡：按持仓市值排序，卖超配买低配到等权（在 target 预算内）
        if pos:
            tw = sum(p["val"] for p in pos.values())
            eq_slot = target / len(pos)
            # 卖出超配
            for s in sorted(pos, key=lambda x: pos[x]["val"] / tw, reverse=True):
                p = pos[s]
                if p["val"] > eq_slot * 1.15:
                    cut = p["val"] - eq_slot
                    cash += cut * (1 - COST)
                    p["val"] -= cut
                    p["cost"] *= p["val"] / (p["val"] + cut)
                    n_sell += 1
                    legs.append(
                        {
                            "stock": s,
                            "side": "SELL",
                            "how": "rebal_sell",
                            "amt_pct": round(cut / max(eqv, 1e-9) * 100, 2),
                        }
                    )
            # 买入低配
            cash_avail = min(
                cash - eqv * 0.005, sum(max(0.0, eq_slot - pos[s]["val"]) for s in pos)
            )
            need_total = sum(max(0.0, eq_slot - pos[s]["val"]) for s in pos)
            if need_total > 0.001 and cash_avail > 0.001:
                scale = min(1.0, cash_avail / need_total)
                for s in sorted(pos, key=lambda x: pos[x]["val"] / tw):
                    p = pos[s]
                    gap = eq_slot - p["val"]
                    if gap > 0.0005:
                        amt = gap * scale
                        if amt > 0.0005 and cash >= amt:
                            cash -= amt
                            p["val"] += amt * (1 - COST)
                            p["cost"] += amt * (1 - COST)
                            n_buy += 1
                            legs.append(
                                {
                                    "stock": s,
                                    "side": "BUY",
                                    "how": "rebal_buy",
                                    "amt_pct": round(amt / max(eqv, 1e-9) * 100, 2),
                                }
                            )
        note = (
            f"10 日再平衡：卖出超配 {n_sell} 笔 / 买入低配 {n_buy} 笔 → 等权×{cap:.0%}"
        )
        act = {"type": "rebalance", "note": note}
        if legs:
            act["legs"] = legs
            for lg in legs:
                if lg["how"] != "regime_trim":  # trim 已在上方逐笔入账
                    trades.append(
                        {
                            "day": day,
                            "stock": lg["stock"],
                            "side": lg["side"],
                            "how": lg["how"],
                            "note": f"换手 {lg['amt_pct']}%",
                            "pnl": None,
                        }
                    )
        actions.append(act)
    # 5) 新股入场（池内未进场且有数据——窗口中途从未有，保持等权完整性）
    want_in = {s for s in books if s not in pos and day in row_idx[s].index}
    n_entry = 0
    for s in sorted(want_in):
        amt = min(cash - eqv * 0.003, deploy_target(eqv, cap) / len(books))
        if amt > 0.001 and cash >= amt:
            cash -= amt
            pos[s] = {
                "val": amt * (1 - COST),
                "cost": amt * (1 - COST),
                "entry_day": day,
            }
            trades.append(
                {
                    "day": day,
                    "stock": s,
                    "side": "BUY",
                    "how": "init_entry",
                    "note": "建仓",
                    "pnl": None,
                }
            )
            n_entry += 1
    if n_entry and di > 0:
        actions.append(
            {
                "type": "entry",
                "stock": f"{n_entry}只",
                "note": f"中途入场 {n_entry} 只（池内补齐）",
            }
        )
    elif n_entry:
        actions.append(
            {
                "type": "entry",
                "stock": f"{n_entry}只",
                "note": f"初始建仓：{n_entry} 只等权入场（明细见持仓表）",
            }
        )
    # 6) 当日账本记录
    eqv = cash + sum(p["val"] for p in pos.values())
    pnl_pct = eqv / prev_eqv - 1 if prev_eqv else 0.0
    if di == 0:
        pnl_pct = 0.0  # 建仓日不计收益
    prev_eqv = eqv
    pos_rows, wins, losses = [], 0, 0
    tw = sum(p["val"] for p in pos.values()) or 1.0
    for s, p in pos.items():
        pp = p["val"] / p["cost"] - 1
        if pp >= 0:
            wins += 1
        else:
            losses += 1
        pos_rows.append(
            {
                "stock": s,
                "tier": tiers.get(s, "-"),
                "pnl_pct": round(pp * 100, 2),
                "wgt_pct": round(p["val"] / tw * 100, 1),
            }
        )
    pos_rows.sort(key=lambda x: x["pnl_pct"])
    days[day] = {
        "pnl_pct": round(pnl_pct * 100, 3),
        "equity": round(eqv, 6),
        "n_pos": len(pos),
        "wins": wins,
        "losses": losses,
        "cap": cap,
        "regime": regime_lv,
        "actions": actions,
    }
    holdings[day] = pos_rows
    equity_curve.append(
        {
            "day": day,
            "nav": round(eqv, 4),
            "inv_pct": round((eqv - cash) / eqv * 100, 1),
        }
    )

# ---------- KPI ----------
eq = pd.Series({d: days[d]["equity"] for d in cal_win})
total = eq.iloc[-1] - 1
n_days = len(eq)
ann = eq.iloc[-1] ** (245 / n_days) - 1
mdd = ((eq / eq.cummax()) - 1).min()
win_days = sum(1 for d in cal_win if days[d]["pnl_pct"] > 0)
loss_days = sum(1 for d in cal_win if days[d]["pnl_pct"] < 0)
# 基准：池等权买入持有（窗口内）
dr = pd.Series(
    {
        d: sum(
            x
            for x in [rets[s].get(d) for s in books]
            if x is not None and not pd.isna(x)
        )
        / max(1, len(books))
        for d in cal_win
    }
)
nav_bh = (1 + dr).cumprod()
bh_total = nav_bh.iloc[-1] - 1
bh_mdd = ((nav_bh / nav_bh.cummax()) - 1).min()

monthly = {}
for d in cal_win:
    ym = d[:6]
    m = monthly.setdefault(
        ym, {"pnl_pct": 0.0, "win_days": 0, "loss_days": 0, "days": 0}
    )
    m["days"] += 1
    if days[d]["pnl_pct"] > 0:
        m["win_days"] += 1
    elif days[d]["pnl_pct"] < 0:
        m["loss_days"] += 1
# 月收益用 nav 复利
navs = {d: days[d]["equity"] for d in cal_win}
yms = sorted(monthly)
for k, ym in enumerate(yms):
    d_in = [d for d in cal_win if d[:6] == ym]
    prev_nav = navs[d_in[0]] / (1 + days[d_in[0]]["pnl_pct"] / 100) if k > 0 else 1.0
    monthly[ym]["pnl_pct"] = round((navs[d_in[-1]] / prev_nav - 1) * 100, 2)

summary = {
    "total_return_pct": round(total * 100, 2),
    "annualized_pct": round(ann * 100, 2),
    "max_drawdown_pct": round(mdd * 100, 2),
    "calmar": round(ann / abs(mdd), 2) if mdd else None,
    "n_days": n_days,
    "win_days": win_days,
    "loss_days": loss_days,
    "day_win_rate_pct": round(win_days / max(1, win_days + loss_days) * 100, 1),
    "avg_invested_pct": round(sum(e["inv_pct"] for e in equity_curve) / n_days, 1),
    "bench_total_pct": round(bh_total * 100, 2),
    "bench_mdd_pct": round(bh_mdd * 100, 2),
    "excess_pct": round((total - bh_total) * 100, 2),
}
out = {
    "meta": {
        "D0": cal_win[0],
        "D1": cal_win[-1],
        "pool_size": len(books),
        "design": "K-STOCK-269 终案：池等权永在场+10日再平衡+regime 80/50/30(D-1)+缠论信号影子化；无硬止损；成本15bp",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "price_basis": "hfq(pct_chg因子链,K-STOCK-263a)",
        # v8 文案治理：本账本 actions 里的「建仓/减仓/卖出」是**模拟再平衡动作**，
        # 不是对该标的的操作建议；缠论买卖点在此仅影子记录（design 已述"影子化"）。
        # 不逐条改写动作名：那是回测事实，改了会与历史账本口径断裂。
        "disclaimer": "纸面回测账本：建仓/减仓/卖出均为模拟再平衡动作，非投资建议；"
                      "缠论信号仅影子记录、不驱动交易。",
    },
    "summary": summary,
    "equity_curve": equity_curve,
    "days": days,
    "holdings": holdings,
    "trades": trades,
    "monthly": monthly,
    "bench_nav": {d: round(v, 4) for d, v in nav_bh.items()},
}
OUT = os.path.join(OUT_DIR, f"ledger_{cal_win[0]}_{cal_win[-1]}.json")
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print("KPI:", json.dumps(summary, ensure_ascii=False), flush=True)
print("月度:", json.dumps(monthly, ensure_ascii=False), flush=True)
print("JSON:", OUT, f"({os.path.getsize(OUT) // 1024}KB)", flush=True)
