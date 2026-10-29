# -*- coding: utf-8 -*-
"""双均线 walk-forward：普通寻优 vs 抗过拟合寻优（同折同窗公平对比）。

抗过拟合三件套：
1. 稳健目标：训练窗切 3 段取最差段夏普（防单段运气拉票）
2. 平台选择：得分 = 稳健分 + 0.3×邻居均值（选参数高原，拒绝孤峰）
3. 折数加密：train 252d / test 63d 滚动推进 → 样本外连续 ~504 交易日
纪律门：样本外夏普 ≥ 样本内(普通口径)均值 × 0.6。

扩展：
- vol_target：波动率目标仓位，仅作用于 OOS 执行层（寻优口径不变），如 0.15
- _chan_signal：缠论简化分型影子信号（无寻优），同一 OOS 折窗评估
"""

import itertools

import numpy as np
import pandas as pd

import metrics as mt
from tdx_read import read_range

TRAIN_N = 252
TEST_N = 63
COST_BPS = 3
BASE = {"fast": 5, "slow": 20}


def ma_signal(close, fast, slow):
    return (close.rolling(int(fast)).mean() > close.rolling(int(slow)).mean()).astype(
        int
    )


def _seg(close, sig, lo, hi, bench_close=None, vol_target=None):
    net, _, pos = mt.run_vec(
        close.iloc[lo:hi], sig.iloc[lo:hi], COST_BPS, vol_target=vol_target
    )
    bret = None
    if bench_close is not None:
        bret = bench_close.iloc[lo:hi].pct_change().fillna(0)
    return mt.evaluate(net, pos, bret)


def _robust(close, sig, lo, hi, k=3):
    """训练窗切 k 段取最差段夏普。"""
    seg = (hi - lo) // k
    ss = [
        _seg(close, sig, lo + j * seg, hi if j == k - 1 else lo + (j + 1) * seg)[
            "sharpe"
        ]
        for j in range(k)
    ]
    return min(ss)


def _neighbors(p, grid):
    out = []
    for k, vals in grid.items():
        i = vals.index(p[k])
        for d in (-1, 1):
            if 0 <= i + d < len(vals):
                q = dict(p)
                q[k] = vals[i + d]
                out.append(q)
    return out


def _pick(close, grid, lo, hi, mode):
    scored = {}
    for combo in itertools.product(*grid.values()):
        p = dict(zip(grid.keys(), combo))
        sig = ma_signal(close, **p)
        s = (
            _seg(close, sig, lo, hi)["sharpe"]
            if mode == "plain"
            else _robust(close, sig, lo, hi)
        )
        scored[tuple(sorted(p.items()))] = (s, p)
    if mode == "robust":
        scored = {
            k: (
                s
                + 0.3
                * (
                    sum(
                        _robust(close, ma_signal(close, **q), lo, hi)
                        for q in _neighbors(p, grid)
                    )
                    / max(len(_neighbors(p, grid)), 1)
                ),
                p,
            )
            for k, (s, p) in scored.items()
        }
    return max(scored.values(), key=lambda x: x[0])[1]


def walk_forward(close, grid, mode, bench_close=None, vol_target=None):
    n = len(close)
    folds, oos, base_oos, bhold = [], [], [], []
    base_sig = ma_signal(close, **BASE)
    lo = 0
    while lo + TRAIN_N + 40 <= n:
        tr_hi = lo + TRAIN_N
        te_hi = min(tr_hi + TEST_N, n)
        best = _pick(close, grid, lo, tr_hi, mode)
        sig = ma_signal(close, **best)
        tr_sh = _seg(close, sig, lo, tr_hi)["sharpe"]  # 普通口径样本内夏普
        mo = _seg(close, sig, tr_hi, te_hi, bench_close, vol_target)
        _seg(close, base_sig, tr_hi, te_hi, bench_close)
        net, _, pos = mt.run_vec(
            close.iloc[tr_hi:te_hi],
            sig.iloc[tr_hi:te_hi],
            COST_BPS,
            vol_target=vol_target,
        )
        bnet, _, bpos = mt.run_vec(
            close.iloc[tr_hi:te_hi], base_sig.iloc[tr_hi:te_hi], COST_BPS
        )
        folds.append(
            {
                "test": "%s~%s" % (close.index[tr_hi], close.index[te_hi - 1]),
                "best": best,
                "train_sharpe": round(tr_sh, 2),
                "oos_sharpe": mo["sharpe"],
                "oos_ret": mo["total_return"],
            }
        )
        oos.append((net, pos))
        base_oos.append((bnet, bpos))
        if bench_close is not None:
            bhold.append(bench_close.iloc[tr_hi:te_hi].pct_change().fillna(0))
        lo += TEST_N
    oos_m = mt.evaluate(
        pd.concat([x[0] for x in oos]),
        pd.concat([x[1] for x in oos]),
        pd.concat(bhold) if bhold else None,
    )
    base_m = mt.evaluate(
        pd.concat([x[0] for x in base_oos]),
        pd.concat([x[1] for x in base_oos]),
        pd.concat(bhold) if bhold else None,
    )
    return folds, oos_m, base_m


