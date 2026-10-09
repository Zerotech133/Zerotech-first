# -*- coding: utf-8 -*-
"""kg_candle 增量口径单测：sync 无参锚定仓内实际日期（OPS 2026-10-09 修复）。

只测 _recent_dates / _cur_prev_ym 的纯文件系统逻辑，不连 Neo4j。"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_candle


@pytest.fixture
def mins_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_candle, "MINS_DIR", str(tmp_path))

    def put(code, ym, days):
        df = pd.DataFrame({
            "trade_time": pd.to_datetime(["%s 09:31:00" % d for d in days]),
            "close": [1.0] * len(days),
        })
        df.to_parquet(os.path.join(str(tmp_path), "%s_%s.parquet" % (code, ym)))
    return put


def _ym_pair():
    now = datetime.now()
    return (now.strftime("%Y%m"),
            (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m"))


def test_cur_prev_ym():
    cur, prev = _ym_pair()
    assert kg_candle._cur_prev_ym() == {cur, prev}
    # 跨年：1 月的上月是去年 12 月
    fake = datetime(2027, 1, 15)
    assert (fake.strftime("%Y%m"),
            (fake.replace(day=1) - timedelta(days=1)).strftime("%Y%m")) \
        == ("202701", "202612")


def test_recent_dates_scopes_curprev_and_sorts(mins_dir):
    """只扫当月+上月文件，跨文件去重升序，忽略旧月。"""
    cur, prev = _ym_pair()
    now = datetime.now()
    days_cur = sorted((now - timedelta(days=k)).strftime("%Y%m%d")
                      for k in (2, 4, 6))
    days_prev = sorted((now - timedelta(days=k)).strftime("%Y%m%d")
                       for k in (34, 36))
    mins_dir("000001_SZ", cur, days_cur)
    mins_dir("600000_SH", prev, days_prev)          # 与 000001 无交集
    mins_dir("300001_SZ", "202501", ["20250106"])   # 旧月应被忽略
    mins_dir("688001_SH", cur, days_cur[:1])        # 重复日期去重

    got = kg_candle._recent_dates(5)
    assert got == sorted(set(days_cur + days_prev))[-5:]
    assert "20250106" not in got


def test_recent_dates_truncates_lookback(mins_dir):
    """lookback 截断取最近 n 个（升序）。"""
    cur, _ = _ym_pair()
    now = datetime.now()
    days = sorted((now - timedelta(days=k)).strftime("%Y%m%d")
                  for k in (1, 2, 3))
    mins_dir("000001_SZ", cur, days)
    assert kg_candle._recent_dates(2) == days[-2:]
    assert kg_candle._recent_dates(5) == days


def test_recent_dates_empty_store(mins_dir):
    """仓内只有旧月文件 → 空（sync 应空转退出而非报错）。"""
    mins_dir("000001_SZ", "202501", ["20250106"])
    assert kg_candle._recent_dates() == []
