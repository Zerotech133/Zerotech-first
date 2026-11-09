# -*- coding: utf-8 -*-
"""tdx_read 日期列探测：读写两侧口径必须对称。

背景：2026-11-06 发现 tdx_sync 写了 30 张表的业务日期列映射，而 tdx_read
只认其中 4 张，其余一律回退 trade_date —— 于是 read_day/read_range 在这些表上
**静默返回空**（ts_anns_d 就是这样：库里有 42 个分片，读出来 0 行）。
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tdx_read as tdxr  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
REAL_PARTS = sorted((HERE / "data" / "tdx" / "ts_anns_d").glob("part_*.parquet"))


def _write_part(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    path = folder / "part_0000.parquet"
    df.to_parquet(path, index=False)
    return str(path)


def test_explicit_mapping_wins():
    tdxr._DATE_COL_CACHE.clear()
    assert tdxr.date_col_of("ts_income") == "end_date"


def test_infers_ann_date_from_schema(tmp_path, monkeypatch):
    part = _write_part(tmp_path / "ts_anns_d", [{"ann_date": "20261105", "ts_code": "600519.SH"}])
    monkeypatch.setattr(tdxr, "_parts", lambda tbl: [part])
    tdxr._DATE_COL_CACHE.clear()
    assert tdxr.date_col_of("ts_anns_d") == "ann_date"


def test_falls_back_to_trade_date_without_parts(monkeypatch):
    monkeypatch.setattr(tdxr, "_parts", lambda tbl: [])
    tdxr._DATE_COL_CACHE.clear()
    assert tdxr.date_col_of("no_such_table") == "trade_date"


def test_read_range_uses_inferred_date_col(tmp_path, monkeypatch):
    part = _write_part(
        tmp_path / "ts_anns_d",
        [
            {"ann_date": "20261105", "ts_code": "600519.SH", "title": "A"},
            {"ann_date": "20261104", "ts_code": "600519.SH", "title": "B"},
        ],
    )
    monkeypatch.setattr(tdxr, "_parts", lambda tbl: [part])
    tdxr._DATE_COL_CACHE.clear()
    df = tdxr.read_range("ts_anns_d", start="20261105", end="20261105")
    assert len(df) == 1 and df.iloc[0]["title"] == "A", "日期列识别错就会静默返回空"


@pytest.mark.skipif(not REAL_PARTS, reason="本地没有 ts_anns_d 分片")
def test_real_anns_table_is_readable():
    tdxr._DATE_COL_CACHE.clear()
    assert tdxr.date_col_of("ts_anns_d") == "ann_date"
    df = tdxr.read_range("ts_anns_d", start="20000101", codes=["600519.SH"],
                         columns=["ts_code", "ann_date", "title"])
    assert len(df) > 0, "分母非空库却读不到数据，说明日期列口径又断了"
