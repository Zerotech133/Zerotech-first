# -*- coding: utf-8 -*-
"""Small in-memory historical BarStore with strict as-of visibility."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from bar_dto import Bar, BarQuery
from bar_sources import SourceReadError


class BarStore:
    """Read-only collection of canonical bars for replay/backtest queries."""

    def __init__(self, bars: Iterable[Bar] = (), metadata: Dict[str, Any] | None = None) -> None:
        self._bars: Tuple[Bar, ...] = tuple(bars)
        self._metadata: Dict[str, Any] = dict(metadata or {})
        self._validate()

    def _validate(self) -> None:
        for bar in self._bars:
            if not isinstance(bar, Bar):
                raise TypeError("BarStore accepts only Bar instances")
        identities = {(bar.calendar_ref, bar.calendar_version) for bar in self._bars}
        if len(identities) > 1:
            raise ValueError("CALENDAR_IDENTITY_MISMATCH: mixed calendar identity")
        ref = self._metadata.get("calendar_ref")
        version = self._metadata.get("calendar_version")
        if (ref is None) != (version is None):
            raise ValueError("CALENDAR_IDENTITY_MISMATCH: incomplete metadata identity")
        if ref is not None and identities and identities != {(ref, version)}:
            raise ValueError("CALENDAR_IDENTITY_MISMATCH: metadata identity differs")

    def calendar_identity(self) -> Tuple[str, str] | None:
        """Return the homogeneous identity bound to this store, if any."""
        ref = self._metadata.get("calendar_ref")
        version = self._metadata.get("calendar_version")
        if ref is not None:
            return str(ref), str(version)
        identities = {(bar.calendar_ref, bar.calendar_version) for bar in self._bars}
        if identities and identities != {(None, None)}:
            return next(iter(identities))
        return None

    def validate_calendar_identity(self, calendar_ref: str, calendar_version: str) -> None:
        """Require the store identity to match the expected calendar snapshot."""
        actual = self.calendar_identity()
        if actual is None:
            raise ValueError("CALENDAR_UNAVAILABLE: calendar identity missing")
        if actual != (calendar_ref, calendar_version):
            raise ValueError("CALENDAR_IDENTITY_MISMATCH: calendar identity differs")

    def add(self, bar: Bar) -> "BarStore":
        """Return a new store with one bar; the current store is unchanged."""
        if not isinstance(bar, Bar):
            raise TypeError("bar must be a Bar instance")
        return BarStore(self._bars + (bar,), self._metadata)

    def query(self, query: BarQuery) -> Tuple[Bar, ...]:
        """Return bars visible under the supplied query contract."""
        if not isinstance(query, BarQuery):
            raise TypeError("query must be a BarQuery")
        return query.apply(self._bars)

    def explain(self) -> Dict[str, Any]:
        """Return source/coverage metadata for this store."""
        sources = sorted({item for bar in self._bars for item in (bar.source_trace or (bar.source,))})
        result: Dict[str, Any] = dict(self._metadata)
        result.setdefault("sources", sources)
        result.setdefault("rows", len(self._bars))
        result.setdefault("coverage", "complete" if self._bars else "missing")
        result.setdefault("missing", [] if self._bars else ["no_bars"])
        return result

    def all(self) -> Tuple[Bar, ...]:
        """Return deterministic all-bars view without exposing mutable storage."""
        return tuple(sorted(self._bars, key=lambda item: (item.dt, item.symbol, item.timeframe)))

    @classmethod
    def from_source(cls, source: object, query: BarQuery, **kwargs: object) -> "BarStore":
        """Load one explicitly selected source; failures are never hidden."""
        reader = getattr(source, "read", None)
        if not callable(reader):
            raise TypeError("source must provide read(query, ...)" )
        try:
            bars = reader(query, **kwargs)
        except SourceReadError:
            raise
        except Exception as exc:
            raise SourceReadError(type(source).__name__, str(exc)) from exc
        metadata = getattr(source, "explain", lambda: {})()
        return cls(bars, metadata if isinstance(metadata, dict) else {})
