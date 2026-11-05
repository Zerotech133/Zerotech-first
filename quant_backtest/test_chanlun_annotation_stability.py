# -*- coding: utf-8 -*-
"""chanlun_annotation_stability 单测：存活判定纯函数 + 记账 + 半衰期/判定。

覆盖任务书点名的三条边界：
  1) 同一笔（端点完全相同）判存活；
  2) 端点漂移（e_dt 变了一天）判不存活，且末笔/非末笔分类正确；
  3) 中枢容差边界：相对差 1e-10 存活、1e-6 不存活、恰好 1e-9 存活。
另加：信号 (type, dt) 身份、半衰期与 PASS/WARN 判定。
全部合成数据，不读 data/、不跑 analyze（模块级无重依赖导入）。
运行：python -m pytest test_chanlun_annotation_stability.py -q
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chanlun_annotation_stability as cas  # noqa: E402


def _bi(s_dt, e_dt, s_price=10.0, e_price=12.0):
    return {"s_dt": s_dt, "e_dt": e_dt, "s_price": s_price, "e_price": e_price}


def _snap(bis=(), piv=(), sigA=(), sigB=()):
    """按 snapshot() 的存储形态造快照：笔存 4 元组，中枢存 (zg, zd)。"""
    return {
        "bi": [(b["s_dt"], b["e_dt"], b["s_price"], b["e_price"]) for b in bis],
        "piv": [tuple(p) for p in piv],
        "sigA": set(sigA),
        "sigB": set(sigB),
    }


# ── 1. 笔：同一对端点 ────────────────────────────────────────────────
def test_bi_same_endpoints_survives():
    b = _bi("2026-01-05", "2026-01-20")
    later = {cas.bi_key(x) for x in [_bi("2026-01-05", "2026-01-20"), _bi("2026-01-20", "2026-02-02")]}
    assert cas.bi_key(b) == ("2026-01-05", "2026-01-20")
    assert cas.bi_survives(cas.bi_key(b), later) is True


# ── 2. 笔：端点漂移（同起点、终点被后续分型改写）→ 不存活 ──────────
def test_bi_endpoint_drift_not_survives():
    old = _bi("2026-01-05", "2026-01-20")
    drifted = [_bi("2026-01-05", "2026-01-21")]  # 终点顺延一天，起点未变
    later = {cas.bi_key(x) for x in drifted}
    assert cas.bi_survives(cas.bi_key(old), later) is False
    # 端点价被改写但日期对不变 → 键仍算存活（口径：身份=日期对）
    repriced = [_bi("2026-01-05", "2026-01-20", e_price=12.5)]
    assert cas.bi_survives(cas.bi_key(old), {cas.bi_key(x) for x in repriced}) is True


def test_observe_splits_tail_from_inner():
    """非末笔理论上恒存活；末笔漂移即计入失败。"""
    s0 = _snap(bis=[_bi("d1", "d2"), _bi("d2", "d3"), _bi("d3", "d4")])
    s1 = _snap(bis=[_bi("d1", "d2"), _bi("d2", "d3"), _bi("d3", "d5")])  # 末笔顺延
    acc = {}
    cas._observe(acc, s0, s1, 1)
    assert acc["bi"][1] == [2, 3]
    assert acc["bi_inner"][1] == [2, 2]
    assert acc["bi_tail"][1] == [0, 1]


# ── 3. 中枢：相对容差边界 ────────────────────────────────────────────
def test_pivot_tolerance_boundary():
    zg, zd = 20.0, 18.0
    # 相对差 1e-10 << 1e-9 → 存活
    assert cas.pivot_close(zg, zd, zg * (1 + 1e-10), zd * (1 + 1e-10)) is True
    # 1e-9 这条线本身在二进制浮点里不可精确表示（20*(1+1e-9)-20 = 2.0000002e-8
    # 已略超 1e-9*20），故边界两侧各取 50% 余地来钉住阈值语义：<= 存活。
    assert cas.pivot_close(zg, zd, zg * (1 + 0.5e-9), zd) is True    # 内侧
    assert cas.pivot_close(zg, zd, zg * (1 + 1.5e-9), zd) is False   # 外侧
    # 相对差 1e-6 → 不存活
    assert cas.pivot_close(zg, zd, zg * (1 + 1e-6), zd) is False
    # 只有 ZD 漂移也算不存活
    assert cas.pivot_close(zg, zd, zg, zd * (1 + 1e-6)) is False
    # 两数皆 0 时退化为严格相等（不能因 scale=0 就放行）
    assert cas.pivot_close(0.0, 0.0, 0.0, 0.0) is True
    assert cas.pivot_close(0.0, 0.0, 1e-12, 0.0) is False


def test_pivot_survives_scan_and_observe():
    pv = (20.0, 18.0)
    assert cas.pivot_survives(pv, [(19.0, 17.0), (20.0 * (1 + 1e-12), 18.0)]) is True
    assert cas.pivot_survives(pv, [(19.0, 17.0), (20.5, 18.0)]) is False
    acc = {}
    cas._observe(acc, _snap(piv=[pv]), _snap(piv=[pv]), 5)
    assert acc["pivot"][5] == [1, 1]


# ── 4. 信号：(type, dt) 身份 ─────────────────────────────────────────
def test_signal_identity():
    assert cas.signal_key({"type": "3买", "dt": "2026-03-02"}) == ("2026-03-02", "3买")
    acc = {}
    s0 = _snap(sigB=[("2026-03-02", "3买"), ("2026-03-02", "1卖")])
    s1 = _snap(sigB=[("2026-03-02", "3买"), ("2026-03-03", "1卖")])  # 1卖 的 dt 变了
    cas._observe(acc, s0, s1, 1)
    assert acc["signal"][1] == [1, 2]


# ── 5. 半衰期 / 判定 ─────────────────────────────────────────────────
def test_half_life_and_verdict():
    assert cas.half_life({1: 0.99, 5: 0.96, 20: 0.70}) is None
    assert cas.half_life({1: 0.99, 5: 0.40, 20: 0.20}) == 5
    assert cas.half_life({1: 0.30}) == 1
    limits = {"bi": {1: 0.980, 5: 0.955, 20: 0.911}}
    assert cas.verdict("bi", {1: 0.999, 5: 0.993, 20: 0.990}, limits) == "PASS"
    assert cas.verdict("bi", {1: 0.999, 5: 0.900, 20: 0.990}, limits) == "WARN"
    assert cas.verdict("bi_tail", {1: 0.5}, limits) is None  # 诊断项不判定
