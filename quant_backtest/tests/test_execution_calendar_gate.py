from datetime import date, datetime, timezone
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from bar_dto import Bar
from bar_store import BarStore
from calendar_provider import CalendarIdentity, CalendarSession, FrozenCalendarProvider
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

def test_identity_mismatch_fails_closed():
    bars=[make_bar(30),make_bar(31)]
    with pytest.raises(ValueError, match="CALENDAR_MISMATCH"):
        run_from_store(BarStore(bars), [OrderIntent("000001.SZ","BUY",100,bars[0].dt)], config(calendar_version="b"*64), status_provider=lambda *a:"TRADING", calendar_provider=calendar_provider())
