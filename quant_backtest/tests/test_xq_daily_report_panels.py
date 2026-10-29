# -*- coding: utf-8 -*-

import pytest
pytest.importorskip("jieba")
# ^ 可选依赖守护：缺 jieba 时跳过而非收集报错（2026-10-28）
"""tests/test_xq_daily_report_panels.py — WO-QT-20261020-077 面板拆分单测

当前热度（current_panel，仅当日发布/时点可证）与历史回溯（ingested_panel
降级为回溯参考）隔离的逻辑单测。全部合成 fixture，不触真实库/网络。

双向变异留证（mutation record）：
  变异点 M1：split_fresh 去掉「created_at 不可解析 → 不进 current」守卫
    （把 `dt = _parse_ts(...)` 结果判 None 分支改为 None 视为当日）。
    → test_no_time_evidence_never_in_current 变红。
    已执行留证：变异后 1 failed / 5 passed，还原后 6 passed。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xq_daily_report as rep

DAY = "2026-10-20"

# 合成 load_rows 行形态：
# (sid, created_at, fetched_at, title, desc, category,
#  event_type, sentiment, tickers, confidence, parse_ok, model, prompt_version, content_type)


def _row(sid, created_at, fetched_at=DAY + " 04:43:40", tickers='["600519"]',
         sn="bullish", et="个股研判"):
    return (sid, created_at, fetched_at, "标题%d" % sid, "", "stock",
            et, sn, tickers, 0.9, 1, "m", "v1", "community")


def test_fresh_row_goes_into_current():
    """当日发布（created_at=day）帖 → 进 current_panel 榜"""
    tac = [_row(1, DAY + " 09:00:00"), _row(2, DAY + " 10:00:00")]
    panels = rep.build_panels(tac, [], DAY)
    cur = panels["current"]
    assert cur["total"] == 2
    assert cur["top_codes"].get("600519") == 2
    assert cur["fresh_basis"] == "created_at"


def test_backfill_row_only_in_ingested_not_current():
    """历史帖（created_at 远早于 day、fetched_at=day）→ 只进 ingested 回溯榜"""
    ing = [_row(1, "2018-09-26 10:37:37"), _row(2, "2020-05-13 08:00:00")]
    panels = rep.build_panels([], ing, DAY)
    assert panels["current"]["total"] == 0
    assert panels["current"]["top_codes"] == {}
    ip = panels["ingested"]
    assert ip["total"] == 2
    assert ip["backfill_count"] == 2
    assert ip["top_codes"].get("600519") == 2
    assert ip["backfill_only"] is True   # 明确降级标记：回溯参考，非当前热度


def test_no_time_evidence_never_in_current():
    """无时间证据（created_at 缺失/垃圾串）→ 失败关闭，绝不进 current"""
    rows = [_row(1, None), _row(2, "not-a-timestamp"), _row(3, "2026-10-20")]
    fresh, stale = rep.split_fresh(rows, DAY)
    assert [r[0] for r in fresh] == [3]      # 仅时点可证者进 current 侧
    assert [r[0] for r in stale] == [1, 2]   # 无证据者归 stale
    panels = rep.build_panels(rows, [], DAY)
    assert panels["current"]["total"] == 1
    assert panels["current"]["top_codes"] == {"600519": 1}


def test_ingested_top_codes_counts_backfill_only():
    """ing 窗口内混入当日发布帖：total 计入、fresh_count 区分，但回溯榜只计历史帖"""
    ing = [_row(1, "2018-09-26 10:37:37"),          # 回溯
           _row(2, "2020-01-01 00:00:00"),          # 回溯
           _row(3, DAY + " 12:00:00")]              # 当日发布（不进回溯榜）
    panels = rep.build_panels([], ing, DAY)
    ip = panels["ingested"]
    assert ip["total"] == 3
    assert ip["fresh_count"] == 1
    assert ip["backfill_count"] == 2
    assert ip["top_codes"] == {"600519": 2}          # 仅 2 条历史帖计票


def test_empty_window_empty_state():
    """空窗口：current 全空、ingested 计数归零——页面空态分支的数据前提"""
    panels = rep.build_panels([], [], DAY)
    assert panels["current"]["total"] == 0
    assert panels["current"]["top_codes"] == {}
    assert panels["current"]["classified"] == 0
    assert panels["ingested"]["total"] == 0
    assert panels["ingested"]["top_codes"] == {}
    assert panels["ingested"]["backfill_count"] == 0


def test_split_fresh_date_boundary():
    """时点边界：day 23:59:59 属当日，day+1 00:00:01 与 day-1 均不属"""
    rows = [_row(1, DAY + " 23:59:59"),
            _row(2, "2026-10-21 00:00:01"),
            _row(3, "2026-10-19 23:59:59")]
    fresh, stale = rep.split_fresh(rows, DAY)
    assert [r[0] for r in fresh] == [1]
    assert [r[0] for r in stale] == [2, 3]
