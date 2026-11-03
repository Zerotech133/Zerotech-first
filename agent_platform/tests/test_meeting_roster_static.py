# -*- coding: utf-8 -*-
"""会议静态花名册：赵云不重复、曹操归研发线。"""
from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "legion_command" / "static" / "meeting.html"


def test_roster_no_duplicate_zhaoyun_and_caocao_in_dev():
    s = PAGE.read_text(encoding="utf-8")
    assert "R2:  { nm:'赵云·风控'" in s
    assert "grp:'risk'" in s
    assert "ARCH1:{ nm:'曹操'" in s and "grp:'dev'" in s
    assert "ZHAOYUN:{ nm:'赵云·治理'" in s and "grp:'zhong'" in s
    assert "grp:'zhong'" in s
