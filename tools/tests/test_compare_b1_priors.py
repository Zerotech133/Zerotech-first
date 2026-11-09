"""P0/P1/P2 对比器单元测试（合成 JSON，不读真实回测）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import compare_b1_priors as cmp  # noqa: E402


def _m(ann: float, sharpe: float, mdd: float, ann_turn: float = 1.0, cost: float = 0.006) -> dict:
    return {
        "ann_return": ann,
        "ann_vol": 0.05,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "ann_one_way_turnover": ann_turn,
        "total_cost": cost,
    }


def _entry(label: str, days: int, cost_bp: int, metrics: dict, values: list[float]) -> dict:
    return {
        "label": label,
        "days": days,
        "cost_bp": cost_bp,
        "metrics": metrics,
        "daily_returns": {"dates": DATES[: len(values)], "values": values},
    }


def _doc(sharpe: float, ic: float, mdd: float = -0.08, ann: float = 0.02) -> dict:
    doc = {
        "composite": {
            "ic_windows": {
                "dev": {"neutral": {"factor": {"ic": {"mean": ic - 0.002, "t": 2.0}}}},
                "holdout": {"neutral": {"factor": {"ic": {"mean": ic, "t": 3.0}}}},
            }
        },
        "portfolio": [],
    }
    for label, days, cost in (
        ("neut_dev", 60, 30),
        ("neut_holdout", 60, 30),
        ("neut_holdout", 40, 30),
        ("neut_holdout", 20, 30),
        ("neut_holdout", 60, 0),
        ("neut_holdout", 60, 50),
        ("raw_holdout", 60, 30),
    ):
        vals = [0.0002] * len(DATES)
        doc["portfolio"].append(_entry(label, days, cost, _m(ann, sharpe, mdd), vals))
    return doc


# 2025-01-02 至 2026-09-29 的交易日，约 430 天
DATES = [d.strftime("%Y%m%d") for d in pd.bdate_range("2025-01-02", "2026-09-29")]


def test_prereg_judgement_pass_and_fail():
    p0 = _doc(0.30, 0.08, -0.09, 0.015)
    p1 = _doc(0.42, 0.102, -0.10, 0.031)
    p2 = _doc(0.50, 0.105, -0.10, 0.033)
    judgement = cmp.prereg_judgement(p0, p1, p2)
    assert judgement["primary_all_pass"] is True
    assert all(c["pass"] for c in judgement["primary_checks"])
    assert "登记候选" in judgement["verdict"]

    p2_bad = _doc(0.44, 0.103, -0.13, 0.029)
    judgement_bad = cmp.prereg_judgement(p0, p1, p2_bad)
    assert judgement_bad["primary_all_pass"] is False
    assert "未通过" in judgement_bad["verdict"]


def test_rolling_and_yearly_metrics():
    _doc(0.30, 0.08, -0.09, 0.015)
    p1 = _doc(0.42, 0.102, -0.10, 0.031)
    p2 = _doc(0.50, 0.105, -0.10, 0.033)
    rolling = cmp.compare_rolling(p1, p2, window=60, step=20)
    assert rolling["n_windows"] > 0
    assert rolling["p1_wins"] + rolling["p2_wins"] == rolling["n_windows"]
    yearly = cmp.yearly_metrics(p2)
    assert set(yearly) == {"2025", "2026"}
    assert yearly["2025"]["days"] > 100
    assert yearly["2026"]["days"] > 100


def test_markdown_contains_verdict_and_tables():
    p0 = _doc(0.30, 0.08, -0.09, 0.015)
    p1 = _doc(0.42, 0.102, -0.10, 0.031)
    p2 = _doc(0.50, 0.105, -0.10, 0.033)
    judgement = cmp.prereg_judgement(p0, p1, p2)
    rolling = cmp.compare_rolling(p1, p2)
    md = cmp.build_markdown(p0, p1, p2, judgement, rolling)
    assert "预注册判定" in md
    assert "P2 通过预注册" in md
    assert "P2−P1" in md
