# -*- coding: utf-8 -*-
"""S1-03 输出级健康字段单测（portal/api_dataops.py）。

覆盖（≥4，实际 8）：
  1) delta 字段存在且新鲜→green（today_incr/last_output_at/lag_hours/level）
  2) 陈旧→yellow / 更陈旧→red（阈值 24h/48h）
  3) 缺日期列 / 查询失败 → 字段 null + fail-visible（不抛错）
  4) xq_hot_post 进 POOLS 与 TODAY_COL（hot 断供盲区）
  5) collector 全量补 4 字段（无口径 → null + output_note），hot 断供拉红
  6) 股吧 jsonl 源日期粒度判级（0/1 天 green、2 天 yellow、≥3 天 red）
  7) 告警按增量/滞后判红，首跑后不再报「首跑待执行」
  8) overview summary 增 xq_today_incr/xq_last_output_at/red_alerts（替身重探测，不连真实库）

纪律：全部用临时 sqlite + monkeypatch；不连真实库、不写文件、不启服务。
"""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PORTAL = REPO / "portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_dataops as dataops  # noqa: E402

DAY = "2026-11-02"
DAY_START = DAY + " 00:00:00"
FIELDS = ("today_incr", "last_output_at", "lag_hours", "level")


def _epoch(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


@pytest.fixture()
def ro_con(tmp_path):
    """临时 sqlite（xq_status 表，3 行：今日 2 行 + 昨日 1 行），只读连接。"""
    db = tmp_path / "xq_info.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE xq_status (status_id TEXT, fetched_at TEXT)")
    con.executemany("INSERT INTO xq_status VALUES (?,?)", [
        ("1", "2026-11-02 07:00:00"),
        ("2", "2026-11-02 07:05:00"),
        ("3", "2026-11-01 20:00:00"),
    ])
    con.commit()
    con.close()
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------- 1) 新鲜→green
def test_output_metrics_fresh_is_green(ro_con):
    m = dataops._table_output_metrics(ro_con, "xq_status", "fetched_at",
                                      now_ts=_epoch(DAY + " 08:00:00"),
                                      day_start=DAY_START)
    for key in FIELDS:
        assert key in m, key
    assert m["today_incr"] == 2
    assert m["last_output_at"] == "2026-11-02 07:05:00"
    assert m["lag_hours"] == pytest.approx(0.9)
    assert m["level"] == "green"
    assert m["today_col"] == "fetched_at"


# ---------------------------------------------------------------- 2) 陈旧→yellow/red
def test_output_metrics_stale_yellow_and_red(ro_con):
    yellow = dataops._table_output_metrics(ro_con, "xq_status", "fetched_at",
                                           now_ts=_epoch("2026-11-03 13:05:00"),
                                           day_start="2026-11-03 00:00:00")
    assert yellow["level"] == "yellow"
    assert 24 < yellow["lag_hours"] <= 48
    red = dataops._table_output_metrics(ro_con, "xq_status", "fetched_at",
                                        now_ts=_epoch("2026-11-05 08:00:00"),
                                        day_start="2026-11-05 00:00:00")
    assert red["level"] == "red"
    assert red["lag_hours"] > 48
    # 纯函数边界
    assert dataops._level_for_lag(None) is None
    assert dataops._level_for_lag(1.0) == "green"
    assert dataops._level_for_lag(30) == "yellow"
    assert dataops._level_for_lag(72) == "red"


# ---------------------------------------------------------------- 3) 缺失→null + fail-visible
def test_output_metrics_missing_col_and_error_are_null_fail_visible(ro_con):
    no_col = dataops._table_output_metrics(ro_con, "xq_status", None)
    assert all(no_col.get(k) is None for k in FIELDS)
    assert no_col.get("output_note")
    bad = dataops._table_output_metrics(ro_con, "no_such_table", "fetched_at")
    assert all(bad.get(k) is None for k in FIELDS)
    assert "error" in bad  # 失败可见，不静默、不抛错


