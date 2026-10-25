# -*- coding: utf-8 -*-
"""jev_night 时间窗回归（K-ORG-20261025-JEVNIGHT）。

窗口语义：22:00 开始 → 次日 07:00 结束。三种情形必须区分清楚，
否则要么夜里不跑、要么白天误跑（都会浪费一整夜 GPU）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_night  # noqa: E402


def test_before_start_waits_until_tonight():
    now = datetime(2026, 10, 25, 11, 46)
    begin, end = jev_night.window("22:00", "07:00", now=now)
    assert begin == datetime(2026, 10, 25, 22, 0)
    assert end == datetime(2026, 10, 26, 7, 0)


def test_inside_window_starts_immediately_ends_tomorrow():
    now = datetime(2026, 10, 25, 23, 10)
    begin, end = jev_night.window("22:00", "07:00", now=now)
    assert begin == now
    assert end == datetime(2026, 10, 26, 7, 0)


def test_cross_midnight_window():
    now = datetime(2026, 10, 25, 20, 0)
    begin, end = jev_night.window("23:30", "01:00", now=now)
    assert begin == datetime(2026, 10, 25, 23, 30)
    assert end == datetime(2026, 10, 26, 1, 0)
