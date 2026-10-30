# -*- coding: utf-8 -*-
"""batch_backlog 判级单测（K-ORG-20261026-BACKLOGDUTY）。

覆盖：
  · 夜间窗口边界（21:50-07:00 跨零点）
  · 白天让位期积压上涨 → 黄；夜间同数据 → 红
  · 环境变量 ZB_HC_BACKLOG_DAYTIME_EXEMPT=0 可关闭豁免
  · rc=124 连续/单次、ETA 超限、不可用等既有分支不被豁免影响
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import gen_health_card as hc  # noqa: E402

DAY = dt.datetime(2026, 10, 26, 14, 0, 0)
NIGHT = dt.datetime(2026, 10, 26, 22, 30, 0)
EARLY = dt.datetime(2026, 10, 27, 3, 0, 0)


def _bl(**over) -> dict:
    base = {
        "available": True, "jev_delta": -104, "xq_delta": 0,
        "timeout_streak": 0, "eta_hours": None, "log_age_min": 5,
        "jev_left": 10000, "xq_left": 5000, "rounds": [], "path": "test",
        "capacity_per_day": 1000, "data_source": "test",
    }
    base.update(over)
    return base


def test_in_night_window_boundaries():
    assert hc._in_night_window(dt.datetime(2026, 10, 26, 21, 50)) is True
    assert hc._in_night_window(dt.datetime(2026, 10, 26, 23, 59)) is True
    assert hc._in_night_window(dt.datetime(2026, 10, 27, 0, 30)) is True
    assert hc._in_night_window(dt.datetime(2026, 10, 27, 6, 59)) is True
    assert hc._in_night_window(dt.datetime(2026, 10, 27, 7, 0)) is False
    assert hc._in_night_window(dt.datetime(2026, 10, 26, 21, 49)) is False
    assert hc._in_night_window(DAY) is False


def test_backlog_rise_is_yellow_in_daytime(monkeypatch):
    monkeypatch.delenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, raising=False)
    level, detail = hc._batch_backlog_level(_bl(), now=DAY)
    assert level == "yellow", detail
    assert "白天让位期" in detail


def test_backlog_rise_is_red_at_night(monkeypatch):
    monkeypatch.delenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, raising=False)
    assert hc._batch_backlog_level(_bl(), now=NIGHT)[0] == "red"
    assert hc._batch_backlog_level(_bl(), now=EARLY)[0] == "red"


def test_exempt_can_be_disabled(monkeypatch):
    monkeypatch.setenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, "0")
    assert hc._batch_backlog_level(_bl(), now=DAY)[0] == "red"


def test_timeout_streak_unaffected_by_exempt(monkeypatch):
    monkeypatch.delenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, raising=False)
    # 连续 2 轮 rc=124：即使白天也保持红（真故障不豁免）
    assert hc._batch_backlog_level(
        _bl(jev_delta=0, timeout_streak=2), now=DAY)[0] == "red"
    # 单次 rc=124 → 黄
    assert hc._batch_backlog_level(
        _bl(jev_delta=0, timeout_streak=1), now=DAY)[0] == "yellow"


def test_eta_red_unaffected_by_exempt(monkeypatch):
    monkeypatch.delenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, raising=False)
    level, detail = hc._batch_backlog_level(
        _bl(jev_delta=0, eta_hours=hc.BATCH_ETA_RED_HOURS + 1.0), now=DAY)
    assert level == "red", detail


def test_unavailable_is_red(monkeypatch):
    monkeypatch.delenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, raising=False)
    assert hc._batch_backlog_level({"available": False}, now=DAY)[0] == "red"


def test_zero_backlog_is_green(monkeypatch):
    # 2026-10-30 BATCH1：两队列积压都为 0 时判绿（旧逻辑 eta=None → 黄）
    monkeypatch.delenv(hc.BACKLOG_DAYTIME_EXEMPT_ENV, raising=False)
    bl = _bl(jev_delta=0, jev_left=0, xq_left=0)
    level, detail = hc._batch_backlog_level(bl, now=DAY)
    assert level == "green", detail
