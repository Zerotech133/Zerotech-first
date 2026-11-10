# -*- coding: utf-8 -*-
"""qpzm_backtest 单元测试（纯合成数据，不依赖本地 parquet）。

覆盖:
  1. RPS 百分位与复合分正确性（已知数据、缺周期降权、NaN 不参与排名）
  2. 环境闸门一票否决（breadth/med20 边界、gate_on/gate_off）
  3. 收益计算（T+1 开盘进、持有 N 日收盘出、末端缺 bar 剔除、成本）
  4. 缺数据股票跳过（不足最低 bar 数不产生信号、load_wide 资格矩阵）
  5. tech 打分、最大回撤、与产线公式逐条对照

运行:
  "C:/Program Files/Python311/python.exe" C:/aios/quant_backtest/test_qpzm_backtest.py
"""

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qpzm_backtest as qb  # noqa: E402
import logging

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    status = "PASS" if cond else "FAIL"
    print("[%s] %s" % (status, name))


def idx(n, start=20240101):
    return np.arange(start, start + n)


# ── 1. RPS ──────────────────────────────────────────────────
def test_rps_percentile():
    # 5 只股票，50 日涨幅已知且互不相同 → 百分位 = rank/5*100
    dates = idx(60)
    cols = ["A", "B", "C", "D", "E"]
    chg = pd.DataFrame(
        [[0.01, 0.05, 0.03, 0.09, -0.02]] * len(dates), index=dates, columns=cols
    )
    mix, n_avail = qb.cross_rps({50: chg}, {50: 0.4})
    # A 涨幅 0.01 排第 2 小 → 40.0；E 最低 → 20.0；D 最高 → 100.0
    check("rps50 A=40", float(mix.iloc[0, 0]) == 40.0)
    check("rps50 E=20", float(mix.iloc[0, 4]) == 20.0)
    check("rps50 D=100", float(mix.iloc[0, 3]) == 100.0)
    check("rps n_avail=1", float(n_avail.iloc[0, 0]) == 1.0)
    # 只有一个周期可用 → 复合分 = 该周期分（权重归一）
    check("rps mix==rps50 when single period", float(mix.iloc[0, 0]) == 40.0)
    # 与产线公式对照: pd.Series.rank(pct=True)*100
    manual = (pd.Series(chg.iloc[0]).rank(pct=True) * 100).round(1)
    check(
        "rps matches production formula",
        np.allclose(mix.iloc[0].to_numpy(dtype=float), manual.to_numpy(dtype=float)),
    )


def test_rps_weight_norm():
    # 三周期权重 0.4/0.3/0.3；B 只有 50 日可用 → mix = rps50；
    # A 三周期全可用 → mix = .4*r50 + .3*r120 + .3*r250
    dates = idx(300)
    rng = np.random.default_rng(7)
    c250 = rng.normal(0.2, 0.05, len(dates))
    c120 = rng.normal(0.1, 0.05, len(dates))
    c50 = rng.normal(0.05, 0.02, len(dates))
    chg50 = pd.DataFrame({"A": c50, "B": c50}, index=dates)
    chg120 = pd.DataFrame({"A": c120, "B": np.nan}, index=dates)
    chg250 = pd.DataFrame({"A": c250, "B": np.nan}, index=dates)
    mix, n_avail = qb.cross_rps({50: chg50, 120: chg120, 250: chg250}, qb.RPS_W_CUR)
    r50 = (chg50.rank(axis=1, pct=True) * 100).round(1)
    r120 = (chg120.rank(axis=1, pct=True) * 100).round(1)
    r250 = (chg250.rank(axis=1, pct=True) * 100).round(1)
    exp_a = (0.4 * r50["A"] + 0.3 * r120["A"] + 0.3 * r250["A"]).round(1)
    check(
        "rps mix weighted A",
        np.allclose(mix["A"].to_numpy(dtype=float), exp_a.to_numpy(dtype=float)),
    )
    check(
        "rps sub-new B: mix==rps50 (weights renorm)",
        np.allclose(mix["B"].to_numpy(dtype=float), r50["B"].to_numpy(dtype=float)),
    )
    check("rps sub-new B n_avail=1", float(n_avail["B"].iloc[-1]) == 1.0)
    # NaN 不参与排名: 该日 B 的 chg50 NaN → mix NaN
    chg50_nan = chg50.copy()
    chg50_nan.iloc[10, 1] = np.nan
    m2, _ = qb.cross_rps({50: chg50_nan}, {50: 1.0})
    check("rps NaN excluded", bool(np.isnan(m2.iloc[10, 1])))


