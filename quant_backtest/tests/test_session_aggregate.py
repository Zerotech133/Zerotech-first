# -*- coding: utf-8 -*-
from datetime import date, datetime, timezone, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_aggregate import SessionAggregator
from bar_dto import Bar


def bar(hour, minute, close=1.0):
    dt = datetime(2026, 10, 16, hour, minute, tzinfo=timezone(timedelta(hours=8)))
    return Bar("BK0001", "SSE", "1m", dt, date(2026, 10, 16), close, close, close, close, 0, 0, dt, dt, True, "test", "v1", "none", "ok")


def test_session_bucket_does_not_cross_lunch_and_zero_volume_is_real():
    from datetime import timedelta
    bars = [bar(9, 30), bar(9, 31), bar(13, 0)]
    result = SessionAggregator("5m").aggregate(bars, datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc))
    assert len(result) == 2
    assert result[0].v == 0
    assert result[0].closed is True
    assert result[0].source_trace == ("test",)


def test_unfinished_bucket_is_open_and_weekend_outside_session_ignored():
    from datetime import timedelta
    bars = [bar(9, 30), bar(9, 31)]
    result = SessionAggregator("5m").aggregate(bars, datetime(2026, 10, 16, 1, 32, tzinfo=timezone.utc))
    assert len(result) == 1
    assert result[0].closed is False
