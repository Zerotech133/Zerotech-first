# -*- coding: utf-8 -*-
"""weight_updater 纯函数单测（P4-20261017）。

覆盖：显式反馈打分 / 归一化退化 / EWMA / clamp 归一化往返 /
次日收益（真实 parquet + 人造边界）。PG/文件 IO 不进单测。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_layer.weight_updater import (  # noqa: E402
    fb_score,
    norm01,
    ewma,
    normalize_clamp,
    next_day_return,
)


# ── fb_score ──────────────────────────────────────────────────────────
def test_fb_score_empty_returns_none():
    assert fb_score([], "kg_boost") is None


def test_fb_score_mixed_verdicts():
    rows = [
        {"strategy": "kg_boost", "verdict": "accept"},
        {"strategy": "kg_boost", "verdict": "accept"},
        {"strategy": "kg_boost", "verdict": "reject"},
        {"strategy": "kg_boost", "verdict": "mute"},
        {"strategy": "chan_monitor", "verdict": "reject"},
    ]
    # kg_boost: (+1 +1 -1 +0) / 4 = 0.25
    assert fb_score(rows, "kg_boost") == pytest.approx(0.25)
    # chan_monitor: -1/1 = -1
    assert fb_score(rows, "chan_monitor") == pytest.approx(-1.0)


def test_fb_score_other_strategy_ignored():
    assert fb_score([{"strategy": "x", "verdict": "accept"}], "kg_boost") is None


# ── norm01 ────────────────────────────────────────────────────────────
def test_norm01_basic():
    assert norm01(0.0, 0.0, 1.0) == 0.0
    assert norm01(1.0, 0.0, 1.0) == 1.0
    assert norm01(0.5, 0.0, 1.0) == pytest.approx(0.5)
    assert norm01(-1.0, 0.0, 1.0) == 0.0  # 越下界 clamp
    assert norm01(2.0, 0.0, 1.0) == 1.0  # 越上界 clamp


def test_norm01_degenerate_range_returns_midpoint():
    # 全窗口同值 = 无信息 → 0.5 不偏置
    assert norm01(0.7, 0.7, 0.7) == 0.5


# ── ewma ──────────────────────────────────────────────────────────────
def test_ewma_alpha_blend():
    assert ewma(0.5, 1.0) == pytest.approx(0.3 * 1.0 + 0.7 * 0.5)
    assert ewma(0.5, 0.5) == pytest.approx(0.5)  # 目标=旧值时不动


# ── normalize_clamp ───────────────────────────────────────────────────
def test_normalize_clamp_sums_to_one():
    w = normalize_clamp({"a": 3.0, "b": 1.0})
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["a"] > w["b"]


def test_normalize_clamp_floor_and_ceiling():
    # 极端比例下 clamp [0.1, 0.7] 仍生效
    w = normalize_clamp({"a": 100.0, "b": 0.001})
    assert all(0.1 - 1e-9 <= v <= 0.7 + 1e-9 for v in w.values())


def test_normalize_clamp_all_zero_falls_equal():
    w = normalize_clamp({"a": 0.0, "b": 0.0})
    assert w == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}


def test_normalize_clamp_empty():
    assert normalize_clamp({}) == {}


# ── next_day_return（真实 parquet）────────────────────────────────────
REAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "ashare",
    "000001_SZ.parquet",
)


@pytest.mark.skipif(not os.path.isfile(REAL), reason="无 ashare 数据")
def test_next_day_return_real_file_in_range():
    r = next_day_return("000001.SZ", "20261015")
    assert r is not None
    assert -0.05 <= r <= 0.05


def test_next_day_return_non_a_share_none():
    assert next_day_return("AAPL.US", "20261016") is None
    assert next_day_return("", "20261016") is None


def test_next_day_return_missing_file_none(tmp_path):
    assert next_day_return("999999.SZ", "20261016", ashare_dir=str(tmp_path)) is None


def test_next_day_return_future_date_none(tmp_path):
    # trade_date 晚于全部数据 → nxt 为空
    assert next_day_return("000001.SZ", "29991231") is None
