from dataclasses import replace
from datetime import date, datetime, timezone
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from bar_dto import Bar
from bar_store import BarStore
from calendar_provider import CalendarIdentity, CalendarSession, FrozenCalendarProvider, SessionSegment
from execution_engine import ExecutionConfig, OrderIntent, run_from_store

VERSION = "a" * 64

def calendar_provider(open_day=True):
    ident = CalendarIdentity("registry:calendar-cn-sse", VERSION, "CN", "SSE", "Asia/Shanghai", date(2026,10,16), date(2026,10,16))
    days = {date(2026,10,16): open_day}
    sessions = [CalendarSession(date(2026,10,16), datetime(2026,10,16,1,30,tzinfo=timezone.utc), datetime(2026,10,16,7,tzinfo=timezone.utc))] if open_day else []
    return FrozenCalendarProvider(ident, days, sessions)

def make_bar(minute, ref="registry:calendar-cn-sse", version=VERSION, session=date(2026,10,16)):
    dt=datetime(2026,10,16,1,minute,tzinfo=timezone.utc)
    return Bar("000001.SZ", "CN", "1m", dt, session, 10,10,10,10,100,1000,dt,dt,True,"fixture","v1","none","observed",("fixture",),ref,version)


def make_at(dt, ref="registry:calendar-cn-sse", version=VERSION, session=date(2026,10,16)):
    return Bar("000001.SZ", "CN", "1m", dt, session, 10,10,10,10,100,1000,dt,dt,True,"fixture","v1","none","observed",("fixture",),ref,version)

def config(**kwargs):
    values = dict(initial_cash=2000, policy_ref="registry:market-cn", calendar_required=True, calendar_ref="registry:calendar-cn-sse", calendar_version=VERSION)
    values.update(kwargs)
    return ExecutionConfig(**values)

def test_verified_calendar_fills_and_echoes_identity():
    bars=[make_bar(30),make_bar(31)]
    result=run_from_store(BarStore(bars), [OrderIntent("000001.SZ","BUY",100,bars[0].dt)], config(), status_provider=lambda *a:"TRADING", calendar_provider=calendar_provider())
    assert result.fills
    assert result.query_metadata["calendar"]["calendar_ref"] == "registry:calendar-cn-sse"
    assert result.source_metadata["calendar"]["calendar_version"] == VERSION
    assert result.audits[0].query_metadata["calendar"]["market"] == "CN"

def test_required_calendar_missing_or_closed_fails_closed():
    bars=[make_bar(30),make_bar(31)]
    with pytest.raises(ValueError, match="CALENDAR_UNAVAILABLE"):
        run_from_store(BarStore(bars), [OrderIntent("000001.SZ","BUY",100,bars[0].dt)], config(), status_provider=lambda *a:"TRADING")
    with pytest.raises(ValueError, match="CALENDAR_UNAVAILABLE"):
        run_from_store(BarStore(bars), [OrderIntent("000001.SZ","BUY",100,bars[0].dt)], config(), status_provider=lambda *a:"TRADING", calendar_provider=calendar_provider(False))

def test_split_session_rejects_lunch_bar_and_allows_afternoon_bar():
    ident = CalendarIdentity("registry:calendar-cn-sse", VERSION, "CN", "SSE", "Asia/Shanghai", date(2026, 10, 16), date(2026, 10, 16))
    morning = SessionSegment(datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc), datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc))
    afternoon = SessionSegment(datetime(2026, 10, 16, 5, tzinfo=timezone.utc), datetime(2026, 10, 16, 7, tzinfo=timezone.utc))
    provider = FrozenCalendarProvider(ident, {date(2026, 10, 16): True}, [CalendarSession(date(2026, 10, 16), segments=(morning, afternoon))])
    lunch = make_at(datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc))
    afternoon_bar = make_at(datetime(2026, 10, 16, 5, 30, tzinfo=timezone.utc))
    afternoon_next = make_at(datetime(2026, 10, 16, 5, 40, tzinfo=timezone.utc))
    store = BarStore([lunch, afternoon_bar, afternoon_next])
    cfg = config()
    with pytest.raises(ValueError, match="CALENDAR_UNAVAILABLE: OUT_OF_SESSION"):
        run_from_store(store, [OrderIntent("000001.SZ", "BUY", 100, lunch.dt)], cfg, status_provider=lambda *a: "TRADING", calendar_provider=provider)
    result = run_from_store(store, [OrderIntent("000001.SZ", "BUY", 100, afternoon_bar.dt)], cfg, status_provider=lambda *a: "TRADING", calendar_provider=provider)
    assert result.fills


def test_early_close_and_bar_close_boundary_are_rejected():
    ident = CalendarIdentity("registry:calendar-cn-sse", VERSION, "CN", "SSE", "Asia/Shanghai", date(2026, 10, 16), date(2026, 10, 16))
    segment = SessionSegment(datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc), datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc))
    provider = FrozenCalendarProvider(ident, {date(2026, 10, 16): True}, [CalendarSession(date(2026, 10, 16), segments=(segment,))])
    signal = make_at(datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc))
    late = make_at(datetime(2026, 10, 16, 1, 40, tzinfo=timezone.utc))
    late = replace(late, close_at=datetime(2026, 10, 16, 2, 5, tzinfo=timezone.utc))
    result = run_from_store(BarStore([signal, late]), [OrderIntent("000001.SZ", "BUY", 100, signal.dt)], config(), status_provider=lambda *a: "TRADING", calendar_provider=provider)
    assert not result.fills
    assert result.audits[0].reason in {"NO_NEXT_BAR", "SIGNAL_BAR_NOT_CLOSED"}


def test_identity_mismatch_fails_closed():
    bars=[make_bar(30),make_bar(31)]
    with pytest.raises(ValueError, match="CALENDAR_MISMATCH"):
        run_from_store(BarStore(bars), [OrderIntent("000001.SZ","BUY",100,bars[0].dt)], config(calendar_version="b"*64), status_provider=lambda *a:"TRADING", calendar_provider=calendar_provider())