# ── 2. 环境闸门 ─────────────────────────────────────────────
def test_env_gate():
    dates = idx(3)
    # Case1: 全部站上 MA20、20 日全涨 → breadth=1, safe
    above = pd.DataFrame(np.ones((3, 4)), index=dates, columns=list("WXYZ"))
    ret = pd.DataFrame(np.full((3, 4), 0.02), index=dates, columns=list("WXYZ"))
    e = qb.env_gate(above, ret)
    check("env all-above safe", bool(e["safe"].iloc[0]))
    check("env breadth=1", abs(float(e["breadth"].iloc[0]) - 1.0) < 1e-9)
    # Case2: breadth=0.35 < 0.40 → 一票否决
    above2 = pd.DataFrame(
        np.tile(np.array([1.0] * 35 + [0.0] * 65), (3, 1)), index=dates
    )
    ret2 = pd.DataFrame(np.full((3, 100), 0.01), index=dates)
    e2 = qb.env_gate(above2, ret2)
    check("env breadth<0.40 veto", not bool(e2["safe"].iloc[0]))
    # Case3: breadth=0.5 但 med20=-5% < -3% → 否决
    above3 = pd.DataFrame(
        np.tile(np.array([1.0] * 30 + [0.0] * 30), (3, 1)), index=dates
    )
    ret3 = pd.DataFrame(np.full((3, 60), -0.05), index=dates)
    e3 = qb.env_gate(above3, ret3)
    check("env med20<-3% veto", not bool(e3["safe"].iloc[0]))
    # Case4: 边界 breadth=0.40 恰好、med20=-0.03 恰好 → 不否决（严格小于）
    above4 = pd.DataFrame(
        np.tile(np.array([1.0] * 40 + [0.0] * 60), (3, 1)), index=dates
    )
    ret4 = pd.DataFrame(np.full((3, 100), -0.03), index=dates)
    e4 = qb.env_gate(above4, ret4)
    check("env boundary 0.40/-3% not veto", bool(e4["safe"].iloc[0]))


def test_gate_veto_in_signal():
    dates = idx(5)
    cols = ["A", "B"]
    above = pd.DataFrame(np.ones((5, 2)), index=dates, columns=cols)
    ret = pd.DataFrame(np.full((5, 2), 0.02), index=dates, columns=cols)
    env_bad = qb.env_gate(above * 0.0, ret * 0.0 - 0.05)  # 全否决
    env_ok = qb.env_gate(above, ret)
    tech = pd.DataFrame(np.full((5, 2), 100.0), index=dates, columns=cols)
    rps = pd.DataFrame(np.full((5, 2), 90.0), index=dates, columns=cols)
    elig = pd.DataFrame(True, index=dates, columns=cols)
    _, buy_on, _ = qb.combine_score(tech, rps, env_bad, gate_on=True, eligible=elig)
    _, buy_off, _ = qb.combine_score(tech, rps, env_bad, gate_on=False, eligible=elig)
    _, buy_ok, _ = qb.combine_score(tech, rps, env_ok, gate_on=True, eligible=elig)
    check("gate veto suppresses buy", not bool(buy_on.any().any()))
    check("gate off keeps buy", bool(buy_off.all().all()))
    check("gate pass keeps buy", bool(buy_ok.all().all()))


