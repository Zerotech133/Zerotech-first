# -*- coding: utf-8 -*-
"""WO-QT-20261021-102 反例测试：年度 freshness 可计龄（4 位年份）。

背景：CnOpenData 5 张年度表 freshness 只有 4 位年份（"2024"），
kg_source_rating._days_since 只认 6/8 位 → 返回 None → stale_tables 里被
静默 continue。后果是年度表**既不误报也永远不可见**（监控上不存在）。
本文件钉住三件事：
  1. 4 位年份按「该年 12 月 31 日」计龄（不是年初，不是年末+1）
  2. 当年/未来年份钳到 0（不为负、不排除）
  3. 6 位/8 位与非法值行为**逐字节不变**（老实现副本对照）
"""
import json
import os
import sys
import tempfile
from datetime import datetime

# 同目录绝对导入（仓库多处同款写法；使本文件在仓库根或子目录下都能跑）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kg_source_rating as ksr          # noqa: E402


# ── 老实现副本：6/8 位与非法值行为不得变化 ──────────────────

def _old_days_since(fresh):
    """改动前 _days_since 原样副本（只作回归对照，不代表期望语义）。"""
    f = str(fresh or "")
    try:
        if len(f) == 8:
            d = datetime.strptime(f, "%Y%m%d")
        elif len(f) == 6:
            y, m = int(f[:4]), int(f[4:6]) + 1
            if m == 13:
                y, m = y + 1, 1
            d = datetime(y, m, 1)
        else:
            return None
        return (datetime.now() - d).days
    except ValueError:
        return None


def _year_end(year):
    return datetime(year, 12, 31)


# ── 1. 4 位年份：年末基准 ──────────────────────────────────

def test_year_2024_anchor_is_year_end():
    """'2024' → 以 2024-12-31 为基准（最保守的"最新可得时点"）。"""
    now = datetime.now()
    want = (now - _year_end(2024)).days
    got = ksr._days_since("2024")
    assert got == want, "2024 计龄应=%d（年末基准），实得 %r" % (want, got)


def test_year_2024_not_year_start():
    """反例：取 1-1 会把表多算近一年 → 必须不等于年初口径。"""
    now = datetime.now()
    start = (now - datetime(2024, 1, 1)).days
    got = ksr._days_since("2024")
    assert got != start, "仍在用年初基准（%d）——年度表被系统性算老一岁" % start
    assert got < start, "年末基准应短于年初基准"


def test_year_2024_matches_ordinal_20241231():
    """'2024' 与 '20241231' 必须等价（年末日就是该年最新可得时点）。"""
    assert ksr._days_since("2024") == ksr._days_since("20241231")


def test_year_2026_clamped_to_zero():
    """今年（now=2026 时 '2026'）：基准日 2026-12-31 尚未到 → 钳到 0。"""
    now = datetime.now()
    assert (now - _year_end(now.year)).days < 0, "本机时间不在年内？"
    assert ksr._days_since(str(now.year)) == 0


def test_future_years_clamped_to_zero():
    """未来年份一律 0：不为负（污染统计）、也不排除（更不该报断流）。"""
    now = datetime.now()
    for y in (now.year + 1, now.year + 3, 2099):
        got = ksr._days_since(str(y))
        assert got == 0, "%s 应钳到 0，实得 %r" % (y, got)


def test_year_series_monotonic():
    """年份越老计龄越大，且相邻差≈2 年±闰日（锚点稳定）。"""
    lags = [ksr._days_since(str(y)) for y in (2024, 2022, 2020, 2018)]
    assert lags == sorted(lags), "年份越老计龄应越大：%r" % (lags,)
    for newer, older in zip(lags, lags[1:]):
        gap = older - newer
        assert 700 <= gap <= 750, "相邻年份差应是 2 年±闰日：%r" % (gap,)


def test_int_year_value_supported():
    """freshness 若以 int 落盘（2024），str() 后同样可计龄。"""
    assert ksr._days_since(2024) == ksr._days_since("2024")


# ── 2. 6 位 / 8 位 / 非法值：行为不变 ──────────────────────

def test_six_digit_month_end_approximation_unchanged():
    """YYYYMM 仍取次月 1 日近似月末；12 月跨年进位不变。"""
    now = datetime.now()
    assert ksr._days_since("202406") == (now - datetime(2024, 7, 1)).days
    assert ksr._days_since("202412") == (now - datetime(2025, 1, 1)).days
    assert ksr._days_since("202506") == (now - datetime(2025, 7, 1)).days