def _chan_signal(close, amp_min=0.005, trend_n=60):
    """缠论简化分型影子信号（过滤版）。

    口径声明：完整缠论（包含处理/笔/段/中枢）接口待核实后替换，此处用
    收盘价局部顶/底分型 + 两道噪声过滤做状态机影子：
      1. 幅度过滤：分型相对邻侧极值的突出幅度 ≥ amp_min（默认 0.5%），剔除毛刺
      2. 趋势过滤：底分型入场需 close > MA(trend_n)（顺势做多）；顶分型离场为风控，不限方向
    分型需次一根K线确认（用到 c[i+1]），于确认bar收盘置位决策，
    经 run_vec 的 shift(1) 次日生效，全程仅用历史信息防未来。
    amp_min=0 且 trend_n=0 退化为无过滤原始分型。
    """
    c = close.values
    ma = close.rolling(trend_n).mean().values if trend_n > 0 else None
    sig = pd.Series(1, index=close.index)  # 初始持有多头
    cur = 1
    for i in range(1, len(c) - 1):
        lo, hi = min(c[i - 1], c[i + 1]), max(c[i - 1], c[i + 1])
        if c[i] > hi and (c[i] - hi) / c[i] >= amp_min:
            cur = 0  # 顶分型确认 → 风控离场
        elif c[i] < lo and (lo - c[i]) / c[i] >= amp_min:
            if trend_n <= 0 or (not np.isnan(ma[i]) and c[i] > ma[i]):
                cur = 1  # 底分型确认 + 顺势 → 入场
        sig.iloc[i + 1] = cur
    return sig


def chan_oos_eval(close, bench_close=None, **kw):
    """缠论影子信号在同一 OOS 折窗的样本外评估（无寻优、无样本内）。"""
    sig = _chan_signal(close, **kw)
    n = len(close)
    nets, poss, bhold = [], [], []
    lo = 0
    while lo + TRAIN_N + 40 <= n:
        tr_hi = lo + TRAIN_N
        te_hi = min(tr_hi + TEST_N, n)
        net, _, pos = mt.run_vec(
            close.iloc[tr_hi:te_hi], sig.iloc[tr_hi:te_hi], COST_BPS
        )
        nets.append(net)
        poss.append(pos)
        if bench_close is not None:
            bhold.append(bench_close.iloc[tr_hi:te_hi].pct_change().fillna(0))
        lo += TEST_N
    return mt.evaluate(
        pd.concat(nets), pd.concat(poss), pd.concat(bhold) if bhold else None
    )


def load_300():
    try:
        df = read_range("ts_index_daily", "20230730", "20260927", codes=["000300.SH"])
    except TypeError:
        df = read_range("ts_index_daily", "20230730", "20260927")
        df = df[df["ts_code"] == "000300.SH"]
    df = df.sort_values("trade_date")
    return pd.Series(
        df["close"].astype(float).values, index=df["trade_date"].astype(str)
    )