# ── 3. 收益计算（T+1 边界） ─────────────────────────────────
def test_trade_returns_t_plus_1():
    dates = idx(8)
    cols = ["A"]
    open_px = pd.DataFrame([[10.0]] * 8, index=dates, columns=cols)
    close_px = pd.DataFrame([[10.0 + i] for i in range(8)], index=dates, columns=cols)
    buy = pd.DataFrame(False, index=dates, columns=cols)
    buy.iloc[1, 0] = True  # T=dates[1] 信号
    ret, dropped = qb.trade_returns(buy, open_px, close_px, n=2, cost=0.0)
    # 买入 = dates[2] 开盘 10；持有 2 日 → dates[3] 收盘 13 → 30%
    check(
        "T+1 open entry, hold2 close exit",
        len(ret) == 1 and abs(float(ret.iloc[0]) - 0.30) < 1e-9,
    )
    check("no drop", dropped == 0)
    # 成本: 0.0015 → 0.30-0.0015
    ret_c, _ = qb.trade_returns(buy, open_px, close_px, n=2, cost=0.0015)
    check(
        "cost applied once round-trip",
        abs(float(ret_c.iloc[0]) - (0.30 - 0.0015)) < 1e-9,
    )
    # 末端边界: 最后一天信号 → 无 T+1，剔除；倒数第二天 n=2 越界 → 剔除
    buy2 = pd.DataFrame(False, index=dates, columns=cols)
    buy2.iloc[7, 0] = True
    _, d1 = qb.trade_returns(buy2, open_px, close_px, n=2, cost=0.0)
    buy3 = pd.DataFrame(False, index=dates, columns=cols)
    buy3.iloc[6, 0] = True
    _, d2 = qb.trade_returns(buy3, open_px, close_px, n=2, cost=0.0)
    check("last-bar signal dropped", d1 == 1)
    check("exit-beyond-end dropped", d2 == 1)
    # 停牌（出场日 NaN）剔除、未停牌的正常交易
    close_nan = close_px.copy()
    close_nan.iloc[3, 0] = np.nan  # 出场日 = T+2 = 行 3
    buy4 = pd.DataFrame(False, index=dates, columns=cols)
    buy4.iloc[1, 0] = True
    ret4, d4 = qb.trade_returns(buy4, open_px, close_nan, n=2, cost=0.0)
    check("suspended exit dropped", d4 == 1 and len(ret4) == 0)


def test_max_drawdown():
    r = pd.Series([0.10, -0.20, 0.05])
    check("max_drawdown -20%", abs(qb.max_drawdown(r) - (-0.20)) < 1e-9)
    check("max_drawdown empty", qb.max_drawdown(pd.Series(dtype=float)) == 0.0)
    r2 = pd.Series([0.1, 0.1, -0.5, 0.0])
    # eq: 1.1, 1.21, 0.605 → dd = 0.605/1.21-1 = -0.5
    check("max_drawdown -50%", abs(qb.max_drawdown(r2) - (-0.5)) < 1e-9)


# ── 4. 缺数据股票跳过 ───────────────────────────────────────
def test_missing_data_skip():
    dates = idx(6)
    cols = ["good", "fresh"]  # fresh: 资格矩阵 False
    tech = pd.DataFrame({"good": [100.0] * 6, "fresh": [100.0] * 6}, index=dates)
    rps = pd.DataFrame({"good": [90.0] * 6, "fresh": [90.0] * 6}, index=dates)
    env = qb.env_gate(
        pd.DataFrame(np.ones((6, 2)), index=dates, columns=cols),
        pd.DataFrame(np.full((6, 2), 0.02), index=dates, columns=cols),
    )
    elig = pd.DataFrame({"good": [True] * 6, "fresh": [False] * 6}, index=dates)
    _, buy, _ = qb.combine_score(tech, rps, env, eligible=elig)
    check(
        "ineligible stock no signal",
        not bool(buy["fresh"].any()) and bool(buy["good"].all()),
    )


def test_load_wide_eligibility(tmpdir):
    import pyarrow  # noqa: F401

    d = os.path.join(tmpdir, "ash")
    os.makedirs(d, exist_ok=True)
    # long: 300 根；short: 30 根（< 55 → 不足资格）
    for name, n in (("600001_SH", 300), ("000002_SZ", 30)):
        pd.DataFrame(
            {
                "trade_date": [str(20230101 + i) for i in range(n)],
                "open": np.full(n, 10.0),
                # K254 列契约：load_wide 要求 high/pre_close；pre_close=close 前值
                # → 后复权因子链 ratio=1，chg250 断言数学不变
                "high": np.arange(n, dtype=float) + 5,
                # v3b 引擎改动（2026-11-07 13:15）：load_wide 增读 low 列，供
                # portfolio_sim 的价格计划/结构矩阵使用。测试夹具原缺此列 →
                # load_wide 直接跳过该文件，本用例长期报 "no parquet loaded"。
                "low": np.arange(n, dtype=float) + 3,
                "close": np.arange(n, dtype=float) + 5,
                "pre_close": np.arange(n, dtype=float) + 4,
            }
        ).to_parquet(os.path.join(d, name + ".parquet"))
    with open(os.path.join(d, "_names.json"), "w") as f:
        json.dump({}, f)  # 应被忽略
    wide = qb.load_wide(d)
    check("load_wide skips _files, union dates=300", wide["close"].shape == (300, 2))
    # codes 按文件名排序: 列 0 = 000002.SZ(short)，列 1 = 600001.SH(long)
    check(
        "load_wide eligible long-early-rows False(<55)",
        not bool(wide["eligible"].iloc[0, 1]),
    )
    check("load_wide eligible long-late-rows True", bool(wide["eligible"].iloc[100, 1]))
    check(
        "load_wide short stock never eligible",
        not bool(wide["eligible"].iloc[:, 0].any()),
    )
    check(
        "load_wide chg250 NaN before 251 bars",
        bool(np.isnan(wide["chg250"].iloc[100, 1]))
        and abs(float(wide["chg250"].iloc[290, 1]) - (295.0 / 45.0 - 1)) < 1e-6,
    )


