# -*- coding: utf-8 -*-
"""JEVROTATE 回归：JevDriftDaily wrapper 必须接线 rotate_logs（每日轮转）。"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_drift_wrapper_calls_rotate_logs():
    text = (REPO / "jev-eval" / "jev_drift_task.ps1").read_text(encoding="utf-8")
    assert "rotate_logs.py" in text
    assert "--execute" in text and "--keep-days" in text
    assert (REPO / "tools" / "rotate_logs.py").exists()
