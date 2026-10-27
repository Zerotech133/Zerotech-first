# -*- coding: utf-8 -*-
"""[20261014_m metrics-dev] P1 判据度量批单测（合成数据，只读产线）。

覆盖四条核心路径：
  1. linkage 滚动命中率计算（成熟命中/未命中/episode 去重/未到期 pending）
  2. 起算日推算（trading_days_between 交易日计数 + ninedim 计时）
  3. upfrac 补齐（rps_resonance.market_env 落字段 + gate_shadow_collect 透传）
  4. 缺失 fail-open（缺文件/坏数据 → 待首采，不抛异常不炸周报）
"""

import json
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shadow_weekly_report as swr


# ---------- 合成数据工具 ----------
def mk_calendar(n=20, start=20260901):
    """连续 n 个交易日日历（YYYYMMDD 字符串，数字递增即可测逻辑）。"""
    return [str(start + i) for i in range(n)]


def bars_from(cal, base=10.0, step=0.1):
    """沿日历生成 [(d8, close)]：step>0 单调涨，<0 单调跌。"""
    return [(d, base * (1 + step * i)) for i, d in enumerate(cal)]


# ---------- 路径1：linkage 命中率计算 ----------
def test_hitrate_matured_hit_and_miss():
    cal = mk_calendar(20)
    bars_up = bars_from(cal, step=0.10)  # A：每天 +10%
    bars_dn = bars_from(cal, step=-0.05)  # B：每天 -5%
    cache = {"600001.SH": bars_up, "600002.SH": bars_dn}
    rows = [
        {
            "code": "600001.SH",
            "signal": "1",
            "ref_date": "2026-09-01",
            "block": {"code": "880336"},
        },
        {
            "code": "600002.SH",
            "signal": "2",
            "ref_date": "2026-09-01",
            "block": {"code": "880336"},
        },
    ]
    hr = swr.calc_linkage_hitrate(rows, cal, lambda c: cache.get(c, []), horizons=(5,))
    h = hr["h5"]
    assert h["episodes"] == 2
    assert h["matured"] == 2
    assert h["hits_abs"] == 1  # 只有上涨票命中
    assert h["rate_abs"] == pytest.approx(0.5)
    # 目标日 = 日历第 6 日（20260906）
    assert h["pending"] == 0


def test_hitrate_episode_dedup_and_pending():
    cal = mk_calendar(20)
    bars = bars_from(cal, step=0.10)
    # 同票同信号连续 3 日触发 → episode 去重只计首日
    rows = [
        {"code": "600001.SH", "signal": "1", "ref_date": d, "block": {"code": "880336"}}
        for d in ("2026-09-01", "2026-09-02", "2026-09-03")
    ]
    hr = swr.calc_linkage_hitrate(rows, cal, lambda c: bars, horizons=(5,))
    h = hr["h5"]
    assert h["episodes"] == 1
    assert h["matured"] == 1
    # 窗口超出日历末端 → pending（未到期）
    rows2 = [
        {
            "code": "600001.SH",
            "signal": "1",
            "ref_date": "2026-09-18",
            "block": {"code": "880336"},
        }
    ]
    h2 = swr.calc_linkage_hitrate(rows2, cal, lambda c: bars, horizons=(5,))["h5"]
    assert h2["pending"] == 1
    assert h2["matured"] == 0


# ---------- 路径2：起算日推算 ----------
def test_trading_days_between():
    cal = mk_calendar(10)  # 20260901..20260910
    assert swr.trading_days_between("20260901", "20260910", cal) == 9
    assert swr.trading_days_between("20260901", "20260901", cal) == 0
    assert swr.trading_days_between("20260830", "20260903", cal) == 3
    assert swr.trading_days_between("20260910", "20260901", cal) == 0
    # 起算日不在日历中（如周末锚）同样成立
    assert swr.trading_days_between("20260831", "20260902", cal) == 2


def test_ninedim_timer_starts_on_first_alert():
    cal = mk_calendar(30)
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "h.jsonl")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(
                json.dumps({"dt": "2026-09-05", "code": "1", "pool_type": "buy_legacy"})
                + "\n"
            )
            f.write(
                json.dumps({"dt": "2026-09-10", "code": "2", "pool_type": "alert"})
                + "\n"
            )
            f.write(
                json.dumps({"dt": "2026-09-11", "code": "3", "pool_type": "alert"})
                + "\n"
            )
        r = swr.crit_ninedim_timer(hist_path=fp, calendar=cal)
        assert r["state"] == "计时中"
        assert r["first_alert_date"] == "20260910"
        # 固定 today 锚不可注入（函数用 date.today()），此处只验首条取 alert 最早


# ---------- 路径3：upfrac 补齐 ----------
def test_market_env_persists_upfrac():
    import numpy as np
    import rps_resonance as rr

    # 30 只票 × 25 日收盘（np.array，与产线 closes 同型）：一半持续涨
    closes = {}
    for i in range(30):
        step = 1.01 if i % 2 == 0 else 0.995
        closes["c%d" % i] = np.array([10.0 * (step**j) for j in range(25)])
    env = rr.market_env(closes)
    assert "upfrac" in env
    assert env["upfrac"] is not None
    assert 0.0 <= env["upfrac"] <= 1.0
    # upfrac=上涨占比：15 只涨 / 30
    assert env["upfrac"] == pytest.approx(0.5, abs=0.01)
    # 数据缺失路径：样本 < POOL_GATE_MIN → upfrac 显式 None（形态一致）
    env2 = rr.market_env({"a": np.array([1.0] * 25), "b": np.array([1.0] * 25)})
    assert env2["upfrac"] is None


