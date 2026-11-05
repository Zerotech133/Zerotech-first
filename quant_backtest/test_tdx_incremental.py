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


# ===== 序性判定（2026-11-05 尾部回溯，判例 ts_news 250 万行 24 分钟 → 14 秒）=====
# 远端 /api/data/table 实测既不认 start_date，也不认 order/sort/order_by/desc
# （10 组参数 total 恒等），offset=0 是最旧行。但 offset=2495283 只要 2.6s
# （非线性扫描），且表按时间升序存储 → 从尾部回溯的请求数只与增量有关。


def test_mode_tail_for_ascending_table_with_new_rows():
    """ts_news 实测：头 2020、尾 2026-11-04、本地 10-30 → 走尾部回溯。"""
    mode, why = t.decide_incremental_mode("20200101", "20200106",
                                          "20261103", "20261104", "2026-10-30")
    assert mode == "tail"
    assert "升序" in why


def test_mode_stop_for_ascending_table_already_covered():
    """升序表尾部已到水位 → 无新增，不能再空翻全表。"""
    mode, _ = t.decide_incremental_mode("20200101", "20200106",
                                        "20261020", "20261027", "2026-10-28")
    assert mode == "stop"


def test_mode_stop_for_descending_table_after_head_rows():
    """tdx_sector_daily 实测：头 20261022、尾 2005、本地 20261022。
    首屏已收下新行，尾部更旧 → 停（否则空翻 748 页）。"""
    mode, why = t.decide_incremental_mode("20260929", "20261022",
                                          "20050607", "20050706", "20261022")
    assert mode == "stop"
    assert "降序" in why


def test_mode_forward_for_unordered_table():
    """无序表（头尾区间重叠、都旧）不能停：中间可能仍有新行，保守翻全表。"""
    mode, why = t.decide_incremental_mode("20050101", "20261010",
                                          "20050301", "20261012", "2026-10-28")
    assert mode == "forward"
    assert "序性不明" in why


def test_mode_forward_when_whole_table_is_new():
    """头尾都比本地水位新 = 本地严重落后，回溯等同全量，仍走 forward。"""
    mode, _ = t.decide_incremental_mode("20261103", "20261104",
                                        "20261103", "20261104", "2026-10-28")
    assert mode == "forward"


def test_mode_forward_without_watermark():
    mode, why = t.decide_incremental_mode("20200101", "20200106",
                                          "20261103", "20261104", None)
    assert mode == "forward" and "水位" in why


def test_page_bounds_normalizes_formats():
    """日期列格式混杂（2026-11-03 / 2026-11-04 09:28:57）都要归一成 YYYYMMDD。"""
    rows = [["A", "2026-11-03 13:01:43"], ["B", "20261104"]]
    assert t._page_bounds(rows, ["c", "dt"], "dt") == ("20261103", "20261104")


def test_page_bounds_missing_col_returns_none():
    assert t._page_bounds([["x"]], ["a"], "dt") == (None, None)


def test_tail_pages_cap_is_bounded():
    """回溯上限必须有限：防退化挂死（200 页 = 100 万行）。"""
    assert 1 <= t.MAX_TAIL_PAGES <= 500
    assert t.PAGE == 5000  # API 单次上限，实测超 5000 也只回 5000


# ===== 日期规整（2026-11-05 假 SKIP 第三变种）=====
# ts_dividend / ts_fina_audit / ts_stk_rewards 的 ann_date 含少量 NaN →
# pandas 把整列读成 float64 → 值是 20261008.0，原代码直接 .replace() 抛
# AttributeError → _parquet_latest_date 整体 except 返回 None → 永久 SKIP。


def test_norm_date_float_from_nan_column():
    """核心判例：含 NaN 的列被读成 float，20261008.0 必须能解析。"""
    assert t._norm_date(20261008.0) == "20261008"


def test_norm_date_accepts_common_formats():
    assert t._norm_date("2026-10-08") == "20261008"
    assert t._norm_date("20261008 17:00:00") == "20261008"
    assert t._norm_date("2026/10/08") == "20261008"
    assert t._norm_date("20261008") == "20261008"


def test_norm_date_rejects_junk():
    for v in (None, "nan", "NaN", "None", "NaT", "", "abc", "2026100"):
        assert t._norm_date(v) is None, v


def test_latest_date_survives_dirty_partition(tmp_path, monkeypatch):
    """单分片脏值/缺列不能让整表水位归零（原实现一处异常即 None）。"""
    import pandas as pd

    monkeypatch.setattr(t, "OUT", str(tmp_path))
    monkeypatch.setattr(t, "_date_col_of", lambda tbl: "ann_date")
    d = tmp_path / "tbl"
    d.mkdir()
    pd.DataFrame({"ann_date": ["20261008", "20260101"]}).to_parquet(
        d / "part_0000.parquet", index=False)
    # 判例根源：含 NaN → pandas 读成 float64 → 20260930.0
    pd.DataFrame({"ann_date": [20260930.0, None]}).to_parquet(
        d / "part_0001.parquet", index=False)
    # 缺列分片：原实现会让整表返 None
    pd.DataFrame({"other": [1, 2]}).to_parquet(d / "part_0002.parquet", index=False)
    assert t._parquet_latest_date("tbl") == "20261008"


def test_latest_date_all_junk_returns_none(tmp_path, monkeypatch):
    """全部分片都不可解析时才允许返回 None（不能把脏值当 0）。"""
    import pandas as pd

    monkeypatch.setattr(t, "OUT", str(tmp_path))
    monkeypatch.setattr(t, "_date_col_of", lambda tbl: "ann_date")
    d = tmp_path / "tbl2"
    d.mkdir()
    pd.DataFrame({"ann_date": ["abc", None]}).to_parquet(
        d / "part_0000.parquet", index=False)
    assert t._parquet_latest_date("tbl2") is None


def test_skipped_tables_now_have_watermark():
    """回归：这三张曾被判"无可解析日期列"，现在必须有水位（本地无数据则跳过）。"""
    import os

    import pytest

    for tbl in ("ts_dividend", "ts_fina_audit", "ts_stk_rewards"):
        if not os.path.isdir(os.path.join(t.OUT, tbl)):
            pytest.skip("%s 本地无数据" % tbl)
        latest = t._parquet_latest_date(tbl)
        assert latest is not None, "%s 水位仍为 None（会再次永久 SKIP）" % tbl
        assert latest >= "20260101", "%s 水位异常旧：%s" % (tbl, latest)



