# -*- coding: utf-8 -*-
"""候风核心链路单元测试（2026-11-07 补）。

背景：审查发现 portfolio_sim / guardian_agent / _volq_det / _dividend_defense /
_pick_live 这一整条链路，此前**没有任何 test_*.py 引用**（grep 零命中）。
本文件先覆盖"改动过、且出错会静默污染数字或页面"的纯逻辑部分：
  - 页面 JSON 的 NaN 防具（错则整页白屏，且 Python 侧看不出来）
  - _atomic_write 的线程级并发（错则产物互相覆盖/报 PermissionError）
  - 账本重写的坏行容错（错则一行损坏打死整日任务）
  - A6 净值链接（错则 nav 每天被重置，偏差校验失去意义）
  - 手数/权重边界（错则预算凭空增减，收益不可信）

用法：python -m pytest test_houfeng_core.py -q
"""
import json
import os
import sys
import tempfile
import threading

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(ROOT, "_qpzm_rd_20261106", "paper")
for p in (ROOT, PAPER):
    if p not in sys.path:
        sys.path.insert(0, p)

import _json_safe  # noqa: E402
import paper_daily as pdaily  # noqa: E402
import portfolio_sim as ps  # noqa: E402


# ── 1. 页面 JSON 防具 ────────────────────────────────────────────────
def test_json_safe_turns_nan_into_null():
    payload = {"a": float("nan"), "b": [float("inf"), float("-inf")],
               "c": np.float64("nan"), "d": np.int64(7), "e": True, "f": None}
    s = _json_safe.dumps(payload)
    assert "NaN" not in s and "Infinity" not in s
    back = json.loads(s)  # 标准库严格解析
    assert back["a"] is None and back["b"] == [None, None]
    assert back["d"] == 7 and back["e"] is True


def test_raw_json_dumps_is_the_hazard():
    """反证：不加防具时确实会写出浏览器解析不了的裸 NaN。"""
    raw = json.dumps({"x": float("nan")})
    assert "NaN" in raw

    def _boom(c):
        raise ValueError("裸常量 %s 出现在 JSON" % c)

    with pytest.raises(ValueError):
        json.loads(raw, parse_constant=_boom)


def test_json_safe_write_roundtrip(tmp_path):
    p = str(tmp_path / "x.json")
    _json_safe.write(p, {"rows": [{"v": float("nan")}, {"v": 1.5}]})
    d = json.load(open(p, encoding="utf-8"))
    assert d["rows"][0]["v"] is None and d["rows"][1]["v"] == 1.5


