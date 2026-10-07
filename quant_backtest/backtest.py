# -*- coding: utf-8 -*-
"""向量化回测引擎 + 绩效指标。

假设：仅做多、T+1 信号（position 在当日收盘后决策，次日生效），
交易成本 = 换手 × cost_bps，滑点在换手时额外扣除 slippage_bps。
"""
import numpy as np
import pandas as pd


def run(df, signal, init_cash=1_000_000, cost_bps=3, slippage_bps=1):
    """
    df: DataFrame with 'close' column (index=trade_date)
    signal: Series of 0/1 aligned to df.index (1=持有多头, 0=空仓)
    返回 dict: metrics + equity Series
    """
    close = df["close"]
    ret = close.pct_change().fillna(0)
    # 信号次日生效，避免未来函数
    pos = signal.shift(1).fillna(0).astype(float)

    # 策略收益率 = 持仓 × 当日收益
    strat_ret = pos * ret

    # 换手成本：仓位变化的绝对值
    turnover = pos.diff().abs().fillna(pos.abs())
    cost = turnover * (cost_bps + slippage_bps) / 10000.0

    net_ret = strat_ret - cost
    equity = (1 + net_ret).cumprod()

    metrics = _metrics(net_ret, equity, pos, close)
    metrics["init_cash"] = init_cash
    metrics["final_value"] = float(init_cash * equity.iloc[-1])
    metrics["equity"] = equity
    metrics["net_ret"] = net_ret
    metrics["position"] = pos
    metrics["trade_list"] = _trade_list(pos, close)
    return metrics


def _trade_list(pos, close):
    """逐笔交易：开仓 = pos 0→1 当日收盘价，平仓 = 1→0 当日收盘价；
    收尾仍持仓则按末日收盘标记未平仓。pnl 不含交易成本。"""
    out = []
    entry = None
    for dt, p in pos.items():
        if p > 0 and entry is None:
            entry = (dt, float(close.loc[dt]))
        elif p == 0 and entry is not None:
            out.append({"in": entry[0].strftime("%m-%d"), "in_px": entry[1],
                        "out": dt.strftime("%m-%d"), "out_px": float(close.loc[dt]),
                        "pnl": float(close.loc[dt]) / entry[1] - 1.0})
            entry = None
    if entry is not None:
        out.append({"in": entry[0].strftime("%m-%d"), "in_px": entry[1],
                    "out": close.index[-1].strftime("%m-%d"),
                    "out_px": float(close.iloc[-1]),
                    "pnl": float(close.iloc[-1]) / entry[1] - 1.0,
                    "open": True})
    return out


def _metrics(net_ret, equity, pos, close):
    total_ret = float(equity.iloc[-1] - 1)
    n = len(net_ret)
    years = n / 252.0 if n > 0 else 1
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 else 0

    vol = float(net_ret.std() * np.sqrt(252)) if n > 1 else 0
    sharpe = float((net_ret.mean() * 252) / vol) if vol > 0 else 0

    downside = net_ret[net_ret < 0]
    sortino = float((net_ret.mean() * 252) / (downside.std() * np.sqrt(252))) \
        if len(downside) > 1 and downside.std() > 0 else 0

    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = float(drawdown.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0

    # 胜率（按持仓日）
    held_ret = net_ret[pos > 0]
    win_rate = float((held_ret > 0).mean()) if len(held_ret) > 0 else 0

    # 交易次数（仓位从 0→1 计一次开仓）
    trades = int(((pos.diff() > 0).sum()))

    # 最大连续亏损
    losses = (net_ret < 0).astype(int)
    max_loss_streak = 0
    cur = 0
    for v in losses:
        cur = cur + 1 if v else 0
        max_loss_streak = max(max_loss_streak, cur)

    return {
        "total_return": round(total_ret, 4),
        "cagr": round(cagr, 4),
        "annual_vol": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(max_dd, 4),
        "calmar": round(calmar, 3),
        "win_rate": round(win_rate, 4),
        "trades": trades,
        "max_loss_streak": max_loss_streak,
        "bar_count": n,
    }


def buy_and_hold(df):
    """基准：买入持有。"""
    ret = df["close"].pct_change().fillna(0)
    equity = (1 + ret).cumprod()
    m = _metrics(ret, equity, pd.Series(1, index=df.index), df["close"])
    m["equity"] = equity
    return m