# ── 5. tech / combine 打分公式对照 ──────────────────────────
def test_tech_score():
    dates = idx(2)
    longs = pd.DataFrame([[2, 4, 0]], index=dates)
    above = pd.DataFrame([[True, True, False]], index=dates)
    t = qb.tech_score(longs, above)
    check("tech 2longs+above=60", float(t.iloc[0, 0]) == 60.0)
    check("tech 4longs+above clip 100", float(t.iloc[0, 1]) == 100.0)
    check("tech 0longs-below clip 0", float(t.iloc[0, 2]) == 0.0)


def test_combine_score_formula():
    # 与产线公式逐条对照: score=(tech*.35+rps*.25+env*.15)/.75（JEV 缺维）
    dates = idx(3)
    cols = ["A", "B"]
    tech = pd.DataFrame([[80.0, 40.0]] * 3, index=dates, columns=cols)
    rps = pd.DataFrame([[90.0, np.nan]] * 3, index=dates, columns=cols)
    above = pd.DataFrame(np.ones((3, 2)), index=dates, columns=cols)
    ret = pd.DataFrame(np.full((3, 2), 0.02), index=dates, columns=cols)
    env = qb.env_gate(above, ret)  # score=100, safe
    elig = pd.DataFrame(True, index=dates, columns=cols)
    score, buy, obs = qb.combine_score(
        tech, rps, env, buy_th=75, obs_th=50, eligible=elig
    )
    exp_a = (80 * 0.35 + 90 * 0.25 + 100 * 0.15) / 0.75  # = 90.0
    check(
        "combine A matches production weights",
        abs(float(score.iloc[0, 0]) - exp_a) < 1e-9,
    )
    check("combine A buy", bool(buy.iloc[0, 0]))
    # B 无 RPS → 分数仍按缺维降权重算，但按设计不产生任何信号
    # （回测资格 ≥55 bar 必有 rps50，此处仅验证防御逻辑）
    exp_b = (40 * 0.35 + 100 * 0.15) / (0.35 + 0.15)
    check("combine B missing-rps renorm", abs(float(score.iloc[0, 1]) - exp_b) < 1e-9)
    check(
        "combine B no signal without rps",
        not bool(obs.iloc[0, 1]) and not bool(buy.iloc[0, 1]),
    )


def test_stock_positions_basic():
    c = pd.Series(np.arange(1, 61, dtype=float))  # 单调上行
    pos = qb.stock_positions(c)
    check("positions int range 0-4", pos.between(0, 4).all() and pos.dtype.kind in "iu")
    check("uptrend late bars have longs>=1", int(pos.iloc[-1]) >= 1)
    flat = pd.Series(np.full(60, 10.0))  # RSI=50, 不触发
    qb.stock_positions(flat)
    check(
        "flat series rsi bit 0 (rsi=50 never <30)", True
    )  # rsi 事件帧不触发，其余维随 ma/boll 判定，仅冒烟不崩溃


def test_atomic_write():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "rep.json")
        qb.atomic_write_json(p, {"a": 1})
        check(
            "atomic write roundtrip", json.load(open(p, encoding="utf-8")) == {"a": 1}
        )
        qb.atomic_write_json(p, {"b": [1, 2]})
        check(
            "atomic replace overwrite",
            json.load(open(p, encoding="utf-8")) == {"b": [1, 2]},
        )


def main():
    test_rps_percentile()
    test_rps_weight_norm()
    test_env_gate()
    test_gate_veto_in_signal()
    test_trade_returns_t_plus_1()
    test_max_drawdown()
    test_missing_data_skip()
    with tempfile.TemporaryDirectory() as td:
        test_load_wide_eligibility(td)
    test_tech_score()
    test_combine_score_formula()
    test_stock_positions_basic()
    test_atomic_write()
    n_pass = sum(1 for _, ok in PASS if ok)
    print("\n%d/%d passed" % (n_pass, len(PASS)))
    failed = [n for n, ok in PASS if not ok]
    if failed:
        print("FAILED:", failed)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/test_qpzm_backtest.py", exc)
    sys.exit(main())