# ── 2. 原子写并发 ────────────────────────────────────────────────────
def test_atomic_write_survives_threads():
    d = tempfile.mkdtemp(prefix="aw_")
    out = os.path.join(d, "t.json")
    errs = []

    def worker(i):
        for k in range(15):
            try:
                ps._atomic_write(out, {"i": i, "k": k, "pad": "x" * 2000})
            except Exception as e:  # noqa: BLE001
                errs.append("%s: %s" % (type(e).__name__, e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs[:3]
    json.load(open(out, encoding="utf-8"))          # 终态必须合法
    assert not [x for x in os.listdir(d) if x.endswith(".tmp")]


# ── 3. 账本坏行容错 ──────────────────────────────────────────────────
def test_ledger_keep_others_unit(tmp_path):
    """直接测 v1.0.4 的 helper：坏行原样保留、目标日期行剔除、其余保留。"""
    mod = _load_guardian()
    p = str(tmp_path / "led.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"date": "20260101", "a": 1}\n')
        f.write("this is not json\n")
        f.write('{"date": "20260102", "a": 2}\n')
        f.write('{"date": "20260102", "a": 3}')
    keep, bad = mod._ledger_keep_others(p, "20260102")
    joined = "".join(keep)
    assert bad == 1
    assert '"20260101"' in joined and '"20260102"' not in joined
    assert "this is not json" in joined      # 坏行不丢
    assert joined.endswith("\n")             # 末行补换行


def _load_guardian():
    import importlib.util
    path = os.path.join(PAPER, "guardian_agent_v1.0.4.py")
    spec = importlib.util.spec_from_file_location("guardian_v104", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── 4. A6 净值链接 ───────────────────────────────────────────────────
def test_prev_nav_skips_today_and_bad_rows():
    lines = [{"date": "20260101", "nav": 1.10},
             {"date": "20260102", "nav": "oops"},
             {"date": "20260102", "nav": 1.20}]
    assert pdaily.prev_nav_of(lines, "20260103") == 1.20
    # 当日行不能当作"上一日"
    assert pdaily.prev_nav_of([{"date": "20260103", "nav": 1.30}], "20260103") == 1.0
    assert pdaily.prev_nav_of([], "20260103") == 1.0


def test_daily_ret_guards():
    assert pdaily.daily_ret_of([]) == 0.0
    assert pdaily.daily_ret_of([{"equity": 100}]) == 0.0
    assert pdaily.daily_ret_of([{"equity": 0}, {"equity": 100}]) == 0.0
    assert abs(pdaily.daily_ret_of([{"equity": 100}, {"equity": 110}]) - 0.10) < 1e-12
    # 链式：1.05 × (1+0.1) 而非 1+窗口收益
    assert abs(1.05 * (1 + pdaily.daily_ret_of([{"equity": 100}, {"equity": 110}])) - 1.155) < 1e-12


# ── 5. 权重与手数边界 ────────────────────────────────────────────────
def test_inv_vol_weights_normalized_and_nan_fallback():
    w = ps._inv_vol_weights(np.array([0.01, 0.02, 0.04]))
    assert abs(w.sum() - 1.0) < 1e-12
    assert w[0] > w[1] > w[2]                 # 低波权重高
    w2 = ps._inv_vol_weights(np.array([np.nan, 0.02, 0.04]))
    assert abs(w2.sum() - 1.0) < 1e-12
    w3 = ps._inv_vol_weights(np.array([np.nan, np.nan]))
    assert np.allclose(w3, [0.5, 0.5])
    w4 = ps._inv_vol_weights(np.array([0.0, -1.0]))
    assert np.allclose(w4, [0.5, 0.5])


def test_plan_shares_strict_rounds_to_lot():
    px = np.array([10.0, 100.0])
    half = 0.0015
    budget = np.array([1000.0, 300000.0])
    sh = ps._plan_shares(budget, px, half, lot_fill="strict")
    assert sh[0] == 0                          # 1000 < 1 手成本 1001.5
    assert sh[1] % ps.LOT == 0
    assert sh[1] * px[1] * (1 + half) <= 300000.0


def test_plan_shares_recycle_conserves_budget():
    # 两只 10 元票各 600 元：单只都不够 1 手（成本 1001.5），合起来够 → 回收档救活一只
    px = np.array([10.0, 10.0, 100.0])
    half = 0.0015
    budget = np.array([600.0, 600.0, 300000.0])
    st = {"recycle_rounds": 0, "recycle_pool": 0.0, "recycle_filled": 0}
    strict = ps._plan_shares(budget, px, half, lot_fill="strict")
    recy = ps._plan_shares(budget, px, half, lot_fill="recycle", st=st)
    assert strict[0] == 0 and strict[1] == 0
    assert recy[0] + recy[1] == ps.LOT          # 回收档把两只饿死票救活一只
    spent = float((recy * px * (1 + half)).sum())
    assert spent <= float(budget.sum()) + 1e-6  # 不得凭空多花钱
    assert st["recycle_filled"] >= 1


# ── 6. 选股 JSON 清洗 ────────────────────────────────────────────────
def test_pick_live_jsonable():
    import _pick_live as pl
    out = pl.jsonable({"a": float("nan"), "b": [np.float64(1.5)], "c": np.int64(3)})
    assert out["a"] is None and out["b"] == [1.5] and out["c"] == 3
    assert json.loads(json.dumps(out, allow_nan=False))["a"] is None