def test_eight_digit_exact_unchanged():
    now = datetime.now()
    assert ksr._days_since("20240131") == (now - datetime(2024, 1, 31)).days
    assert ksr._days_since("20261020") == (now - datetime(2026, 10, 20)).days


def test_six_eight_digit_equal_old_impl():
    """对 6/8 位样本，新实现与老实现逐值一致（零影响硬门）。"""
    samples = ["20240101", "20240229", "20240630", "20241231", "20260101",
               "202401", "202412", "202506", "202605", "202612"]
    for s in samples:
        assert ksr._days_since(s) == _old_days_since(s), \
            "%s 计龄相对老实现漂移" % s


def test_invalid_values_still_none():
    """非法值按原逻辑回落 None（不是 0——0 会被当成"今天刚更新"）。"""
    for bad in ("", "abc", None, "20a4", "202", "2024a", "2024-12",
                "0000", "99999999", "  ", [], {}, "20241"):
        assert ksr._days_since(bad) is None, "%r 应回落 None" % (bad,)


def test_ymd_invalid_date_still_none():
    """8 位但非法日期仍 ValueError → None。"""
    assert ksr._days_since("20240230") is None
    assert ksr._days_since("99999999") is None


# ── 3. 集成：年度表必须出现在 stale_tables 里 ──────────────

def _write_catalog(tmpdir, tables):
    path = os.path.join(tmpdir, "catalog.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "tables": tables, "aliases": {}}, f)
    return path


def _annual_entry(fresh, **kw):
    e = {"source": "cnopendata", "date_col": "year", "freshness": fresh,
         "freq": "Y", "rows": 100, "files": 1, "date_status": "registered",
         "verification_status": "verified"}
    e.update(kw)
    return e


def test_annual_table_visible_in_stale_tables(monkeypatch):
    """回归钉：年度表此前被静默跳过（lag=None → continue），现在必须可见。"""
    now = datetime.now()
    tables = {
        "annual_old": _annual_entry("2014"),
        "annual_current": _annual_entry(str(now.year)),
        "annual_future": _annual_entry(str(now.year + 1)),
    }
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(ksr, "CATALOG", _write_catalog(td, tables))
        stale = {r["table"]: r for r in ksr.stale_tables()}
    assert "annual_old" in stale, "年度表仍未进断流列表（盲区未修好）"
    got = stale["annual_old"]
    assert got["freshness"] == "2014"
    assert got["budget"] == 400, "年度表应用 Y 档预算 400，实得 %r" % got["budget"]
    assert got["lag_days"] == (now - _year_end(2014)).days
    assert "annual_current" not in stale, "当年年度包误报断流"
    assert "annual_future" not in stale, "未来年份误报断流"


def test_annual_table_lag_equals_explicit_year_end(monkeypatch):
    """freshness='2024' 与 '20241231' 在同一 catalog 下判定完全一致。"""
    tables = {"annual_tag": _annual_entry("2024"),
              "annual_exact": _annual_entry("20241231")}
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(ksr, "CATALOG", _write_catalog(td, tables))
        stale = {r["table"]: r for r in ksr.stale_tables()}
    assert stale["annual_tag"]["lag_days"] == stale["annual_exact"]["lag_days"]
    assert stale["annual_tag"]["budget"] == stale["annual_exact"]["budget"] == 400


def test_daily_table_unaffected_by_year_branch(monkeypatch):
    """同 catalog 内日频表判定不受 4 位年份分支影响。

    交易日历口径（trade_days_lag）在本用例打桩为 None：它自带
    test_kg_schema_smoke 覆盖，这里要的是「自然日判定没被年度分支带偏」。
    """
    now = datetime.now()
    fresh_day = now.strftime("%Y%m%d")
    tables = {
        "daily_ok": {"source": "tushare", "date_col": "trade_date",
                     "freshness": fresh_day, "freq": "D", "rows": 100},
        "monthly_ok": {"source": "tushare", "date_col": "trade_date",
                       "freshness": now.strftime("%Y%m"), "freq": "M",
                       "rows": 100},
        "annual_old": _annual_entry("2014"),
    }
    monkeypatch.setattr(ksr, "trade_days_lag", lambda *a, **k: None)
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(ksr, "CATALOG", _write_catalog(td, tables))
        names = {r["table"] for r in ksr.stale_tables()}
    assert "daily_ok" not in names and "monthly_ok" not in names, \
        "日/月频表被年度分支带偏：%r" % (names,)
    assert "annual_old" in names
