# -*- coding: utf-8 -*-
"""Canonical, timezone-safe bar DTOs for historical replay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Optional, Tuple


def ensure_utc(value: datetime, field_name: str = "datetime") -> datetime:
    """Require an aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Bar:
    """Immutable normalized OHLCV bar."""

    symbol: str
    market: str
    timeframe: str
    dt: datetime
    session_date: date
    o: Optional[float]
    h: Optional[float]
    l: Optional[float]
    c: Optional[float]
    v: Optional[float]
    amount: Optional[float]
    close_at: Optional[datetime]
    available_at: Optional[datetime]
    closed: bool
    source: str
    source_version: str
    adjustment: str
    quality: str
    source_trace: Tuple[str, ...] = ()
    calendar_ref: Optional[str] = None
    calendar_version: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "dt", ensure_utc(self.dt, "dt"))
        if self.close_at is not None:
            object.__setattr__(self, "close_at", ensure_utc(self.close_at, "close_at"))
        if self.available_at is not None:
            object.__setattr__(self, "available_at", ensure_utc(self.available_at, "available_at"))
        if not self.symbol or not self.market or not self.timeframe:
            raise ValueError("symbol, market, and timeframe are required")
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise ValueError("session_date must be a date")
        if self.v is not None and self.v < 0:
            raise ValueError("volume cannot be negative")
        if self.amount is not None and self.amount < 0:
            raise ValueError("amount cannot be negative")
        object.__setattr__(self, "source_trace", tuple(str(item) for item in self.source_trace))
        if (self.calendar_ref is None) != (self.calendar_version is None):
            raise ValueError("calendar_ref and calendar_version must be provided together")
        for name in ("calendar_ref", "calendar_version"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class BarQuery:
    """Time and closure constraints for selecting canonical bars."""

    start: Optional[datetime] = None
    end: Optional[datetime] = None
    as_of: Optional[datetime] = None
    closed_only: bool = False

    def __post_init__(self) -> None:
        for name in ("start", "end", "as_of"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, name))
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        if self.as_of is not None and self.as_of > datetime.now(timezone.utc):
            raise ValueError("as_of cannot be in the future")

    def apply(self, bars: Iterable[Bar]) -> Tuple[Bar, ...]:
        """Return bars satisfying this query in deterministic order."""
        selected = []
        for bar in bars:
            if self.start is not None and bar.dt < self.start:
                continue
            if self.end is not None and bar.dt > self.end:
                continue
            if self.as_of is not None:
                if bar.dt > self.as_of:
                    continue
                if bar.available_at is not None and bar.available_at > self.as_of:
                    continue
                if bar.close_at is not None and bar.close_at > self.as_of:
                    continue
            if self.closed_only and (not bar.closed or
                                     (bar.close_at is not None and
                                      (self.as_of is None or bar.close_at > self.as_of))):
                continue
            selected.append(bar)
        return tuple(sorted(selected, key=lambda item: (item.dt, item.symbol, item.timeframe)))
