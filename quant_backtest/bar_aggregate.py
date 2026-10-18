# -*- coding: utf-8 -*-
"""Exchange-session minute-bar aggregation with no future leakage."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, List, Optional

from bar_dto import Bar, ensure_utc
from market_policy import get_policy, timezone_for

_SSE_SZSE = frozenset({"SSE", "SZSE", "SH", "SZ"})


def _local_dt(dt: datetime, market: str = "CN") -> datetime:
    policy = get_policy(market)
    return ensure_utc(dt).astimezone(timezone_for(policy.timezone))


def _session_close(local_day: date, local_timeframe: str, minutes: int) -> datetime:
    """Return UTC close for the bucket containing a local market timestamp."""
    if local_timeframe.endswith("m"):
        size = int(local_timeframe[:-1])
    else:
        raise ValueError("timeframe must be minute-based, e.g. 5m")
    minute = minutes // size * size
    if time(9, 30) <= time(minutes // 60, minutes % 60) < time(11, 30):
        start = datetime.combine(local_day, time(9, 30)) + timedelta(minutes=minute - 9 * 60 - 30 + 9 * 60 + 30)
    elif time(13, 0) <= time(minutes // 60, minutes % 60) < time(15, 0):
        start_minutes = 13 * 60
        start = datetime.combine(local_day, time(13, 0)) + timedelta(minutes=minute - start_minutes)
    else:
        raise ValueError("timestamp outside SSE/SZSE sessions")
    return (start + timedelta(minutes=size)).replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)


def _bucket_start(local: datetime, timeframe: str, market: str) -> datetime:
    size = int(timeframe[:-1])
    policy = get_policy(market)
    if market.upper() in _SSE_SZSE:
        sessions = policy.sessions
    else:
        sessions = policy.sessions
    minute = local.hour * 60 + local.minute
    for session in sessions:
        start_text, end_text = session.split("-", 1)
        sh, sm = (int(part) for part in start_text.split(":", 1))
        eh, em = (int(part) for part in end_text.split(":", 1))
        start_minutes, end_minutes = sh * 60 + sm, eh * 60 + em
        if start_minutes == 0 and end_minutes == 24 * 60:
            return datetime.combine(local.date(), time(0, 0)) + timedelta(minutes=(minute // size) * size)
        if start_minutes <= minute < end_minutes:
            offset = minute - start_minutes
            return datetime.combine(local.date(), time(sh, sm)) + timedelta(minutes=(offset // size) * size)
    raise ValueError("timestamp outside market sessions")


class SessionAggregator:
    """Aggregate minute bars strictly within mainland equity sessions."""

    def __init__(self, timeframe: str = "5m") -> None:
        if not timeframe.endswith("m") or int(timeframe[:-1]) <= 0:
            raise ValueError("timeframe must be positive minute duration")
        self.timeframe = timeframe

    def aggregate(self, bars: Iterable[Bar], as_of: Optional[datetime] = None) -> List[Bar]:
        """Aggregate bars; incomplete/future buckets remain closed=False."""
        source_bars = list(bars)
        if not source_bars:
            return []
        effective_as_of = ensure_utc(as_of, "as_of") if as_of is not None else max(item.dt for item in source_bars)
        groups = {}
        for bar in source_bars:
            local = _local_dt(bar.dt, bar.market)
            try:
                start_local = _bucket_start(local, self.timeframe, bar.market)
            except ValueError:
                continue
            key = (bar.symbol, bar.market, start_local)
            groups.setdefault(key, []).append(bar)
        result: List[Bar] = []
        for (symbol, market, start_local), items in groups.items():
            items.sort(key=lambda item: item.dt)
            size = int(self.timeframe[:-1])
            market_policy = get_policy(market)
            local_end = None
            minute = start_local.hour * 60 + start_local.minute
            for session in market_policy.sessions:
                start_text, end_text = session.split("-", 1)
                sh, sm = (int(part) for part in start_text.split(":", 1))
                eh, em = (int(part) for part in end_text.split(":", 1))
                start_minutes, end_minutes = sh * 60 + sm, eh * 60 + em
                if start_minutes <= minute < end_minutes:
                    local_end = datetime.combine(start_local.date(), time(eh % 24, em))
                    break
            if local_end is None:
                raise ValueError("timestamp outside market sessions")
            expected_close_local = min(start_local + timedelta(minutes=size), local_end)
            expected_close = expected_close_local.replace(tzinfo=timezone_for(market_policy.timezone)).astimezone(timezone.utc)
            closed = effective_as_of >= expected_close
            first, last = items[0], items[-1]
            values = [item for item in items if item.c is not None]
            result.append(Bar(symbol=symbol, market=market, timeframe=self.timeframe,
                              dt=start_local.replace(tzinfo=timezone_for(market_policy.timezone)).astimezone(timezone.utc),
                              session_date=start_local.date(), o=first.o,
                              h=max((item.h for item in items if item.h is not None), default=None),
                              l=min((item.l for item in items if item.l is not None), default=None),
                              c=last.c, v=sum((item.v for item in items if item.v is not None), 0.0) if any(item.v is not None for item in items) else None,
                              amount=sum((item.amount for item in items if item.amount is not None), 0.0) if any(item.amount is not None for item in items) else None,
                              close_at=expected_close, available_at=max((item.available_at or item.dt for item in items)),
                              closed=closed, source=first.source, source_version=first.source_version,
                              adjustment=first.adjustment, quality="complete" if closed else "partial",
                              source_trace=tuple(dict.fromkeys(trace for item in items for trace in
                                                              (item.source_trace or (item.source,))))))
        return sorted(result, key=lambda item: (item.dt, item.symbol))