def load_300_ohlcv():
    """读 000300.SH 升序 ohlcv，返回 (close Series, bars list[{dt,o,h,l,c,v}])。"""
    try:
        df = read_range("ts_index_daily", "20230730", "20260927", codes=["000300.SH"])
    except TypeError:
        df = read_range("ts_index_daily", "20230730", "20260927")
        df = df[df["ts_code"] == "000300.SH"]
    df = df.sort_values("trade_date")
    bars = [
        {
            "dt": str(r.trade_date),
            "o": float(r.open),
            "h": float(r.high),
            "l": float(r.low),
            "c": float(r.close),
            "v": float(r.vol),
        }
        for r in df.itertuples()
    ]
    close = pd.Series([b["c"] for b in bars], index=[b["dt"] for b in bars])
    return close, bars


def _confirm_idx(bars, s, idx0, max_lag=40):
    """逐信号截断重放：最小 m 使 analyze(bars[:idx0+m+1]) 检出 (type,dt)。
    返回可成交确认时点 idx0+m；始终未检出（含滞后超 max_lag）返回 -1。
    与 chanlun_backtest.eval_samples 同口径（最早可检出 = 可成交日）。"""
    from chanlun_signal import analyze as _ca

    for m in range(0, min(max_lag, len(bars) - idx0) + 1):
        try:
            st = _ca(bars[: idx0 + m + 1])
        except Exception:
            continue
        for t in st.get("signals", []):
            if t["type"] == s["type"] and t["dt"] == s["dt"]:
                return idx0 + m
    return -1


def _apply_stop(close, bars, buy_cis, sell_cis, k=2.0, atr_n=20):
    """ATR 止损叠加：买点入场后，持仓期间收盘 < 入场价 - k*ATR(入场时)
    则当日决策止损、次日离场，至下个买点前保持空仓。ATR 不足（数据初期）
    不设止损。返回决策信号 Series（T+1 经 run_vec shift 生效）。"""
    hs = pd.Series([b["h"] for b in bars], index=close.index)
    ls = pd.Series([b["l"] for b in bars], index=close.index)
    prev_c = close.shift(1)
    tr = pd.concat([(hs - ls), (hs - prev_c).abs(), (ls - prev_c).abs()], axis=1).max(
        axis=1
    )
    atr = tr.rolling(atr_n).mean()
    ev = sorted(set(buy_cis) | set(sell_cis))
    etype = {}
    for c in buy_cis:
        etype.setdefault(c, "buy")
    for c in sell_cis:
        etype[c] = "sell"
    sig = pd.Series(0.0, index=close.index)
    in_pos = False
    stop_px = None
    ei = 0
    n = len(close)
    for day in range(n):
        if ei < len(ev) and ev[ei] == day:
            t = etype.get(day)
            if t == "buy" and not in_pos:
                in_pos = True
                a = atr.iloc[day]
                stop_px = (close.iloc[day] - k * a) if (a == a and a > 0) else None
            elif t == "sell" and in_pos:
                in_pos = False
                stop_px = None
            ei += 1
        if in_pos and stop_px is not None and close.iloc[day] < stop_px:
            in_pos = False
            stop_px = None
        sig.iloc[day] = 1.0 if in_pos else 0.0
    return sig


def chan_full_oos_eval(
    close,
    bars,
    bench_close=None,
    vol_target=None,
    stop_k=None,
    sig_types=None,
    ret_series=False,
):
    """完整缠论三类买卖点 → 持仓 Series → 同 OOS 折窗评估（无寻优）。

    防未来：全量 analyze 得信号，逐信号截断重放求最早可检出时点 ci，
    仅 ci 收盘决策、经 run_vec shift(1) 次日生效。买点(1/2/3买)→持多1，
    卖点(1/2/3卖)→空仓0。
    stop_k：ATR 止损系数（如 2.0），止损价=入场价 - k*ATR(20)，持仓期间
    收盘跌破则次日离场至下个买点；None=不止损（ffill 状态延续）。
    OOS 段=[TRAIN_N,n) 连续区间（折窗首尾相接），截取 run_vec 结果评估，
    持仓跨折继承历史状态。
    """
    from chanlun_signal import analyze as _ca

    n = len(bars)
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    st = _ca(bars)
    buy_cis, sell_cis = [], []
    for s in sorted(st.get("signals", []), key=lambda x: idx.get(x["dt"], 0)):
        if sig_types is not None and s["type"] not in sig_types:
            continue
        i0 = idx.get(s["dt"])
        if i0 is None or not s.get("price"):
            continue
        ci = _confirm_idx(bars, s, i0)
        if ci < 0 or ci >= n:
            continue
        (buy_cis if s["type"].endswith("买") else sell_cis).append(ci)
    if stop_k:
        sig = _apply_stop(close, bars, buy_cis, sell_cis, k=stop_k)
    else:
        sig = pd.Series(np.nan, index=close.index)
        for ci in buy_cis:
            sig.iloc[ci] = 1.0
        for ci in sell_cis:
            sig.iloc[ci] = 0.0
        sig = sig.ffill().fillna(0.0)
    net, _, pos = mt.run_vec(close, sig, COST_BPS, vol_target=vol_target)
    oos_lo = TRAIN_N
    net_oos = net.iloc[oos_lo:]
    pos_oos = pos.iloc[oos_lo:]
    if ret_series:
        return net_oos, pos_oos
    bret = None
    if bench_close is not None:
        bret = bench_close.iloc[oos_lo:].pct_change().fillna(0)
    return mt.evaluate(net_oos, pos_oos, bret)


