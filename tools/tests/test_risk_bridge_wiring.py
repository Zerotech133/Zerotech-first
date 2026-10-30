# -*- coding: utf-8 -*-
"""JEVALERTB 回归：小时轮 wrapper 必须接线风险扫描且仅写 outbox（不外发）。"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_hourly_wrapper_wires_outbox_only():
    text = (REPO / "jev-eval" / "jev_backfill_task.ps1").read_text(encoding="utf-8")
    assert "jev_risk_alert.py" in text
    assert "--emit-outbox" in text
    assert "--emit-flag" not in text          # B 方案：不外发飞书
