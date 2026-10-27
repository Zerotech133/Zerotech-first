# -*- coding: utf-8 -*-
"""
K-STOCK-267 探针5：等权池 + 周期再平衡 × regime 调仓（含成本快速扫描）
回答：正常仓位管理的正确形态 = 组合级再平衡频率 + 环境总仓位调节。
口径：76 池等权；再平衡时权重拉回等权；换手按 15bp 单边计费；
regime（池等权指数 MA20/60，D-1 判定）只在 80/50/30 间调部署率，永不禁开仓。
"""

import os
import json
import pandas as pd
import numpy as np

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")

D0 = "20231010"
COST = 0.0015
CAP_HIGH, CAP_MID, CAP_LOW = 0.80, 0.50, 0.30

pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
rets = {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0

cal_all = sorted(set().union(*[set(r.index) for r in rets.values()]))
cal = [d for d in cal_all if d >= D0]
R = pd.DataFrame({s: rets[s] for s in rets}).reindex(cal)  # days × stocks, NaN=无数据
avail = R.notna()  # 当日可交易

# 池等权指数（每日等权，用于 regime）
idx_ret = R.mean(axis=1, skipna=True)
idx_nav = (1 + idx_ret).cumprod()
ma20 = idx_nav.rolling(20).mean()
ma60 = idx_nav.rolling(60).mean()
caps = []
for i in range(len(cal)):
    if i == 0 or pd.isna(ma60.iloc[i - 1]):
        caps.append(CAP_MID)
        continue
    v, m20, m60 = idx_nav.iloc[i - 1], ma20.iloc[i - 1], ma60.iloc[i - 1]
    caps.append(CAP_HIGH if v >= m20 else (CAP_MID if v >= m60 else CAP_LOW))


def run(reb_n, use_regime):
    nav = 1.0
    w = np.zeros(R.shape[1])  # 当前持仓权重（相对 nav）
    eq_curve, turn_total = [], 0.0
    n_reb = 0
    for i, day in enumerate(cal):
        r = R.iloc[i].values.copy()
        r[np.isnan(r)] = 0.0
        rp = float(np.dot(w, r))
        nav *= 1 + rp
        w = w * (1 + r) / (1 + rp) if (1 + rp) != 0 else w * 0
        need_reb = reb_n > 0 and i % reb_n == 0
        d = caps[i] if use_regime else CAP_HIGH
        # 新上市股票纳入目标权重
        ok = avail.iloc[i].values
        # 目标：可交易股票等权 × 部署率
        w_t = np.zeros_like(w)
        m = ok.sum()
        if m > 0:
            w_t[ok] = d / m
        if need_reb or abs(d - w.sum()) > 0.02:  # 到期再平衡 或 部署率变动超 2pp
            turn = float(np.abs(w_t - w).sum())
            nav *= 1 - turn * COST
            turn_total += turn
            w = w_t
            n_reb += 1
        eq_curve.append(nav)
    eq = pd.Series(eq_curve, index=cal)
    total = (eq.iloc[-1] - 1) * 100
    ann = (eq.iloc[-1] ** (245 / len(eq)) - 1) * 100
    mdd = ((eq / eq.cummax()) - 1).min() * 100
    return {
        "reb": f"{reb_n}d",
        "regime": use_regime,
        "total_pct": round(total, 2),
        "ann_pct": round(ann, 2),
        "mdd_pct": round(mdd, 2),
        "calmar": round(ann / abs(mdd), 2) if mdd else None,
        "turnover_x": round(turn_total, 1),
        "n_rebal": n_reb,
        "est_cost_drag_pct": round(turn_total * COST * 100, 2),
    }


rows = []
for reb_n in (1, 5, 10, 20, 60, 0):  # 0=只调regime不再平衡
    for rg in (True, False):
        rows.append(run(reb_n, rg))
print(
    f"{'频率':>6} {'regime':>7} {'总收益%':>9} {'年化%':>8} {'最大回撤%':>9} {'Calmar':>7} {'换手(倍)':>9} {'成本拖累%':>9}"
)
for x in rows:
    print(
        f"{x['reb']:>6} {str(x['regime']):>7} {x['total_pct']:>9} {x['ann_pct']:>8} {x['mdd_pct']:>9} {str(x['calmar']):>7} {x['turnover_x']:>9} {x['est_cost_drag_pct']:>9}"
    )
json.dump(
    rows,
    open(
        os.path.join(BASE, "quant_backtest", "_probe5_ew_rebalance.json"),
        "w",
        encoding="utf-8",
    ),
    ensure_ascii=False,
    indent=1,
)
print("JSON: _probe5_ew_rebalance.json")
