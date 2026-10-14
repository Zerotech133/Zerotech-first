# -*- coding: utf-8 -*-
"""env_gate 切 gatekeeper 集成测试（K-STOCK-401，合成数据零湖依赖）。

覆盖：委托路径判定正确性 / QPZM_GATEKEEPER=0 回退 / NaN 行旧语义放行 /
账本只落最新锚日+去重（真实实现+ledger_fp 注入 tmp 账本）。
"""
import importlib
import json
import os
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GK_DIR = os.path.abspath(os.path.join(HERE, ".."))
PROJ = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for p in (GK_DIR, PROJ):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("pandas")


def _make_wide(n_days=30):
    """合成 above/ret20 wide：A 上行(above=1)、B 下行(above=0)、
    C 前 10 日缺维、D 全 NaN。ret20 同构（med20 末日=+0.05 放行域）。"""
    dates = [20260001 + i for i in range(n_days)]
    cols = {
        "A": pd.Series([1.0] * n_days, index=dates),
        "B": pd.Series([0.0] * n_days, index=dates),
        "C": pd.Series([None] * 10 + [1.0] * (n_days - 10), index=dates,
                       dtype=float),
        "D": pd.Series([None] * n_days, index=dates, dtype=float),
    }
    above = pd.DataFrame(cols)
    rcols = {
        "A": pd.Series([0.05] * n_days, index=dates),
        "B": pd.Series([-0.05] * n_days, index=dates),
        "C": pd.Series([None] * 10 + [0.05] * (n_days - 10), index=dates,
                       dtype=float),
        "D": pd.Series([None] * n_days, index=dates, dtype=float),
    }
    return above, pd.DataFrame(rcols)


@pytest.fixture()
def qb(tmp_path, monkeypatch):
    """隔离 import qpzm_backtest：账本指 tmp（防合成数据污染产线账本，
    2026-10-15 判例：合成日期 20260030 曾漏进 data/gate_decisions.jsonl），
    teardown reload 还原（防模块污染判例）。"""
    monkeypatch.setenv("QPZM_GATEKEEPER", "1")
    monkeypatch.setattr("ledger.DEFAULT_LEDGER_FP",
                        str(tmp_path / "env_gate_test.jsonl"))
    import qpzm_backtest as m
    importlib.reload(m)
    yield m
    importlib.reload(m)


def test_delegate_safe_verdict(qb):
    """合成 wide：末日 breadth=2/3（round3=0.667）、med20=0.05 → 放行。"""
    above, ret20 = _make_wide()
    env = qb.env_gate(above, ret20)
    last = env.iloc[-1]
    assert bool(last["safe"]) is True
    # env["breadth"] 是 round(3) 展示列：2/3 → 0.667
    assert abs(float(last["breadth"]) - 0.667) < 1e-9


def test_delegate_kill_by_med20(qb):
    """med20<-3% 杀伤分支经 policy 判定生效。"""
    above, ret20 = _make_wide()
    ret20.loc[:, ["A", "C"]] = -0.05              # 全体 med20 杀
    env = qb.env_gate(above, ret20)
    assert bool(env.iloc[-1]["safe"]) is False


def test_nan_rows_legacy_true(qb):
    """breadth 全 NaN 日（无票可评）：safe=True 旧语义放行。"""
    above, ret20 = _make_wide()
    above.iloc[0, :] = None
    ret20.iloc[0, :] = None
    env = qb.env_gate(above, ret20)
    assert bool(env.iloc[0]["safe"]) is True


def test_off_switch_identical(qb, monkeypatch):
    """QPZM_GATEKEEPER=0 回退：safe 与委托路径逐值一致。"""
    above, ret20 = _make_wide()
    env_gk = qb.env_gate(above, ret20)
    monkeypatch.setenv("QPZM_GATEKEEPER", "0")
    env_legacy = qb.env_gate(above, ret20)
    assert env_gk["safe"].equals(env_legacy["safe"])


def test_ledger_anchor_day_and_dedup(qb, tmp_path):
    """真实实现：只落最新锚日 1 行 + 二次调用去重 + 字段齐全。"""
    from ledger import Ledger
    fp = str(tmp_path / "g.jsonl")
    dates = [20260001 + i for i in range(30)]
    breadth = pd.Series([None] * 10 + [0.667] * 20, index=dates, dtype=float)
    med = pd.Series([None] * 10 + [0.05] * 20, index=dates, dtype=float)
    upfrac = pd.Series(0.5, index=dates, dtype=float)
    qb._env_gate_via_gatekeeper(breadth, med, upfrac, ledger_fp=fp)
    s1 = qb._env_gate_via_gatekeeper(breadth, med, upfrac, ledger_fp=fp)
    assert bool(s1.iloc[-1]) is True
    rows1 = Ledger(fp=fp).query("CN")
    assert len(rows1) == 1, "应只落最新锚日 1 行"
    qb._env_gate_via_gatekeeper(breadth, med, upfrac, ledger_fp=fp)
    rows2 = Ledger(fp=fp).query("CN")
    assert len(rows2) == 1, "重跑应去重"
    row = json.loads(json.dumps(rows2[0]))
    assert row["date"] == "20260030"          # 最新有效锚日
    for k in ("market", "date", "safe", "conditions", "features",
              "policy_ver", "policy_sha", "feeder"):
        assert k in row, "缺字段 %s" % k
    assert row["feeder"] == "qpzm_wide_embedded"
