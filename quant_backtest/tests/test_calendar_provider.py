from datetime import date, datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from calendar_provider import (
    CalendarContractError,
    CalendarIdentity,
    CalendarSession,
    SessionSegment,
    FrozenCalendarProvider,
    require_production_provider,
)


VERSION = "a" * 64


def identity(**changes):
    values = dict(
        calendar_ref="registry:calendar-cn-sse",
        version=VERSION,
        market="CN",
        exchange="SSE",
        timezone="Asia/Shanghai",
        coverage_start=date(2026, 10, 16),
        coverage_end=date(2026, 10, 17),
    )
    values.update(changes)
    return CalendarIdentity(**values)


def provider(**changes):
    ident = identity(**changes.pop("identity", {}))
    sessions = [CalendarSession(
        date(2026, 10, 16),
        datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc),
    )]
    return FrozenCalendarProvider(ident, {
        date(2026, 10, 16): True,
        date(2026, 10, 17): False,
    }, sessions, **changes)


def test_verified_provider_uses_explicit_coverage_and_utc_bounds():
    cal = provider()
    assert cal.identity.calendar_ref == "registry:calendar-cn-sse"
    assert cal.is_open(date(2026, 10, 16)) is True
    assert cal.is_open(date(2026, 10, 17)) is False
    assert cal.session_bounds(date(2026, 10, 16)).open_at == datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    assert cal.is_in_session(datetime(2026, 10, 16, 2, tzinfo=timezone.utc)) is True
    assert cal.next_open_session(date(2026, 10, 16), inclusive=True) == date(2026, 10, 16)
    assert cal.next_open_at(datetime(2026, 10, 16, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)


def test_unverified_identity_is_never_production():
    with pytest.raises(CalendarContractError, match="NOT_PRODUCTION_ELIGIBLE"):
        provider(identity={"production_eligible": False})
    with pytest.raises(CalendarContractError, match="NOT_PRODUCTION_ELIGIBLE"):
        provider(identity={"source_authenticated": False})


def test_missing_explicit_day_or_mismatched_sessions_fail_closed():
    ident = identity()
    sessions = [CalendarSession(
        date(2026, 10, 16),
        datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc),
    )]
    with pytest.raises(CalendarContractError, match="COVERAGE_MISSING"):
        FrozenCalendarProvider(ident, {date(2026, 10, 16): True}, sessions)
    with pytest.raises(CalendarContractError, match="disagree"):
        FrozenCalendarProvider(ident, {date(2026, 10, 16): False, date(2026, 10, 17): False}, sessions)


def test_timezone_mismatch_and_out_of_coverage_are_stable_errors():
    with pytest.raises(CalendarContractError, match="TIMEZONE"):
        require_production_provider(provider(identity={"timezone": "UTC"}))
    cal = provider()
    with pytest.raises(CalendarContractError, match="COVERAGE_MISSING"):
        cal.is_open(date(2026, 10, 18))


def test_future_as_of_is_rejected_and_provider_is_immutable():
    cal = provider()
    future = datetime.now(timezone.utc).replace(microsecond=0).replace(year=2999)
    with pytest.raises(CalendarContractError, match="FUTURE_UNVERIFIED"):
        cal.is_open(date(2026, 10, 16), as_of=future)
    with pytest.raises(TypeError):
        cal._days[date(2026, 10, 16)] = False


def split_provider():
    """SSE split session: 09:30-11:30 / 13:00-15:00 Asia/Shanghai (UTC+8)."""
    morning = SessionSegment(
        datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc),
    )
    afternoon = SessionSegment(
        datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc),
    )
    next_day = SessionSegment(
        datetime(2026, 10, 17, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 17, 3, 30, tzinfo=timezone.utc),
    )
    sessions = [
        CalendarSession(date(2026, 10, 16), segments=(morning, afternoon)),
        CalendarSession(date(2026, 10, 17), segments=(next_day,)),
    ]
    return FrozenCalendarProvider(identity(), {
        date(2026, 10, 16): True,
        date(2026, 10, 17): True,
    }, sessions)


def test_split_session_next_open_at_returns_afternoon_segment():
    cal = split_provider()
    # 午休 12:00 本地（04:00 UTC）→ 下午段 13:00（05:00 UTC），而非上午段 09:30
    assert cal.next_open_at(datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
    # 上午收盘后 11:40 本地（03:40 UTC）→ 下午段开盘
    assert cal.next_open_at(datetime(2026, 10, 16, 3, 40, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
    # 下午段内 14:00 本地（06:00 UTC）→ 当前段开盘（与单段语义一致）
    assert cal.next_open_at(datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
    # 当日收盘后 15:30 本地（07:30 UTC）→ 次日开盘
    assert cal.next_open_at(datetime(2026, 10, 16, 7, 30, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 17, 1, 30, tzinfo=timezone.utc)


def test_split_session_is_in_session_and_bounds():
    cal = split_provider()
    bounds = cal.session_bounds(date(2026, 10, 16))
    assert bounds.open_at == datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    assert bounds.close_at == datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc)
    # 午休时段不在交易段内
    assert cal.is_in_session(datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)) is False
    # 两个分段都在交易段内
    assert cal.is_in_session(datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)) is True
    assert cal.is_in_session(datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)) is True
