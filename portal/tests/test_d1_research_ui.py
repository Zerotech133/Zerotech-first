# -*- coding: utf-8 -*-
"""D1 投研页正式裁决接入测试（WO-D1-UI-20261101，离线）。"""
from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "portal" / "d1_research.html"


def test_page_has_decision_entry():
    s = PAGE.read_text(encoding="utf-8")
    assert "D1 正式裁决" in s
    assert "/api/d1/decision" in s
    assert "/api/d1/decisions" in s
    assert "paper_only:true" in s
    for tier in ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"):
        assert tier in s
    assert "未正式裁决" in s


def test_page_has_modal_and_submit():
    s = PAGE.read_text(encoding="utf-8")
    assert "dec-mask" in s
    assert "submitDec" in s
    assert "dec-rationale" in s
    assert "置信度" in s
