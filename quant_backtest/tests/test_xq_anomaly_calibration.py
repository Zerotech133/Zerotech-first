# -*- coding: utf-8 -*-
"""tests/test_xq_anomaly_calibration.py — 阈值敏感性脚本纯逻辑单测"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tools", "research"))

import xq_anomaly_calibration as cal


def _fake_active():
    """构造假活跃日表：day -> {posts, bear_t}"""
    return {
        "2020-05-10": {"posts": 9, "bear_t": 0},
        "2020-05-12": {"posts": 9, "bear_t": 2},
        "2020-05-13": {"posts": 15, "bear_t": 1},
        "2020-05-16": {"posts": 4, "bear_t": 1},
        "2020-03-02": {"posts": 24, "bear_t": 2},
        "2020-02-26": {"posts": 21, "bear_t": 4},
        "2023-11-11": {"posts": 2, "bear_t": 0},
        "2023-10-27": {"posts": 2, "bear_t": 1},
    }


def test_pick_sample_days_basic():
    days = cal.pick_sample_days(_fake_active(), cap=20)
    assert days, "应选出样本日"
    assert days == sorted(days), "样本日应按日期升序"
    # 种子日窗口必含 2020-05-13
    assert "2020-05-13" in days
    # 活跃日（bear_t>=1）优先入选
    assert "2020-02-26" in days and "2020-03-02" in days
    # 最近活跃日入选
    assert "2023-11-11" in days


def test_pick_sample_days_cap():
    days = cal.pick_sample_days(_fake_active(), cap=3)
    assert len(days) <= 3
    # cap 截断时 bear_t 高者优先保留
    assert "2020-02-26" in days  # bear_t=4 最高


def test_pick_sample_days_seed_window_bounds():
    days = cal.pick_sample_days(_fake_active(), cap=20)
    # 种子窗口 ±3 天：2020-05-10 与 2020-05-16 在窗口内且 active，应入选
    assert "2020-05-10" in days and "2020-05-16" in days
    # 窗口外且无信号的日期不该凭空出现
    assert "2020-06-01" not in days


def test_grid_stats_counts():
    results = {
        (3, 0.6): {"2020-05-13": [("600000", 3, 3, 0)], "2020-05-12": [],
                   "2020-03-02": [("000001", 4, 3, 1), ("000002", 3, 2, 0)]},
        (2, 0.5): {"2020-05-13": [], "2020-05-12": [], "2020-03-02": []},
    }
    day_data = {"2020-05-13": (15, object()), "2020-05-12": (9, object()),
                "2020-03-02": (24, object())}
    stats = cal.grid_stats(results, day_data)
    cur = stats[(3, 0.6)]
    assert cur["total"] == 3
    assert cur["days_hit"] == 2
    assert cur["days_multi"] == 1          # 2020-03-02 两个标的
    assert cur["days_zero"] == 1
    assert cur["avg_n_at_hit"] == (15 + 24) / 2
    assert stats[(2, 0.5)]["total"] == 0 and stats[(2, 0.5)]["days_zero"] == 3


def test_recommendation_paths():
    def _st(total, hit=1, multi=0):
        return {"total": total, "days_hit": hit, "days_multi": multi,
                "days_zero": 3 - hit, "avg_n_at_hit": 20.0 if total else None}

    # 现行零触发且最低格零触发 → 过严（通道空转）
    s1 = {cal.CURRENT: _st(0, 0), (2, 0.5): _st(0, 0), (2, 0.6): _st(0, 0),
          (5, 0.7): _st(0, 0)}
    v, a = cal.make_recommendation(s1)
    assert "过严" in v and "复检" in a
    # 现行零触发、最低格有触发 → 过严，建议下调
    s2 = {cal.CURRENT: _st(0, 0), (2, 0.5): _st(2), (2, 0.6): _st(1),
          (5, 0.7): _st(0, 0)}
    v, a = cal.make_recommendation(s2)
    assert v == "过严" and "下调" in a
    # 现行有触发、最低格零触发 → 合理，维持
    s3 = {cal.CURRENT: _st(3, 2), (2, 0.5): _st(0, 0), (2, 0.6): _st(1),
          (5, 0.7): _st(0, 0)}
    v, a = cal.make_recommendation(s3)
    assert "合理" in v and "维持" in a
    # 最低格不低于现行 → 无区分度
    s4 = {cal.CURRENT: _st(1), (2, 0.5): _st(2), (2, 0.6): _st(2),
          (5, 0.7): _st(0, 0)}
    v, a = cal.make_recommendation(s4)
    assert "无区分度" in v
    # 现行 > 最低格 > 0 → 偏严
    s5 = {cal.CURRENT: _st(5, 2), (2, 0.5): _st(2), (2, 0.6): _st(3),
          (5, 0.7): _st(0, 0)}
    v, a = cal.make_recommendation(s5)
    assert "偏严" in v