# ---------------------------------------------------------------- 4) xq_hot_post 登记
def test_xq_hot_post_registered_in_pools_and_today_col():
    pool_names = [t for rel, tables in dataops.POOLS if "xueqiu" in rel for t, _note in tables]
    assert "xq_hot_post" in pool_names
    assert dataops.TODAY_COL.get("xq_hot_post") == "fetched_at"
    assert "xq_hot_post" in dataops.COLLECTOR_OUTPUT_TABLES.get("XueqiuCrawl", [])


# ---------------------------------------------------------------- 5) collector 全量补字段
def test_attach_output_health_adds_fields_to_all_collectors():
    pool_index = {
        "xq_status": {"today_incr": 5, "last_output_at": "2026-11-02 07:00:00",
                      "lag_hours": 1.0, "level": "green"},
        "xq_hot_post": {"today_incr": 0, "last_output_at": "2026-10-25 02:12:58",
                        "lag_hours": 200.0, "level": "red"},   # hot 断供
    }
    collectors = [
        {"key": "XueqiuCrawl", "name": "雪球日采集", "health": "armed"},
        {"key": "AgentShadowDaily", "name": "影子日巡", "health": "armed"},
    ]
    for c in collectors:
        dataops._attach_output_health(c, pool_index, {"series": [], "last_by_source": {}},
                                      now_day=DAY)
    for c in collectors:
        for key in FIELDS:
            assert key in c, (c["key"], key)      # 缺失也是显式 null，字段齐全
    crawl = collectors[0]
    assert crawl["today_incr"] == 5
    assert crawl["last_output_at"] == "2026-11-02 07:00:00"
    assert crawl["level"] == "red"                # 最差级：hot 断供亦拉红
    assert set(crawl["output_metrics"]) == {"xq_status", "xq_hot_post"}
    other = collectors[1]
    assert other["level"] is None and other["output_note"]


# ---------------------------------------------------------------- 6) 股吧日期粒度
def test_attach_output_health_guba_date_granularity():
    fresh = {"key": "GubaCrawl", "name": "股吧采集"}
    dataops._attach_output_health(fresh, {}, {"series": [{"股吧帖": 3}],
                                              "last_by_source": {"股吧帖": "2026-11-01"}},
                                  now_day=DAY)
    assert fresh["today_incr"] == 3
    assert fresh["last_output_at"] == "2026-11-01"
    assert fresh["level"] == "green"   # 日更源昨日=正常

    yellow = {"key": "GubaCrawl", "name": "股吧采集"}
    dataops._attach_output_health(yellow, {}, {"series": [], "last_by_source": {"股吧帖": "2026-10-31"}},
                                  now_day=DAY)
    assert yellow["level"] == "yellow"

    red = {"key": "GubaCrawl", "name": "股吧采集"}
    dataops._attach_output_health(red, {}, {"series": [], "last_by_source": {"股吧帖": "2026-10-29"}},
                                  now_day=DAY)
    assert red["level"] == "red"

    none = {"key": "GubaCrawl", "name": "股吧采集"}
    dataops._attach_output_health(none, {}, {"series": [], "last_by_source": {}}, now_day=DAY)
    assert none["last_output_at"] is None and none["level"] is None


# ---------------------------------------------------------------- 7) 告警按增量/滞后
def test_collector_alerts_red_by_lag_and_no_first_run_after_data():
    fresh = [{"key": "XueqiuCrawl", "name": "雪球日采集", "today_incr": 5,
              "last_output_at": "2026-11-02 07:00:00", "lag_hours": 1.0,
              "level": "green", "output_metrics": {}}]
    r_fresh = dataops._collector_alerts(fresh)
    assert r_fresh["red_alerts"] == 0
    assert not any("首跑待执行" in a for a in r_fresh["alerts"])   # 首跑后不再报

    stale = [{"key": "XueqiuCrawl", "name": "雪球日采集", "today_incr": 0,
              "last_output_at": "2026-10-28 07:00:00", "lag_hours": 120.0,
              "level": "red", "output_metrics": {}}]
    r_stale = dataops._collector_alerts(stale)
    assert r_stale["red_alerts"] == 1
    assert any("【红】" in a and "产出中断" in a for a in r_stale["alerts"])

    no_data = [{"key": "XueqiuCrawl", "name": "雪球日采集", "today_incr": None,
                "last_output_at": None, "lag_hours": None, "level": None,
                "output_metrics": {}}]
    r_nodata = dataops._collector_alerts(no_data)
    assert any("首跑待执行" in a for a in r_nodata["alerts"])

    # hot 断供盲区：主口径绿、hot 红 → 单条红告警
    hot_stale = [{"key": "XueqiuCrawl", "name": "雪球日采集", "today_incr": 5,
                  "last_output_at": "2026-11-02 07:00:00", "lag_hours": 1.0,
                  "level": "green",
                  "output_metrics": {"xq_status": {"level": "green"},
                                     "xq_hot_post": {"level": "red",
                                                     "last_output_at": "2026-10-25 02:12:58",
                                                     "lag_hours": 200.0}}}]
    r_hot = dataops._collector_alerts(hot_stale)
    assert r_hot["red_alerts"] == 1
    assert any("xq_hot_post" in a and "断供" in a for a in r_hot["alerts"])


