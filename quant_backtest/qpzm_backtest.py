# -*- coding: utf-8 -*-
"""青萍之末策略回测锚（qpzm_backtest）—— S4 里程碑，只读历史数据。

目的：对已上产线、参数未经历史验证的 rps_resonance.py 打分体系建立回测锚。
本文件为独立新建文件，不 import、不修改 rps_resonance.py / portfolio.py /
sector_effect.py；打分公式按产线逐条复刻（见 DESIGN 注释与单元测试）。

回测口径（与产线对齐）:
  · RPS: 逐交易日截面。每股用自身 bar 序列算 N 日涨幅（N=50/120/250），
    全市场 rank(pct=True)*100，复合分 = Σ(w_p*rps_p)/Σ(可用周期权重)，
    现行权重 {50:0.4, 120:0.3, 250:0.3}（rps_resonance.RPS_W）。
    上市不足 250 日的次新股：不跳过（与产线一致），缺周期降权参与，
    并按可用周期数标记，报告提供按周期数的信号拆分。
  · 共振打分: tech/JEV/RPS/env 四维缺维降权（W4 tech.35/jev.25/rps.25/
    env.15）。JEV 依赖 news.db 近 7 天新闻，历史不可重放 → 回测口径即
    产线 JEV 缺维时的三维降权（tech/rps/env → 0.4667/0.3333/0.2），
    与 reports/rps_latest.json 的 dims="tech+rps+env" 完全一致。
    tech = 4策略多头数*25 ± 10(close vs MA20)，clip[0,100]；
    ≥买入阈值 → 买入信号，≥观察阈值 → 观察；env safe=False → 一票否决。
  · 环境闸门: breadth = 站上MA20占比（≥21 根 bar 才计入），med20 =
    20日涨幅中位数，upfrac = 20日上涨占比；score = 60*breadth + 40*upfrac；
    breadth<0.40 或 med20<-3% → safe=False（rps_resonance.market_env）。
  · 信号后验: T 日收盘产生信号 → T+1 开盘价买入，持有 N 个交易日
    （T+1 起第 N 根 bar 收盘卖出）；N∈{5,10,20}。成本 = 往返一次性，
    默认 0.15%。K254：全价后复权（pre_close 链）；T+1 一字板（开=高=收
    且开盘涨幅达板）视为无法成交剔除；出场停牌按最后可得价结算并计数。
  · 参数网格: RPS 权重 3 组 × 买入/观察阈值 2 组 × 闸门开关 2 态。

产物: reports/qpzm_backtest_report.json（原子写）+
      reports/qpzm_backtest_summary.md（人类可读摘要）。

用法（产线解释器唯一合法路径，junction 路径避免中文路径问题）:
  "C:/Program Files/Python311/python.exe" C:/aios/quant_backtest/qpzm_backtest.py \
      --start 20251001 --end 20260930          # 近 1 年小样本
  ... --start 20240401                          # 数据允许的全量信号窗
  ... --stocks-limit 80                         # 冒烟：只取部分股票
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")

# ── 产线参数快照（rps_resonance.py / config.json，只读复刻） ──
PERIODS = (50, 120, 250)
RPS_W_CUR = {50: 0.4, 120: 0.3, 250: 0.3}       # 现行
RPS_W_EQ = {50: 1 / 3, 120: 1 / 3, 250: 1 / 3}  # 等权
RPS_W_ALT = {50: 0.5, 120: 0.3, 250: 0.2}       # 替代：短周期加权
W4 = {"tech": 0.35, "jev": 0.25, "rps": 0.25, "env": 0.15}
# 077k w015 态：JEV 以 50 中性分按 0.15 进权重的权重重组
W4_W015 = {"tech": 0.40, "jev": 0.15, "rps": 0.30, "env": 0.15}
JEV_REPLAY_PATH = os.path.join(HERE, "data", "jev_archive",
                               "jev_replay_series.json")
JEV_MODES = ("missing", "neutral50", "w015", "replay")
TH_CUR = (75, 50)      # 现行 买入/观察
TH_ALT = (70, 50)
MIN_BARS_ELIG = 55     # resonance_watchlist 最低 bar 数（缺则不参与打分）
MIN_BARS_ENV = 21      # market_env 计入宽度要求
STRAT_PARAMS = {       # config.json strategies（产线现行参数）
    "ma_cross": {"fast": 8, "slow": 20},
    "rsi_reversal": {"period": 6, "oversold": 30, "overbought": 65},
    "boll_breakout": {"period": 20, "k": 1.5},
    "trend_follow": {"n": 20, "slope_lookback": 3},
}

DESIGN = {
    "replay": "逐交易日全 A 截面；每股用自身 bar 序列算涨幅（与产线 "
              "_load_market 只读自身 close 数组一致），停牌日不占窗口",
    "rps": "N 日涨幅截面 rank(pct=True)*100（pandas average 法，同产线）；"
           "复合分按可用周期权重归一；涨幅不可得(NaN)不参与排名",
    "sub_new": "上市不足 250 日不跳过（与产线一致），缺周期降权参与，按可用"
               "周期数(1/2/3)标记并给出信号拆分",
    "jev": "JEV 维历史不可重放（news.db 仅近 7 天），按产线缺维降权规则取 "
           "tech/rps/env 三维（归一权重 0.4667/0.3333/0.2），与 "
           "rps_latest.json 的 dims='tech+rps+env' 一致；"
           "077k 起可经 --jev-mode 注入四态：missing(默认，同上)/"
           "neutral50(50 中性分进权重)/w015(权重 tech.40/rps.30/jev.15/"
           "env.15，jev 按 50 中性进权重)/replay(消费 data/jev_archive/"
           "jev_replay_series.json 逐日截面，归档仅覆盖其日期范围——"
           "回测区间早于该范围时相关日期无 jev 数据自然缺维，预期行为)",
    "entry_exit": "T 日收盘信号 → T+1 开盘买入 → 持有 N 个交易日后收盘卖出；"
                  "K254：出场日停牌按最后可得价结算（ffill）并计数"
                  "(n_dropped 口径变更为 nobar+一字板)；T+1 一字板（开=高=收"
                  "且开盘涨幅达板）视为无法成交剔除",
    "cost": "往返一次性成本（佣金双边+印花税+滑点），默认 0.0015",
    "price": "K254 起后复权口径：逐股 own-bar 因子链 "
             "f(t)=f(t-1)*close(t-1)/pre_close(t)，close/open 全乘因子；"
             "（K253 实证旧「未复权」口径近一年 2883 起除权、1131 起单次"
             "假收益>2pp、最大 -36.77%，远超旧文档所称「轻微失真」）",
    "excess": "超额 = 信号逐笔收益均值 − 基准（同样的 T+1 开盘进/持有 N 日"
              "收盘出、全体可交易股票等权）限制在同一批信号日内的逐笔均值",
    "gate_veto": "闸门开 = env safe=False 时买入/观察信号全部否决；"
                 "闸门关 = 不否决（env 分仍计入打分），用于隔离否决贡献",
}


# ── 纯计算层（可单测） ───────────────────────────────────────
def cross_rps(chg_by_period, weights):
    """截面 RPS 复合分（复刻 batch_rps）。

    chg_by_period: {p: DataFrame(dates × stocks) N日涨幅}
    weights: {p: w}
    返回 (mix DataFrame, n_avail DataFrame(float))。
    """
    num = None
    den = None
    n_avail = None
    for p, chg in chg_by_period.items():
        r = (chg.rank(axis=1, pct=True) * 100).round(1)   # 同产线 round(1)
        wp = float(weights.get(p, 0.0))
        rw = (r * wp).fillna(0.0)        # 产线 sum(axis=1) 跳 NaN 的等价写法
        num = rw if num is None else num + rw
        m = r.notna().astype(float)
        den = m * wp if den is None else den + m * wp
        n_avail = m if n_avail is None else n_avail + m
    mix = (num / den.replace(0.0, np.nan)).round(1)
    return mix, n_avail


def env_gate(above, ret20):
    """环境闸门（复刻 market_env，逐日向量化）。

    above/ret20: DataFrame(dates × stocks)，bar 数不足 MIN_BARS_ENV 处为 NaN。
    返回 DataFrame: breadth, med20, upfrac, score(float), safe(bool)。
    """
    a = above.notna()
    breadth = above.where(a).mean(axis=1)
    upfrac = (ret20 > 0).where(a).mean(axis=1)
    med = ret20.where(a).median(axis=1)
    score = (60 * breadth + 40 * upfrac).clip(0, 100)
    safe = ~((breadth < 0.40) | (med < -0.03))
    return pd.DataFrame({"breadth": breadth.round(3), "med20": med.round(4),
                         "upfrac": upfrac.round(3), "score": score,
                         "safe": safe})


def tech_score(longs, above20):
    """技术维（复刻 resonance_watchlist: tech = longs*25 ± 10, clip[0,100]）。

    longs: 4 策略多头数 wide；above20: close>MA20 wide（NaN=数据不足→按否）。
    """
    bonus = np.where(above20.fillna(False).to_numpy(dtype=bool), 10.0, -10.0)
    return (longs * 25 + bonus).clip(0, 100)


def load_jev_replay(path=JEV_REPLAY_PATH):
    """读 jev_replay_series.json → (score DataFrame dates×codes, meta)。

    日期键 'YYYY-MM-DD' 转成 int64 YYYYMMDD 与 wide 索引对齐；只有归档
    覆盖内的 (date, code) 有分，其余为 NaN（自然缺维，预期行为）。
    """
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = {}
    for d, by_code in payload.get("series", {}).items():
        d_int = int(d.replace("-", ""))
        for code, rec in by_code.items():
            # 审查 P0（077l）：replay 归档键是 sz301047/gb_nvda 市场形态，
            # 回测 wide 列是 6 位数字码——双形态都写入，命中为准
            c6 = str(code).split(".")[0][-6:]
            rows.setdefault(d_int, {})[c6] = float(rec.get("score", 50.0))
            if str(code) != c6:
                rows[d_int][str(code)] = float(rec.get("score", 50.0))
    mat = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    meta = payload.get("meta", {})
    # 审查 P2-1（077l）：末日为重放时刻前部分数据，剔除防半日偏置
    if meta.get("partial_last_day") and len(mat) > 1:
        mat = mat.iloc[:-1]
        meta["partial_last_day_dropped"] = str(mat.index[-1])
    return mat, meta


def combine_score(tech, rps_mix, env_df, buy_th=75.0, obs_th=50.0,
                  gate_on=True, eligible=None, jev_mode="missing",
                  jev_mat=None):
    """共振打分 + 信号（复刻 resonance_watchlist 缺维降权）。

    jev_mode 四态（077k）:
      missing   JEV 缺维（默认，历史基准，与产线 JEV 缺维完全一致）
      neutral50 JEV 按 50 中性分进权重（等价 env_score 广播形式）
      w015      权重重组 tech.40/rps.30/jev.15/env.15，JEV 按 50 中性进权重
      replay    消费 jev_mat（逐日截面 replay 分）；能匹配的票该分作 jev 维，
                匹配不到的票保持缺维降权（replay 归档仅覆盖其日期范围）

    返回 (score DataFrame, buy bool, obs bool)。gate_on 且 env safe=False →
    买入/观察全部否决（与产线 action='回避（环境闸门）' 一致）。
    """
    if jev_mode not in JEV_MODES:
        raise ValueError("unknown jev_mode: %s" % jev_mode)
    if jev_mode == "replay" and jev_mat is None:
        raise ValueError("jev_mode=replay requires jev_mat")
    t = tech.to_numpy(dtype=float)
    rp = rps_mix.to_numpy(dtype=float)
    el = tech.notna().to_numpy(dtype=bool) if eligible is None \
        else (eligible & tech.notna()).to_numpy(dtype=bool)
    hr = el & ~np.isnan(rp)
    env_score = env_df["score"].to_numpy(dtype=float)      # (D,)
    if jev_mode == "missing":
        wsum = (W4["tech"] + W4["env"]) * el + W4["rps"] * hr
        num = t * W4["tech"] + env_score[:, None] * W4["env"] \
            + np.where(np.isnan(rp), 0.0, rp * W4["rps"])
    elif jev_mode == "neutral50":
        wsum = (W4["tech"] + W4["env"] + W4["jev"]) * el + W4["rps"] * hr
        num = t * W4["tech"] + env_score[:, None] * W4["env"] \
            + 50.0 * W4["jev"] * el \
            + np.where(np.isnan(rp), 0.0, rp * W4["rps"])
    elif jev_mode == "w015":
        wt = W4_W015["tech"]
        wr = W4_W015["rps"]
        wj = W4_W015["jev"]
        we = W4_W015["env"]
        wsum = (wt + we + wj) * el + wr * hr
        num = t * wt + env_score[:, None] * we \
            + 50.0 * wj * el \
            + np.where(np.isnan(rp), 0.0, rp * wr)
    else:  # replay
        jm = jev_mat.reindex(index=tech.index,
                             columns=tech.columns).to_numpy(dtype=float)
        jv = el & ~np.isnan(jm)
        wsum = (W4["tech"] + W4["env"]) * el + W4["rps"] * hr \
            + W4["jev"] * jv
        num = t * W4["tech"] + env_score[:, None] * W4["env"] \
            + np.where(np.isnan(rp), 0.0, rp * W4["rps"]) \
            + np.where(np.isnan(jm), 0.0, jm * W4["jev"])
    sc = np.divide(num, wsum, out=np.full(num.shape, np.nan),
                   where=wsum > 0)
    gate = env_df["safe"].to_numpy(dtype=bool) if gate_on \
        else np.ones(len(env_df), dtype=bool)
    gate_ok = np.broadcast_to(gate[:, None], sc.shape)
    ge = np.nan_to_num(sc, nan=-1e9)
    buy_m = el & hr & gate_ok & (ge >= buy_th)
    obs_m = el & hr & gate_ok & (ge >= obs_th) & (ge < buy_th)
    score = pd.DataFrame(sc, index=tech.index, columns=tech.columns)
    buy = pd.DataFrame(buy_m, index=tech.index, columns=tech.columns)
    obs = pd.DataFrame(obs_m, index=tech.index, columns=tech.columns)
    return score, buy, obs


def _flat_series(mask, values, index, columns):
    """bool 矩阵取值摊平为 MultiIndex(date, code) Series（跳过 False）。"""
    vals = np.where(mask.to_numpy(dtype=bool), values, np.nan).ravel()
    mi = pd.MultiIndex.from_product([index, columns], names=["date", "code"])
    return pd.Series(vals, index=mi).dropna()


def trade_returns(buy, open_px, close_px, n, cost, unfill=None):
    """T+1 开盘买入、持有 n 日收盘卖出的逐信号净收益（含成本）。

    K254：出场日停牌按最后可得价结算（close 先 ffill 再 shift）；
    T+1 一字板（unfill 矩阵）视为无法成交剔除。
    返回 (ret Series(date, code), n_dropped int)（n_dropped=nobar+一字板）。
    """
    entry = open_px.shift(-1).to_numpy(dtype=float)
    exit_px = close_px.ffill().shift(-n).to_numpy(dtype=float)
    ret_vals = exit_px / entry - 1.0 - cost
    ok = open_px.shift(-1).notna() & close_px.ffill().shift(-n).notna()
    if unfill is not None:
        unf = (unfill.shift(-1).fillna(False)
               .reindex_like(open_px).fillna(False))
        ok = ok & ~unf
    valid = buy & ok
    n_dropped = int((buy & ~ok).sum().sum())
    return _flat_series(valid, ret_vals, buy.index, buy.columns), n_dropped


def date_mean(ret_s):
    """按信号日聚合的均值收益序列。"""
    if len(ret_s) == 0:
        return pd.Series(dtype=float)
    return ret_s.groupby(level="date").mean()


def max_drawdown(daily_ret):
    """日频净值最大回撤（算术累乘净值）。"""
    if len(daily_ret) == 0:
        return 0.0
    eq = (1.0 + daily_ret.to_numpy(dtype=float)).cumprod()
    dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
    return float(dd)


def bench_returns(eligible, open_px, close_px, n, cost, unfill=None):
    """基准逐笔收益：全体可交易股票同窗口（T+1 开盘进 / 持有 n 日收盘出）。

    K254：停牌 ffill 出场；entry 一字板剔除（与信号侧对称）。
    """
    entry = open_px.shift(-1)
    exit_px = close_px.ffill().shift(-n)
    valid = eligible & entry.notna() & exit_px.notna()
    if unfill is not None:
        unf = (unfill.shift(-1).fillna(False)
               .reindex_like(open_px).fillna(False))
        valid = valid & ~unf
    ret_vals = (exit_px / entry - 1.0 - cost).to_numpy(dtype=float)
    return _flat_series(valid, ret_vals, eligible.index, eligible.columns)


def eval_hold(buy, open_px, close_px, n, cost, bench_ret=None,
              eligible=None, unfill=None):
    """一个 (信号, 持有期) 组合的后验指标。

    bench_ret 未给则现算（同一 hold 下与变体无关，可预计算传入复用）。
    超额 = 信号逐笔均值 − 基准限制在同一批信号日内的逐笔均值。
    """
    ret, n_drop = trade_returns(buy, open_px, close_px, n, cost,
                                unfill=unfill)
    dm = date_mean(ret)
    if bench_ret is None:
        bench_ret = bench_returns(eligible, open_px, close_px, n, cost,
                                  unfill=unfill)
    if len(dm) and len(bench_ret):
        sig_dates = set(dm.index)
        b = bench_ret[bench_ret.index.get_level_values("date").isin(sig_dates)]
        bench_mean = float(b.mean()) if len(b) else None
    else:
        bench_mean = None
    stats = {
        "n_trades": int(len(ret)),
        "n_signal_dates": int(len(dm)),
        "n_dropped_nobar": n_drop,
        "mean": round(float(ret.mean()), 6) if len(ret) else None,
        "median": round(float(ret.median()), 6) if len(ret) else None,
        "win_rate": round(float((ret > 0).mean()), 4) if len(ret) else None,
        "std": round(float(ret.std()), 6) if len(ret) > 1 else None,
        "p10": round(float(ret.quantile(0.10)), 6) if len(ret) else None,
        "p90": round(float(ret.quantile(0.90)), 6) if len(ret) else None,
        "max_dd": round(max_drawdown(dm), 4),
        "bench_mean": round(bench_mean, 6) if bench_mean is not None else None,
        "excess_mean": round(float(ret.mean()) - bench_mean, 6)
        if len(ret) and bench_mean is not None else None,
    }
    return stats


# ── 数据层 ──────────────────────────────────────────────────
def stock_positions(close):
    """4 策略持仓数（复刻 strategies.REGISTRY + config.json 参数；
    指标口径同 indicators.py：rolling min_periods=1、RSI fillna(50)、
    boll 突破持有至跌破中轨、RSI 事件帧）。返回 int Series(0-4)。"""
    c = close
    p = STRAT_PARAMS
    fa = c.rolling(int(p["ma_cross"]["fast"]), min_periods=1).mean()
    sl = c.rolling(int(p["ma_cross"]["slow"]), min_periods=1).mean()
    pos_ma = (fa > sl).astype(int)
    n = int(p["rsi_reversal"]["period"])
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(n, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(n, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50)
    pos_rsi = pd.Series(np.nan, index=c.index)
    pos_rsi[rsi < float(p["rsi_reversal"]["oversold"])] = 1
    pos_rsi[rsi > float(p["rsi_reversal"]["overbought"])] = 0
    pos_rsi = pos_rsi.ffill().fillna(0).astype(int)
    k = float(p["boll_breakout"]["k"])
    nb = int(p["boll_breakout"]["period"])
    mid = c.rolling(nb, min_periods=1).mean()
    std = c.rolling(nb, min_periods=1).std(ddof=0)
    upper = mid + k * std
    pos_boll = (c > upper).astype(float).replace(0.0, np.nan)
    pos_boll = pos_boll.ffill().fillna(0).mask(c < mid, 0.0).astype(int)
    m = c.rolling(int(p["trend_follow"]["n"]), min_periods=1).mean()
    slope = m.diff(int(p["trend_follow"]["slope_lookback"]))
    pos_tf = ((c > m) & (slope > 0)).astype(int)
    return pos_ma + pos_rsi + pos_boll + pos_tf


def load_wide(data_dir, stocks_limit=0, log_every=1000):
    """读全部 parquet → union 交易日索引的 wide 矩阵集合。

    逐股在自身 bar 序列上算指标（与产线 own-bar 语义一致），再 scatter。
    返回 dict: dates(np int64), codes, close/open/chg{p}/ret20/longs/
    n_bars(float32 DataFrame), above20(计闸门槛 ≥MIN_BARS_ENV),
    eligible(≥MIN_BARS_ELIG bool), unfill(K254 一字板 bool)；
    K254 起 close/open 为后复权口径（own-bar pre_close 链因子）。
    """
    files = sorted(f for f in os.listdir(data_dir)
                   if f.endswith(".parquet") and not f.startswith("_"))
    if stocks_limit:
        files = files[:int(stocks_limit)]
    codes, per = [], []
    t0 = time.time()
    for i, f in enumerate(files):
        try:
            df = pd.read_parquet(os.path.join(data_dir, f),
                                 columns=["trade_date", "open", "high",
                                          "close", "pre_close"])
        except Exception as ex:  # A5: 坏样本静默消失会让回测样本集悄悄缩水——留痕
            print("[qpzm] 样本读取失败跳过 %s：%s" % (f, str(ex)[:60]))
            continue
        if len(df) < 2:
            continue
        df = df.sort_values("trade_date").reset_index(drop=True)
        codes.append(f[:-8].replace("_", "."))
        per.append(df)
        if (i + 1) % log_every == 0:
            print("  loaded %d/%d (%.0fs)" % (i + 1, len(files),
                                              time.time() - t0), flush=True)
    if not per:
        raise RuntimeError("no parquet data in %s" % data_dir)
    union = np.unique(np.concatenate(
        [d["trade_date"].to_numpy() for d in per])).astype("int64")
    dpos = {int(d): i for i, d in enumerate(union)}
    S, D = len(per), len(union)
    print("universe %d stocks, union %d dates (%s ~ %s)" % (
        S, D, union[0], union[-1]), flush=True)

    def blank():
        return np.full((D, S), np.nan, dtype="float32")

    arr = {k: blank() for k in ("close", "open", "chg50", "chg120",
                                "chg250", "ret20", "longs", "n_bars")}
    above_buf = np.zeros((D, S), dtype=bool)
    unfill_buf = np.zeros((D, S), dtype=bool)   # K254 一字板(T+1 无法成交)
    for j, df in enumerate(per):
        rows = np.array([dpos[int(d)] for d in df["trade_date"].to_numpy()])
        c = df["close"].astype(float)
        o = df["open"].astype(float)
        # K254 后复权因子链（own-bar）：f(t)=f(t-1)*close(t-1)/pre_close(t)
        pc = pd.to_numeric(df["pre_close"], errors="coerce")
        ratio = (c.shift(1) / pc).astype(float)
        ratio = ratio.where(ratio.between(0.05, 20.0), 1.0).fillna(1.0)
        fct = ratio.cumprod()
        c = c * fct
        o = o * fct
        arr["close"][rows, j] = c.to_numpy()
        arr["open"][rows, j] = o.to_numpy()
        arr["n_bars"][rows, j] = np.arange(1, len(df) + 1)
        for p in PERIODS:
            arr["chg%d" % p][rows, j] = (c / c.shift(p) - 1).to_numpy()
        arr["ret20"][rows, j] = (c / c.shift(20) - 1).to_numpy()
        ma20 = c.rolling(20, min_periods=1).mean()
        above_buf[rows, j] = (c > ma20).to_numpy()
        arr["longs"][rows, j] = stock_positions(c).to_numpy()
        # K254 一字板（原始价判定）：开=高=收 且 开盘涨幅达板
        o_raw = df["open"].astype(float)
        h_raw = df["high"].astype(float)
        rg = (o_raw / pc.replace(0.0, np.nan) - 1.0)
        lim = 0.295 if codes[j].endswith(".BJ") else (
            0.195 if codes[j][2:4] in ("30", "68") else 0.095)
        unfill_buf[rows, j] = ((o_raw >= h_raw - 1e-9)
                               & ((o_raw - df["close"].astype(float)).abs()
                                  <= 1e-6)
                               & (rg >= lim - 0.005)
                               & rg.notna()).to_numpy()

    wide = {"dates": union, "codes": codes}
    for k, a in arr.items():
        wide[k] = pd.DataFrame(a, index=union, columns=codes)
    # 闸门只计入 ≥21 根自身 bar（market_env: len(c)>=21）
    wide["above20"] = pd.DataFrame(
        np.where(arr["n_bars"] >= MIN_BARS_ENV, above_buf.astype(float),
                 np.nan), index=union, columns=codes)
    wide["eligible"] = pd.DataFrame(arr["n_bars"] >= MIN_BARS_ELIG,
                                    index=union, columns=codes)
    wide["unfill"] = pd.DataFrame(unfill_buf, index=union, columns=codes)
    return wide


# ── 网格与报告 ──────────────────────────────────────────────
GRID_W = [("w_cur", RPS_W_CUR), ("w_eq", RPS_W_EQ), ("w_alt", RPS_W_ALT)]
GRID_TH = [("th7550", TH_CUR), ("th7050", TH_ALT)]
GRID_GATE = [("gate_on", True), ("gate_off", False)]


def run_backtest(args):
    t0 = time.time()
    wide = load_wide(args.data_dir, args.stocks_limit)
    union, codes = wide["dates"], wide["codes"]

    chg = {p: wide["chg%d" % p] for p in PERIODS}
    env_df = env_gate(wide["above20"], wide["ret20"])
    print("env computed, unsafe days %d/%d" % (
        int((~env_df["safe"]).sum()), len(env_df)), flush=True)

    rps_by_w = {}
    for wname, w in GRID_W:
        rps_by_w[wname] = cross_rps(chg, w)
        print("rps mixed [%s]" % wname, flush=True)

    jev_mat = None
    jev_meta = {}
    if args.jev_mode == "replay":
        jev_mat, jev_meta = load_jev_replay()
        print("jev replay loaded: %s ~ %s (%d codes, %d days)" % (
            jev_meta.get("start"), jev_meta.get("end"),
            jev_mat.shape[1], jev_mat.shape[0]), flush=True)
    else:
        print("jev_mode=%s" % args.jev_mode, flush=True)

    in_win = np.ones(len(union), dtype=bool)
    if args.start:
        in_win &= union >= int(args.start)
    if args.end:
        in_win &= union <= int(args.end)
    win_index = union[in_win]
    holds = args.holds
    eligible = wide["eligible"]

    # 预计算每档持有期的基准逐笔收益（与变体无关）
    bench_by_hold = {}
    for n in holds:
        bench_by_hold[n] = bench_returns(
            eligible.loc[in_win], wide["open"].loc[in_win],
            wide["close"].loc[in_win], n, args.cost,
            unfill=wide.get("unfill"))

    variants = []
    tech_w = tech_score(wide["longs"], wide["above20"])
    for wname, w in GRID_W:
        mix, _n_avail = rps_by_w[wname]
        for thname, (bth, oth) in GRID_TH:
            for gname, g in GRID_GATE:
                _score, buy, _obs = combine_score(
                    tech_w, mix, env_df,
                    buy_th=bth, obs_th=oth, gate_on=g, eligible=eligible,
                    jev_mode=args.jev_mode, jev_mat=jev_mat)
                buy_w = buy.loc[in_win]
                v = {"label": "%s_%s_%s" % (wname, thname, gname),
                     "rps_w": {str(k): round(vv, 4) for k, vv in w.items()},
                     "buy_th": bth, "obs_th": oth, "gate": g,
                     "n_buy_in_window": int(buy_w.sum().sum()),
                     "holds": {}}
                for n in holds:
                    v["holds"][str(n)] = eval_hold(
                        buy_w, wide["open"].loc[in_win],
                        wide["close"].loc[in_win], n, args.cost,
                        bench_ret=bench_by_hold[n],
                        unfill=wide.get("unfill"))
                variants.append(v)
                h0 = v["holds"][str(holds[0])]
                print("%-24s buy=%6d hold%d mean=%s win=%s" % (
                    v["label"], v["n_buy_in_window"], holds[0],
                    h0["mean"], h0["win_rate"]), flush=True)

    # 次新拆分：买入信号按 RPS 可用周期数（现行权重+闸门开，全窗口）
    mix_cur, n_avail_cur = rps_by_w["w_cur"]
    _, buy_cur, _ = combine_score(
        tech_w, mix_cur, env_df, gate_on=True, eligible=eligible,
        jev_mode=args.jev_mode, jev_mat=jev_mat)
    sig_n = n_avail_cur.where(buy_cur).to_numpy().ravel()
    sig_n = sig_n[~np.isnan(sig_n)]
    subnew = {str(int(k)): int(v) for k, v in
              zip(*np.unique(sig_n, return_counts=True))} if len(sig_n) else {}
    # S5 决策数据：按可用周期数分组的后验收益（全窗口，绝对口径组间可比；
    # 不带基准超额——全窗口基准序列内存不可控，组间对比用绝对收益即可）
    subnew_holds = {}
    for k in sorted(subnew, key=int):
        mask = (n_avail_cur == int(k)) & buy_cur
        subnew_holds[k] = {}
        for n in holds:
            ret, _drop = trade_returns(
                mask, wide["open"], wide["close"], n, args.cost,
                unfill=wide.get("unfill"))
            subnew_holds[k][str(n)] = {
                "n_trades": int(len(ret)),
                "mean": round(float(ret.mean()), 6) if len(ret) else None,
                "median": round(float(ret.median()), 6) if len(ret) else None,
                "win_rate": round(float((ret > 0).mean()), 4)
                if len(ret) else None}
    all_ret_hold = {}
    for n in holds:
        ret, _drop = trade_returns(
            buy_cur, wide["open"], wide["close"], n, args.cost,
            unfill=wide.get("unfill"))
        all_ret_hold[str(n)] = {
            "n_trades": int(len(ret)),
            "mean": round(float(ret.mean()), 6) if len(ret) else None,
            "median": round(float(ret.median()), 6) if len(ret) else None,
            "win_rate": round(float((ret > 0).mean()), 4) if len(ret) else None}

    env_rows = []
    for d, r in env_df.loc[in_win].iterrows():
        if pd.isna(r["score"]):
            continue
        env_rows.append({"date": str(int(d)), "breadth": float(r["breadth"]),
                         "med20": float(r["med20"]),
                         "upfrac": float(r["upfrac"]),
                         "score": round(float(r["score"])),
                         "safe": bool(r["safe"])})

    report = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "interpreter": sys.executable,
            "data_dir": args.data_dir,
            "n_stocks": len(codes),
            "n_dates_union": int(len(union)),
            "window": {"start": args.start, "end": args.end,
                       "signal_days": int(in_win.sum())},
            "holds": holds, "cost_roundtrip": args.cost,
            "jev_mode": args.jev_mode,
            "jev_replay_meta": jev_meta,
            "sample_mode": bool(args.stocks_limit),
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "design": DESIGN,
        "params_snapshot": {
            "periods": list(PERIODS), "w4": W4,
            "strategies": STRAT_PARAMS,
            "thresholds_current": list(TH_CUR),
            "min_bars_eligible": MIN_BARS_ELIG,
            "min_bars_env": MIN_BARS_ENV,
        },
        "env_daily": env_rows,
        "variants": variants,
        "subnew_buy_split": {
            "note": "买入信号按 RPS 可用周期数(1/2/3)拆分，现行权重+闸门开，"
                    "全窗口（不受 --start 限制）；holds_by_period=分组后验"
                    "（绝对口径），all=全体对照",
            "counts": subnew,
            "holds_by_period": subnew_holds,
            "all": all_ret_hold},
        "summary": {},
    }
    report["summary"] = build_summary(report, env_df.loc[in_win])
    return report


def build_summary(report, env_win):
    """人类可读摘要（同结构写入 md）。"""
    w = report["meta"]["window"]
    unsafe = int((~env_win["safe"]).sum()) if len(env_win) else 0
    h0 = str(report["meta"]["holds"][0])
    lines = ["# 青萍之末回测锚（%s）" % (
        "小样本" if report["meta"]["sample_mode"] else "全量"), ""]
    lines.append("- 股票数: %d；信号窗: %s ~ %s（%d 个交易日，闸门收紧 %d 天）"
                 % (report["meta"]["n_stocks"], w["start"], w["end"],
                    w["signal_days"], unsafe))
    lines.append("- 成本: 往返 %.2f%%；口径: T+1 开盘买入，持有 N 日收盘卖出；"
                 "超额基准 = 同窗口全体可交易股票等权"
                 % (report["meta"]["cost_roundtrip"] * 100))
    lines.append("")
    lines.append("## 参数网格（hold=%s 日）" % h0)
    lines.append("")
    lines.append("| 变体 | 买入信号 | 均值 | 中位 | 胜率 | 超额 | 净值回撤 |")
    lines.append("|---|---|---|---|---|---|---|")
    for v in report["variants"]:
        s = v["holds"][h0]
        lines.append("| %s | %d | %s | %s | %s | %s | %s |" % (
            v["label"], v["n_buy_in_window"],
            _pct(s["mean"]), _pct(s["median"]), _pct(s["win_rate"], True),
            _pct(s["excess_mean"]), _pct(s["max_dd"])))
    lines.append("")
    lines.append("## 多持有期（现行权重 75/50 闸门开）")
    lines.append("")
    lines.append("| hold | 笔数 | 均值 | 中位 | 胜率 | 最大回撤 |")
    lines.append("|---|---|---|---|---|---|")
    for v in report["variants"]:
        if v["label"] == "w_cur_th7550_gate_on":
            for n, s in sorted(v["holds"].items(), key=lambda kv: int(kv[0])):
                lines.append("| %s | %d | %s | %s | %s | %s |" % (
                    n, s["n_trades"], _pct(s["mean"]), _pct(s["median"]),
                    _pct(s["win_rate"], True), _pct(s["max_dd"])))
    return {"headline_md": "\n".join(lines)}


def _pct(x, is_ratio=False):
    if x is None:
        return "—"
    return ("%+.2f%%" if not is_ratio else "%.1f%%") % (x * 100)


def atomic_write_json(path, payload):
    """tempfile + os.replace 原子写（同产线 _atomic_write_json 风格）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="青萍之末策略回测锚")
    ap.add_argument("--start", default="20251001", help="信号窗起 YYYYMMDD")
    ap.add_argument("--end", default=None, help="信号窗止 YYYYMMDD（默认数据尾）")
    ap.add_argument("--hold", default="5,10,20", help="持有交易日，逗号分隔")
    ap.add_argument("--cost", type=float, default=0.0015,
                    help="往返成本比例，默认 0.0015")
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--out", default=os.path.join(
        REPORTS, "qpzm_backtest_report.json"))
    ap.add_argument("--stocks-limit", type=int, default=0,
                    help=">0 只取前 N 只股票（冒烟用）")
    ap.add_argument("--jev-mode", default="missing",
                    choices=list(JEV_MODES),
                    help="JEV 维注入态：missing=缺维降权（默认，历史基准）；"
                         "neutral50=50 中性分进权重；w015=权重重组 "
                         "tech.40/rps.30/jev.15/env.15；replay=消费 "
                         "jev_replay_series.json 逐日截面（覆盖外日期自然缺维）")
    args = ap.parse_args(argv)
    args.holds = sorted({int(x) for x in str(args.hold).split(",") if x})
    return args


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args(argv)
    report = run_backtest(args)
    atomic_write_json(args.out, report)
    md_path = os.path.splitext(args.out)[0] + "_summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report["summary"]["headline_md"] + "\n")
    print("report -> %s" % args.out)
    print("summary -> %s" % md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