def test_gate_shadow_collect_qpzm_upfrac_passthrough():
    import gate_shadow_collect as gsc

    with tempfile.TemporaryDirectory() as td:
        snap = os.path.join(td, "rps_latest.json")
        with open(snap, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "latest": "20261013",
                    "envs": [
                        {
                            "label": "A股全市场(10只)",
                            "breadth": 0.5,
                            "ret20": -0.01,
                            "upfrac": 0.42,
                        }
                    ],
                    "res": [{"code": "600001.SH", "market": "CN", "action": "买入"}],
                },
                f,
            )
        date, codes, br, med, upf, src = gsc.read_qpzm(snap)
        assert upf == pytest.approx(0.42)
        rows = gsc._mk_rows(date, "qpzm", 1, codes, br, med, src, upfrac=upf)
        assert all(r["upfrac"] == pytest.approx(0.42) for r in rows)
        # 旧快照无 upfrac 字段 → None 透传，不阻塞
        with open(snap, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "latest": "20261013",
                    "envs": [
                        {"label": "A股全市场(10只)", "breadth": 0.5, "ret20": -0.01}
                    ],
                    "res": [],
                },
                f,
            )
        _, _, _, _, upf2, _ = gsc.read_qpzm(snap)
        assert upf2 is None
        # 坏值 fail-open：字符串 → None
        with open(snap, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "latest": "20261013",
                    "envs": [
                        {
                            "label": "A股全市场(10只)",
                            "breadth": 0.5,
                            "ret20": -0.01,
                            "upfrac": "bad",
                        }
                    ],
                    "res": [],
                },
                f,
            )
        _, _, _, _, upf3, _ = gsc.read_qpzm(snap)
        assert upf3 is None


# ---------- 路径4：缺失 fail-open ----------
def test_criteria_fail_open_on_missing_sources():
    with tempfile.TemporaryDirectory() as td:
        # k33：history 不存在 → 待首采
        r1 = swr.crit_k33_recon(hist_path=os.path.join(td, "no.jsonl"))
        assert r1["state"] == "待首采" and r1["n"] == 0
        # strong：快照缺失 → 待首采
        r2 = swr.crit_strong_coverage(os.path.join(td, "no.json"))
        assert r2["state"] == "待首采"
        # qpzm：三档档不存在 → 待首采
        r3 = swr.crit_qpzm_gate_counts(os.path.join(td, "no.jsonl"))
        assert r3["state"] == "待首采"
        # ninedim：空库 → 未启动（不抛）
        r4 = swr.crit_ninedim_timer(
            hist_path=os.path.join(td, "no.jsonl"), calendar=mk_calendar(5)
        )
        assert r4["state"] == "未启动"
        # 坏行 jsonl → 不抛，按可得行计
        fp = os.path.join(td, "g3.jsonl")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("not-json\n")
            f.write(
                json.dumps(
                    {
                        "strategy": "qd",
                        "theta": 40,
                        "date": "20261012",
                        "source_status": "ok",
                    }
                )
                + "\n"
            )
        r5 = swr.crit_qpzm_gate_counts(fp)
        assert r5["rows_total"] == 1
        assert r5["per_strategy_theta"] == {"qd|θ40": 1}


def test_metrics_criteria_single_line_failure_isolated(monkeypatch):
    """单判据内部炸 → 该项标 error，其余判据照常出数。"""

    def boom():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(swr, "crit_k33_recon", boom)
    crit = swr.metrics_criteria(None, None)
    assert crit["c1_k33_recon"]["state"] == "error"
    assert "RuntimeError" in crit["c1_k33_recon"]["note"]
    assert crit["c5_qpzm_gate_counts"]["state"] in ("待首采", "采集中")
    assert crit["c6_g3_progress"]["state"] in ("计时中", "error")


def test_linkage_hitrate_caliber_excess():
    """口径B：个股跑输板块 → excess 不命中，绝对口径仍命中。"""
    cal = mk_calendar(20)
    stock = bars_from(cal, base=10.0, step=0.01)  # 涨得慢
    block = bars_from(cal, base=10.0, step=0.05)  # 板块涨得快
    rows = [
        {
            "code": "600001.SH",
            "signal": "1",
            "ref_date": "2026-09-01",
            "block": {"code": "880336"},
        }
    ]
    hr = swr.calc_linkage_hitrate(
        rows,
        cal,
        lambda c: stock,
        block_fn=lambda b: block if b == "880336" else None,
        horizons=(5,),
    )
    h = hr["h5"]
    assert h["hits_abs"] == 1  # 绝对口径命中
    assert h["ex_n"] == 1
    assert h["hits_ex"] == 0  # 跑输板块 → 口径B不命中
