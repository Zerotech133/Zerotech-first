# -*- coding: utf-8 -*-
"""IDXDAILY 回归：curated 日更必须覆盖 index_daily（JEV 基准）。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from analysis import curated_update as cu  # noqa: E402


def test_curated_tables_cover_index_daily():
    assert "index_daily" in cu.TABLES, "index_daily 必须纳入日更（否则 JEV 基准冻结）"
    for t in ("daily", "daily_basic", "moneyflow"):
        assert t in cu.TABLES