# ---------------------------------------------------------------- 8) overview summary
def test_overview_summary_output_health_fields(monkeypatch, tmp_path):
    """overview 集成：重探测全部替身（临时目录），不连真实库、不读真实数据根。"""
    monkeypatch.setattr(dataops, "DATA", str(tmp_path / "data"))
    monkeypatch.setattr(dataops, "_pool_stats", lambda errors: [
        {"db": "xueqiu/xueqiu_info.db", "size_mb": 30, "mtime": None, "tables": [
            {"name": "xq_status", "note": "雪球帖存量", "rows": 100, "today": 7,
             "today_incr": 7, "last_output_at": "2026-11-02 07:00:00",
             "lag_hours": 1.2, "level": "green"},
            {"name": "xq_status_classified", "note": "打标", "rows": 90, "today": None,
             "today_incr": None, "last_output_at": None, "lag_hours": None, "level": None},
            {"name": "xq_hot_post", "note": "热帖", "rows": 100, "today": 3,
             "today_incr": 3, "last_output_at": "2026-11-02 07:00:00",
             "lag_hours": 1.2, "level": "green"},
        ]},
    ])
    monkeypatch.setattr(dataops, "_pipeline_states", lambda errors: {
        "seed": {}, "classify": {"pending": 0, "log_tail": []},
        "report": {"posts_today": 1, "ingested_today": 1, "log_tail": []}})
    monkeypatch.setattr(dataops, "_daily_intake", lambda errors, days=14: {
        # 动态今日：GubaCrawl 的健康按真实当日计算；固定 2026-11-02 会在
        # 2026-11-05 之后被判 stale/red，形成日期时间炸弹（非分支回归）。
        "series": [{"股吧帖": 1}], "last_by_source": {"股吧帖": time.strftime("%Y-%m-%d")},
        "days": 14, "today": 1})
    monkeypatch.setattr(dataops, "_task_groups",
                        lambda errors: {"groups": [], "total": 0, "armed_total": 0})
    monkeypatch.setattr(dataops, "_catalog_summary",
                        lambda errors: {"version": 18, "table_count": 256})
    monkeypatch.setattr(dataops, "_source_cards", lambda errors: [])

    ov = dataops.overview()
    summary = ov["summary"]
    # 只增字段（向后兼容原有键仍在）
    for old_key in ("total_rows", "ingested_today", "structured_rate", "xq_total",
                    "xq_classified", "active_collectors", "alerts"):
        assert old_key in summary, old_key
    assert summary["xq_today_incr"] == 7
    assert summary["xq_last_output_at"] == "2026-11-02 07:00:00"
    assert summary["red_alerts"] == 0
    assert not any("首跑待执行" in a for a in summary["alerts"])

    by_key = {c["key"]: c for c in ov["collectors"]}
    for c in ov["collectors"]:
        for key in FIELDS:
            assert key in c, (c["key"], key)
    assert by_key["XueqiuCrawl"]["level"] == "green"
    assert by_key["XueqiuCrawl"]["today_incr"] == 7
    assert by_key["XueqiuCrawl"]["output_metrics"]["xq_hot_post"]["today_incr"] == 3
    assert by_key["GubaCrawl"]["today_incr"] == 1
    # 真错误仍 fail-visible（DATA 被指向空临时目录，模型分布探测失败入 errors）
    assert isinstance(ov["errors"], list)