if __name__ == "__main__":
    close = load_300()
    n = len(close)
    print("000300.SH %d 根 %s~%s" % (n, close.index[0], close.index[-1]))
    grid = {"fast": [3, 5, 10], "slow": [20, 50, 60, 120]}

    f_p, oos_p, base_m = walk_forward(close, grid, "plain", close)
    f_r, oos_r, _ = walk_forward(close, grid, "robust", close)
    f_v, oos_v, _ = walk_forward(close, grid, "robust", close, vol_target=0.15)
    chan_raw = chan_oos_eval(close, close, amp_min=0, trend_n=0)
    chan_flt = chan_oos_eval(close, close)
    close_h, bars_h = load_300_ohlcv()
    chan_full = chan_full_oos_eval(close_h, bars_h, close_h)
    chan_full_v = chan_full_oos_eval(close_h, bars_h, close_h, vol_target=0.15)

    for tag, folds in (
        ("普通寻优", f_p),
        ("抗过拟合", f_r),
        ("抗过拟合+波动率目标0.15", f_v),
    ):
        print("\n── %s %d 折 ──" % (tag, len(folds)))
        for f in folds:
            print(
                "  %s best=%s tr_sh=%.2f oos_sh=%5.2f oos_ret=%6.2f%%"
                % (
                    f["test"],
                    str(f["best"]).replace("'", ""),
                    f["train_sharpe"],
                    f["oos_sharpe"],
                    f["oos_ret"] * 100,
                )
            )

    print("\n── 样本外八指标对比（同一 OOS 窗口） ──")
    mt.show("普通寻优 样本外", oos_p)
    mt.show("抗过拟合 样本外", oos_r)
    mt.show("抗过拟合+波动率目标0.15", oos_v)
    mt.show("缠论影子(无过滤)", chan_raw)
    mt.show("缠论影子(幅度0.5%+MA60)", chan_flt)
    mt.show("缠论完整(逐信号重放)", chan_full)
    mt.show("缠论完整+波动率目标0.15", chan_full_v)
    mt.show("固定基线5/20 样本外", base_m)

    tr_p = sum(f["train_sharpe"] for f in f_p) / len(f_p)
    tr_r = sum(f["train_sharpe"] for f in f_r) / len(f_r)
    print("\n纪律门（OOS夏普 ≥ 样本内均值×0.6）：")
    print(
        "  普通寻优   %.2f vs %.2f → %s"
        % (
            oos_p["sharpe"],
            0.6 * tr_p,
            "PASS" if oos_p["sharpe"] >= 0.6 * tr_p else "FAIL",
        )
    )
    print(
        "  抗过拟合   %.2f vs %.2f → %s"
        % (
            oos_r["sharpe"],
            0.6 * tr_r,
            "PASS" if oos_r["sharpe"] >= 0.6 * tr_r else "FAIL",
        )
    )
    print(
        "  抗过拟合+波动率目标 %.2f vs %.2f → %s"
        % (
            oos_v["sharpe"],
            0.6 * tr_r,
            "PASS" if oos_v["sharpe"] >= 0.6 * tr_r else "FAIL",
        )
    )
    print("  缠论影子/完整 无寻优 → 纪律门不适用")
