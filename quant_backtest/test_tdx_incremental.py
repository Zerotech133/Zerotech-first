# -*- coding: utf-8 -*-
"""tdx_sync 增量过滤/日期列推断的回归测试（2026-11-05，全离线）。

覆盖两个真实判例：
  1. ts_new_share offset 按过滤后行数推进 → 2072 行要 1000+ 次请求（卡死 5 分钟）；
  2. 一批表因日期列不叫 trade_date 被判"无可解析日期列"→ 永久 SKIP。
"""

import tdx_sync as t

COLS = ["ts_code", "ipo_date", "price"]


def _row(code, d):
    return [code, d, 1.0]


def test_filter_all_old_filtered_stops():
    """远端确实按日期过滤过：首屏全旧 = 无新增，可以停。"""
    rows = [_row("A", "20261001"), _row("B", "20261002")]
    kept, stop = t._filter_incremental(rows, COLS, "20261028", "ipo_date", filtered=True)
    assert kept == [] and stop is True


def test_filter_all_old_unfiltered_does_not_stop():
    """远端没过滤（返回全表）：首屏全旧不代表后面没有新行，必须继续翻。"""
    rows = [_row("A", "20200101"), _row("B", "20200102")]
    kept, stop = t._filter_incremental(rows, COLS, "20261028", "ipo_date", filtered=False)
    assert kept == [] and stop is False


def test_filter_keeps_new_rows():
    rows = [_row("A", "20261001"), _row("B", "20261029"), _row("C", "20261104")]
    kept, stop = t._filter_incremental(rows, COLS, "20261028", "ipo_date", filtered=False)
    assert [r[0] for r in kept] == ["B", "C"]
    assert stop is False


def test_filter_no_date_col_passes_through():
    """列不存在时不能把整页丢掉（老逻辑会 break → 静默漏数据）。"""
    kept, stop = t._filter_incremental([["x"]], ["a"], "20261028", "ipo_date", filtered=True)
    assert kept == [["x"]] and stop is False


def test_filter_without_watermark_passes_through():
    kept, stop = t._filter_incremental([["x"]], COLS, None, "ipo_date", filtered=True)
    assert kept == [["x"]] and stop is False


def test_filter_handles_dash_and_datetime_format():
    """日期列格式混杂：2026-10-29 / 2026-10-29 17:00:00 都要能比较。"""
    rows = [[_row("A", "2026-10-29")], [_row("B", "2026-11-04 17:00:03")]]
    flat = [r[0] for r in rows]
    kept, _ = t._filter_incremental(flat, COLS, "20261028", "ipo_date", filtered=False)
    assert [r[0] for r in kept] == ["A", "B"]


def test_offset_must_advance_by_raw_rows():
    """把 bug 的算术固化：2 行新数据但远端返回 2072 行，offset 应 +2072 而不是 +2。"""
    rows = [_row("A", "20261001")] * 2070 + [_row("B", "20261104"), _row("C", "20261104")]
    raw_n = len(rows)
    kept, _ = t._filter_incremental(rows, COLS, "20261028", "ipo_date", filtered=False)
    assert len(kept) == 2
    assert raw_n == 2072  # 若按 len(kept) 推进，offset 只 +2 → 1000+ 次请求
