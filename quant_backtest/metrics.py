# -*- coding: utf-8 -*-
"""八指标评估库：Sharpe/Sortino/MaxDD/Calmar/胜率/盈亏比/年化换手/信息比率。

口径：年化 252 交易日；signal 当日收盘决策、次日生效（run_vec 内 shift(1)）；
成本 = 换手 × cost_bps/1e4；IR 基准 = 买入持有。
"""
import numpy as np
import pandas as pd

TD = 252


def run_vec(close, signal, cost_bps=3, vol_target=None, vol_n=20, max_lev=2.0):
    """signal(0/1 Series, index 对齐 close) → (净日收益, 权益曲线, 实际持仓)。

    vol_target：波动率目标仓位（如 0.15）——scale=min(目标/已实现波动率, max_lev)，
    已实现波动率=ret.rolling(vol_n).std()*sqrt(252)，scale.shift(1) 防未来函数。
    """
    pos = signal.shift(1).fillna(0).astype(float)      # T+1 生效，防未来函数
    ret = close.pct_change().fillna(0)
    if vol_target:
        vol = ret.rolling(vol_n).std() * (252 ** 0.5)
        scale = (vol_target / vol).clip(upper=max_lev).shift(1).fillna(0)
        pos = pos * scale
    turn = pos.diff().abs()
    turn.iloc[0] = abs(float(pos.iloc[0]))
    net = pos * ret - turn * cost_bps / 1e4
    return net, (1 + net).cumprod(), pos


def evaluate(net, pos, bench_ret=None):
    """net=净日收益, pos=实际持仓；返回八指标 dict（附 equity 曲线）。"""
    eq = (1 + net).cumprod()
    n = max(len(net), 1)
    years = n / TD
    total = float(eq.iloc[-1] - 1)
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0

    vol = float(net.std() * np.sqrt(TD)) if n > 1 else 0.0
    sharpe = float(net.mean() * TD / vol) if vol > 0 else 0.0

    down = net[net < 0]
    dvol = float(down.std() * np.sqrt(TD)) if len(down) > 1 else 0.0
    sortino = float(net.mean() * TD / dvol) if dvol > 0 else 0.0

    dd = eq / eq.cummax() - 1
    maxdd = float(dd.min()) if n else 0.0
    calmar = cagr / abs(maxdd) if maxdd < 0 else 0.0

    held = net[pos > 0]
    wins, losses = held[held > 0], held[held < 0]
    win_rate = float((held > 0).mean()) if len(held) else 0.0
    pf = float(wins.sum() / -losses.sum()) \
        if len(losses) and losses.sum() < 0 else 0.0

    turnover = float(pos.diff().abs().sum() / years) if years > 0 else 0.0

    ir = 0.0
    if bench_ret is not None:
        ex = net - bench_ret
        te = float(ex.std() * np.sqrt(TD)) if len(ex) > 1 else 0.0
        ir = float(ex.mean() * TD / te) if te > 0 else 0.0

    return {"total_return": round(total, 4), "cagr": round(cagr, 4),
            "sharpe": round(sharpe, 3), "sortino": round(sortino, 3),
            "max_dd": round(maxdd, 4), "calmar": round(calmar, 3),
            "win_rate": round(win_rate, 4), "profit_factor": round(pf, 3),
            "turnover": round(turnover, 2), "ir": round(ir, 3), "equity": eq}


def show(tag, m):
    print("%-22s 年化=%7.2f%% 夏普=%5.2f 索提诺=%5.2f 回撤=%6.1f%% 卡玛=%5.2f "
          "胜率=%5.1f%% 盈亏比=%5.2f 换手=%6.1f IR=%5.2f"
          % (tag, m["cagr"] * 100, m["sharpe"], m["sortino"],
             m["max_dd"] * 100, m["calmar"], m["win_rate"] * 100,
             m["profit_factor"], m["turnover"], m["ir"]))
