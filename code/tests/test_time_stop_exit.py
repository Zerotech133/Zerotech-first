"""Task 4: 时间止损 helper 单元测试。"""
from datetime import UTC, date

from zhice_os_v2.application.services.workflow_service import _time_stop_hit


def test_time_stop_hit_at_threshold():
    cal = {date(2026, 1, d): True for d in range(5, 30)}
    assert _time_stop_hit(cal, date(2026, 1, 5), date(2026, 1, 19), 15) is True
    assert _time_stop_hit(cal, date(2026, 1, 5), date(2026, 1, 18), 15) is False
    assert _time_stop_hit(cal, date(2026, 1, 5), date(2026, 1, 19), None) is False


def test_time_stop_hit_zero_disables():
    """max_hold_days=0 视为不启用（falsy）。"""
    cal = {date(2026, 1, d): True for d in range(1, 30)}
    assert _time_stop_hit(cal, date(2026, 1, 1), date(2026, 1, 20), 0) is False


def test_time_stop_hit_accepts_datetime():
    """opened/as_of 传入 datetime 时自动取 .date()。"""
    from datetime import datetime

    cal = {date(2026, 2, d): True for d in range(1, 16)}
    opened = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
    as_of = datetime(2026, 2, 15, 15, 0, tzinfo=UTC)
    assert _time_stop_hit(cal, opened, as_of, 15) is True


def test_time_stop_hit_below_threshold():
    """未达阈值时返回 False。"""
    cal = {date(2026, 3, d): True for d in range(1, 30)}
    assert _time_stop_hit(cal, date(2026, 3, 1), date(2026, 3, 14), 15) is False


def test_time_stop_hit_none_opened():
    """opened=None 时恒返回 False，不抛 TypeError。"""
    cal = {date(2026, 1, d): True for d in range(5, 30)}
    assert _time_stop_hit(cal, None, date(2026, 1, 19), 15) is False
