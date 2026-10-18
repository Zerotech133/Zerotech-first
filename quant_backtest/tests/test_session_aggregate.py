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


def _local(hour, minute):
    from datetime import timedelta
    return datetime(2026, 10, 16, hour, minute,
                    tzinfo=timezone(timedelta(hours=8)))


def test_non_divisor_timeframe_close_at_is_clipped_to_lunch_close():
    # 90m 桶 11:00 起：11:00+90m=12:30 跨午休 → close_at 裁剪到上午收盘 11:30
    bars = [bar(9, 30), bar(10, 0), bar(11, 0), bar(11, 29)]
    result = SessionAggregator("90m").aggregate(
        bars, datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc))
    assert len(result) == 2
    second = result[1]
    assert second.dt == _local(11, 0)
    assert second.close_at == _local(11, 30)
    assert second.closed is True


def test_non_divisor_timeframe_close_at_is_clipped_to_session_close():
    # 150m 桶 09:30 起：09:30+150m=12:00 跨午休 → close_at=11:30
    bars = [bar(9, 30), bar(10, 30), bar(11, 0)]
    result = SessionAggregator("150m").aggregate(
        bars, datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc))
    assert len(result) == 1
    assert result[0].close_at == _local(11, 30)
    # 下午段 13:00 起 150m：15:30 越过收盘 → close_at=15:00
    afternoon = SessionAggregator("150m").aggregate(
        [bar(13, 0), bar(14, 0)], datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc))
    assert len(afternoon) == 1
    assert afternoon[0].close_at == _local(15, 0)
