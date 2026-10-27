"""Production-safe trading calendar identity and provider contract.

This module deliberately does not derive holidays from weekdays or from bar
availability.  A provider must carry an explicit date-status snapshot and a
verified identity before it can be used by a production execution path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Protocol, Tuple
from zoneinfo import ZoneInfo


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REF = re.compile(r"^registry:calendar-[a-z0-9][a-z0-9_-]{1,95}$")


class CalendarContractError(ValueError):
    """Stable machine-readable calendar contract failure."""

    def __init__(self, code: str, detail: str, message: str | None = None) -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(
            f"{self.code}:{self.detail}" + (f":{message}" if message else "")
        )


@dataclass(frozen=True)
class CalendarIdentity:
    calendar_ref: str
    version: str
    market: str
    exchange: str
    timezone: str
    coverage_start: date
    coverage_end: date
    source_authenticated: bool = True
    exception_completeness_verified: bool = True
    production_eligible: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.calendar_ref, str) or not _REF.fullmatch(
            self.calendar_ref
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "calendar_ref is invalid"
            )
        if not isinstance(self.version, str) or not _SHA256.fullmatch(self.version):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "calendar version must be SHA256"
            )
        market = str(self.market).upper()
        if market not in {"CN", "US", "HK", "CRYPTO"}:
            raise CalendarContractError(
                "CALENDAR_MISMATCH", "MARKET", "unsupported calendar market"
            )
        object.__setattr__(self, "market", market)
        if not isinstance(self.exchange, str) or not self.exchange.strip():
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "exchange is required"
            )
        if not isinstance(self.timezone, str) or not self.timezone.strip():
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "timezone is required"
            )
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise CalendarContractError(
                "CALENDAR_MISMATCH", "TIMEZONE", "timezone data unavailable"
            ) from exc
        if not isinstance(self.coverage_start, date) or isinstance(
            self.coverage_start, datetime
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "coverage_start must be a date"
            )
        if not isinstance(self.coverage_end, date) or isinstance(
            self.coverage_end, datetime
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "coverage_end must be a date"
            )
        if self.coverage_start > self.coverage_end:
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "coverage is reversed"
            )
        for name in (
            "source_authenticated",
            "exception_completeness_verified",
            "production_eligible",
        ):
            if type(getattr(self, name)) is not bool:
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE", "INVALID", f"{name} must be boolean"
                )


@dataclass(frozen=True)
class SessionSegment:
    """One contiguous, observable market interval in UTC."""

    open_at: datetime
    close_at: datetime

    def __post_init__(self) -> None:
        for field in ("open_at", "close_at"):
            value = getattr(self, field)
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE", "INVALID", f"{field} must be timezone-aware"
                )
            object.__setattr__(self, field, value.astimezone(timezone.utc))
        if self.open_at >= self.close_at:
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "session segment bounds are reversed"
            )
        if self.open_at.date() > self.close_at.date():
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE",
                "INVALID",
                "overnight sessions are unsupported in P0",
            )


@dataclass(frozen=True)
class CalendarSession:
    session_date: date
    open_at: Optional[datetime] = None
    close_at: Optional[datetime] = None
    segments: Tuple[SessionSegment, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.session_date, date) or isinstance(
            self.session_date, datetime
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "session_date must be a date"
            )
        # Preserve the legacy three-argument constructor as one segment while
        # allowing segment-only construction for split sessions.
        if self.segments:
            normalized = tuple(self.segments)
            if self.open_at is not None or self.close_at is not None:
                if self.open_at is None or self.close_at is None:
                    raise CalendarContractError(
                        "CALENDAR_UNAVAILABLE",
                        "INVALID",
                        "open_at and close_at must be paired",
                    )
        else:
            if self.open_at is None or self.close_at is None:
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE", "INVALID", "session bounds are required"
                )
            normalized = (SessionSegment(self.open_at, self.close_at),)
        if not all(isinstance(item, SessionSegment) for item in normalized):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE",
                "INVALID",
                "segments must contain SessionSegment",
            )
        ordered = tuple(sorted(normalized, key=lambda item: item.open_at))
        for previous, current in zip(ordered, ordered[1:]):
            if current.open_at < previous.close_at:
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE", "INVALID", "session segments overlap"
                )
        if ordered[0].open_at.date() > ordered[-1].close_at.date():
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE",
                "INVALID",
                "overnight sessions are unsupported in P0",
            )
        object.__setattr__(self, "segments", ordered)
        object.__setattr__(self, "open_at", ordered[0].open_at)
        object.__setattr__(self, "close_at", ordered[-1].close_at)


class CalendarProvider(Protocol):
    @property
    def identity(self) -> CalendarIdentity: ...

    def is_open(
        self, session_date: date, *, as_of: Optional[datetime] = None
    ) -> bool: ...

    def session_bounds(
        self, session_date: date, *, as_of: Optional[datetime] = None
    ) -> CalendarSession: ...

    def is_in_session(
        self,
        value: datetime,
        *,
        session_date: Optional[date] = None,
        as_of: Optional[datetime] = None,
    ) -> bool: ...

    def next_open_session(
        self,
        session_date: date,
        *,
        inclusive: bool = False,
        as_of: Optional[datetime] = None,
    ) -> date: ...

    def next_open_at(
        self, value: datetime, *, as_of: Optional[datetime] = None
    ) -> datetime: ...


class FrozenCalendarProvider:
    """Immutable provider backed by an explicit, verified date-status map.

    ``days`` must contain every natural date in the declared coverage, with a
    boolean status.  This is validation of an explicit snapshot, not a holiday
    inference rule.  ``sessions`` must contain bounds for every open date.
    """

    def __init__(
        self,
        identity: CalendarIdentity,
        days: Mapping[date, bool],
        sessions: Iterable[CalendarSession],
        *,
        require_production: bool = True,
    ) -> None:
        if not isinstance(identity, CalendarIdentity):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "identity is required"
            )
        validate_identity(identity, require_production=require_production)
        if not isinstance(days, Mapping):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "days must be a mapping"
            )
        normalized_days = {}
        cursor = identity.coverage_start
        while cursor <= identity.coverage_end:
            if cursor not in days or type(days[cursor]) is not bool:
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE",
                    "COVERAGE_MISSING",
                    "days must explicitly cover every date",
                )
            normalized_days[cursor] = days[cursor]
            cursor += timedelta(days=1)
        if set(days) != set(normalized_days):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE",
                "INVALID",
                "days contains dates outside coverage",
            )
        session_map = {}
        for session in sessions:
            if not isinstance(session, CalendarSession):
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE",
                    "INVALID",
                    "sessions must contain CalendarSession",
                )
            if session.session_date in session_map:
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE", "INVALID", "duplicate session date"
                )
            session_map[session.session_date] = session
        for session_date, open_flag in normalized_days.items():
            if open_flag != (session_date in session_map):
                raise CalendarContractError(
                    "CALENDAR_UNAVAILABLE",
                    "INVALID",
                    "day status and session bounds disagree",
                )
        if any(
            item < identity.coverage_start or item > identity.coverage_end
            for item in session_map
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "session outside coverage"
            )
        self._identity = identity
        self._days = MappingProxyType(dict(normalized_days))
        self._sessions = MappingProxyType(dict(session_map))
        self._session_dates = tuple(sorted(session_map))

    @property
    def identity(self) -> CalendarIdentity:
        return self._identity

    def _check_date(self, value: date) -> date:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "session_date must be a date"
            )
        if value < self._identity.coverage_start or value > self._identity.coverage_end:
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE",
                "COVERAGE_MISSING",
                "date is outside calendar coverage",
            )
        return value

    @staticmethod
    def _check_as_of(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "as_of must be timezone-aware"
            )
        normalized = value.astimezone(timezone.utc)
        if normalized > datetime.now(timezone.utc):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "FUTURE_UNVERIFIED", "as_of cannot be in future"
            )
        return normalized

    def is_open(self, session_date: date, *, as_of: Optional[datetime] = None) -> bool:
        self._check_as_of(as_of)
        return self._days[self._check_date(session_date)]

    def session_bounds(
        self, session_date: date, *, as_of: Optional[datetime] = None
    ) -> CalendarSession:
        self._check_as_of(as_of)
        day = self._check_date(session_date)
        if not self._days[day]:
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "COVERAGE_MISSING", "date is closed"
            )
        return self._sessions[day]

    def is_in_session(
        self,
        value: datetime,
        *,
        session_date: Optional[date] = None,
        as_of: Optional[datetime] = None,
    ) -> bool:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "datetime must be timezone-aware"
            )
        self._check_as_of(as_of)
        instant = value.astimezone(timezone.utc)
        local_day = instant.astimezone(ZoneInfo(self.identity.timezone)).date()
        day = self._check_date(session_date if session_date is not None else local_day)
        if session_date is not None and day != local_day:
            return False
        if not self._days[day]:
            return False
        bounds = self._sessions[day]
        return any(
            segment.open_at <= instant < segment.close_at for segment in bounds.segments
        )

    def session_segments(
        self, session_date: date, *, as_of: Optional[datetime] = None
    ) -> Tuple[SessionSegment, ...]:
        """Return immutable contiguous segments for one open session."""
        self._check_as_of(as_of)
        day = self._check_date(session_date)
        if not self._days[day]:
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "COVERAGE_MISSING", "date is closed"
            )
        return self._sessions[day].segments

    def next_open_session(
        self,
        session_date: date,
        *,
        inclusive: bool = False,
        as_of: Optional[datetime] = None,
    ) -> date:
        self._check_as_of(as_of)
        day = self._check_date(session_date)
        for candidate in self._session_dates:
            if candidate > day or (inclusive and candidate == day):
                return candidate
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE",
            "COVERAGE_MISSING",
            "no next open session in coverage",
        )

    def next_open_at(
        self, value: datetime, *, as_of: Optional[datetime] = None
    ) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise CalendarContractError(
                "CALENDAR_UNAVAILABLE", "INVALID", "datetime must be timezone-aware"
            )
        self._check_as_of(as_of)
        instant = value.astimezone(timezone.utc)
        local_day = instant.astimezone(ZoneInfo(self.identity.timezone)).date()
        self._check_date(local_day)
        for candidate in self._session_dates:
            bounds = self._sessions[candidate]
            for segment in bounds.segments:
                if segment.open_at >= instant:
                    return segment.open_at
                if segment.open_at <= instant < segment.close_at:
                    return segment.open_at
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE",
            "COVERAGE_MISSING",
            "no next open session in coverage",
        )


def validate_identity(
    identity: CalendarIdentity, *, market_policy=None, require_production: bool = True
) -> CalendarIdentity:
    if not isinstance(identity, CalendarIdentity):
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE", "INVALID", "invalid calendar identity"
        )
    if require_production and not (
        identity.source_authenticated
        and identity.exception_completeness_verified
        and identity.production_eligible
    ):
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE",
            "NOT_PRODUCTION_ELIGIBLE",
            "calendar identity is not production eligible",
        )
    if market_policy is None:
        from market_policy import get_policy

        market_policy = get_policy(identity.market)
    if identity.timezone != market_policy.timezone:
        raise CalendarContractError(
            "CALENDAR_MISMATCH",
            "TIMEZONE",
            "calendar timezone does not match market policy",
        )
    return identity


def require_production_provider(
    provider: CalendarProvider, *, market_policy=None
) -> CalendarIdentity:
    if provider is None or not hasattr(provider, "identity"):
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE",
            "PROVIDER_UNAVAILABLE",
            "calendar provider is required",
        )
    try:
        identity = provider.identity
        return validate_identity(
            identity, market_policy=market_policy, require_production=True
        )
    except CalendarContractError:
        raise
    except Exception as exc:
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE", "INVALID", "calendar provider identity is invalid"
        ) from exc


def validate_coverage(provider: CalendarProvider, start: date, end: date) -> None:
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(end, date)
        or isinstance(end, datetime)
    ):
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE", "INVALID", "coverage bounds must be dates"
        )
    if start > end:
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE", "INVALID", "coverage bounds are reversed"
        )
    identity = require_production_provider(provider)
    if start < identity.coverage_start or end > identity.coverage_end:
        raise CalendarContractError(
            "CALENDAR_UNAVAILABLE",
            "COVERAGE_MISSING",
            "requested range is outside coverage",
        )
    if hasattr(provider, "sessions"):
        # A provider may implement a richer bulk API; P0 only requires explicit
        # date checks and never synthesizes dates.
        for day in (start, end):
            provider.is_open(day)
