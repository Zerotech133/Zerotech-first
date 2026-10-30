# -*- coding: utf-8 -*-
"""kline_daily 本地 parquet 回退测试（WO-KLINE-LOCAL-20261030）。"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "agent_platform" / "engine"))
import tools  # noqa: E402


def test_local_kline_shape():
    p = Path(tools.LOCAL_KLINE_DIR) / "002112_SZ.parquet"
    if not p.exists():
        pytest.skip("本地 parquet 不在本机")
    bars = tools._local_kline("002112", 5)
    assert bars and len(bars) <= 5
    assert len(bars[0]) == 6
    assert bars[0][0].count("-") == 2  # 日期统一 YYYY-MM-DD
