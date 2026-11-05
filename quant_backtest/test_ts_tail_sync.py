# -*- coding: utf-8 -*-
"""ts_tail_sync 纯逻辑测试（2026-11-05，全离线，不出网）。

铁律：日期/编号类断言不靠"看打印对不对"——字面量用 ord() 复核，
比较一律走字符串精确匹配。
"""

import datetime as dt

import ts_tail_sync as tail

TODAY = dt.date(2026, 11, 5)
DAYS = ["20261029", "20261030", "20261102", "20261103", "20261104"]


def test_date_literals_are_clean():
    """码点复核：本机输出通道会把数字字形串味，源码字节必须干净。"""
    assert [ord(c) for c in "20261104"] == [50, 48, 50, 54, 49, 49, 48, 52]
    assert ord("0") == 48 and ord("9") == 57


def test_expected_last_day_conservative():
    """早盘跑批不把今天算缺口 → 昨日。"""
    assert tail.expected_last_day(TODAY, DAYS) == "20261104"


def test_expected_last_day_include_today():
    days = DAYS + ["20261105"]
    assert tail.expected_last_day(TODAY, days, include_today=True) == "20261105"


def test_missing_trading_days():
    assert tail.missing_days("20261029", "20261104", DAYS, calendar=False, max_days=30) == [
        "20261030", "20261102", "20261103", "20261104"]


def test_missing_days_watermark_at_last_returns_empty():
    assert tail.missing_days("20261104", "20261104", DAYS, calendar=False, max_days=30) == []


def test_missing_calendar_days_includes_weekend():
    """业绩快报按公告日：周末公告必须覆盖。10-31 是周六。"""
    out = tail.missing_days("20261030", "20261102", DAYS, calendar=True, max_days=30)
    assert out == ["20261031", "20261101", "20261102"]


def test_missing_days_capped_to_recent():
    """历史大洞不塞进日常批：只保留最近 N 天。"""
    out = tail.missing_days("20261014", "20261104", DAYS, calendar=False, max_days=2)
    assert out == ["20261103", "20261104"]


def test_missing_days_without_watermark():
    assert tail.missing_days(None, "20261104", DAYS, calendar=False, max_days=30) == []


def _manifest(**over):
    m = {t: {"done": True, "rows": 100} for t in tail.RECIPES}
    m.update(over)
    return m


def test_select_stale_picks_lagging():
    wm = {t: "20261104" for t in tail.RECIPES}
    wm["hk_daily"] = "20261029"
    picked, skipped = tail.select_stale(_manifest(), wm, DAYS, TODAY)
    assert picked == ["hk_daily"]
    assert any(x.startswith("irm_qa_sh ") and "已到位" in x for x in skipped)


def test_select_stale_skips_not_done():
    wm = {t: "20261001" for t in tail.RECIPES}
    picked, skipped = tail.select_stale(_manifest(hk_hold={"done": False}), wm, DAYS, TODAY)
    assert "hk_hold" not in picked
    assert any(x.startswith("hk_hold ") and "done=false" in x for x in skipped)


def test_select_stale_skips_no_watermark():
    wm = {t: "20261104" for t in tail.RECIPES}
    wm["forecast"] = None
    picked, skipped = tail.select_stale(_manifest(), wm, DAYS, TODAY)
    assert picked == []
    assert any(x.startswith("forecast ") and "无水位" in x for x in skipped)


def test_select_stale_only_and_deferred():
    """显式点名低频表时不硬拉，返回口径说明。"""
    wm = {t: "20261104" for t in tail.RECIPES}
    picked, skipped = tail.select_stale(
        _manifest(), wm, DAYS, TODAY, only=["hk_basic", "index_weight", "not_a_table"])
    assert picked == []
    assert any(x.startswith("hk_basic ") and "list_date" in x for x in skipped)
    assert any(x.startswith("index_weight ") and "月度" in x for x in skipped)
    assert any(x.startswith("not_a_table ") and "无配方" in x for x in skipped)


def test_only_restricts_scope():
    wm = {t: "20261001" for t in tail.RECIPES}
    picked, _ = tail.select_stale(_manifest(), wm, DAYS, TODAY, only=["top_inst"])
    assert picked == ["top_inst"]


def test_recipes_cover_ten_tail_tables():
    """对账基线：8 张进兜底 + 2 张口径待修 = govern 报的 10 张欠账。"""
    assert len(tail.RECIPES) == 8
    assert set(tail.DEFERRED) == {"hk_basic", "index_weight"}
    assert len(tail.RECIPES) + len(tail.DEFERRED) == 10


def test_recent_periods():
    assert tail.recent_periods(dt.date(2026, 11, 5), 2) == ["20260630", "20260930"]
    assert tail.recent_periods(dt.date(2027, 1, 8), 2) == ["20260930", "20261231"]


def test_periodic_under_sla_not_picked():
    """季频表滞后未超 SLA 不入选——上游确实没发，不是漏采。"""
    wm = {t: "20261104" for t in tail.RECIPES}
    wm["express"] = "20261021"  # 滞后 15 天
    picked, skipped = tail.select_stale(_manifest(), wm, DAYS, TODAY)
    assert "express" not in picked
    assert any(x.startswith("express ") and "SLA 45d" in x for x in skipped)


def test_periodic_over_sla_picked():
    wm = {t: "20261104" for t in tail.RECIPES}
    wm["express"] = "20260601"  # 滞后 157 天
    picked, _ = tail.select_stale(_manifest(), wm, DAYS, TODAY)
    assert "express" in picked


def test_plan_days_periodic_returns_periods():
    days = tail.plan_days("express", tail.RECIPES["express"], "20260601", "20261104",
                          DAYS, TODAY, max_backfill_days=30)
    assert days == ["20260630", "20260930"]


def test_plan_days_daily_returns_trading_days():
    days = tail.plan_days("top_inst", tail.RECIPES["top_inst"], "20261029", "20261104",
                          DAYS, TODAY, max_backfill_days=30)
    assert days == ["20261030", "20261102", "20261103", "20261104"]


def test_every_recipe_has_required_fields():
    for table, rec in tail.RECIPES.items():
        for k in ("api", "scan", "write", "pull", "dcol", "cap", "kind"):
            assert k in rec, "%s 缺 %s" % (table, k)
        assert rec["scan"] in {"by_date", "part_all", "by_code", "single"}
        assert rec["write"] in {"d_", "part", "single"}
        assert rec["pull"] in {"trade_date", "ann_date", "period"}
        assert rec["kind"] in {"daily", "periodic"}
        if rec["kind"] == "periodic":
            assert rec.get("sla_days"), "%s 季频表必须声明 sla_days" % table
        if rec["write"] == "part":
            assert rec.get("keys"), "%s part 追加必须声明去重键" % table
