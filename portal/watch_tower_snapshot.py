# -*- coding: utf-8 -*-
"""Read-only Watch Tower snapshot aggregation.

This module deliberately consumes persisted/runtime observations only. It does
not start scanners, write caches, or read the prototype realtime placeholders.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from watch_tower_linkage import LinkageReader


SCHEMA = "watch_tower.snapshot.v1"
DEFAULT_TTL_SEC = 15
DEFAULT_POLL_AFTER_SEC = 10
MAX_BODY_BYTES = 256 * 1024


class ReplayArgumentError(ValueError):
    """Raised when a historical replay timestamp is invalid."""


def parse_replay_at(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 replay instant and return it normalized to UTC.

    Date-only values are accepted as a convenient day selector and interpreted
    as midnight UTC. Datetime values must carry an explicit timezone offset;
    accepting a naive datetime would make historical boundaries ambiguous.
    Future instants are rejected so the replay path can never masquerade as a
    live snapshot.
    """
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            parsed = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ReplayArgumentError("replay_at must include a timezone offset")
            parsed = parsed.astimezone(timezone.utc)
    except ReplayArgumentError:
        raise
    except (TypeError, ValueError) as exc:
        raise ReplayArgumentError("replay_at must be ISO-8601") from exc
    if parsed > datetime.now(timezone.utc):
        raise ReplayArgumentError("replay_at cannot be in the future")
    return parsed


def _iso_from_timestamp(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _json_file(path: Path) -> Tuple[Optional[Any], Optional[str], Optional[float]]:
    try:
        stat = path.stat()
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value, None, stat.st_mtime
    except FileNotFoundError:
        return None, "missing", None
    except (OSError, UnicodeError):
        return None, "read_error", None
    except json.JSONDecodeError:
        return None, "invalid_json", None


def _source(
    name: str,
    *,
    state: str,
    required: bool,
    observed_at: Optional[str],
    age_sec: Optional[float],
    ttl_sec: int,
    source: str,
    source_mode: str,
    fallback_used: bool,
    partial: bool,
    error_code: Optional[str],
    note: str,
    data: Any,
) -> Dict[str, Any]:
    return {
        "name": name,
        "state": state,
        "required": required,
        "observed_at": observed_at,
        "age_sec": round(age_sec, 3) if age_sec is not None else None,
        "ttl_sec": ttl_sec,
        "source": source,
        "source_mode": source_mode,
        "fallback_used": fallback_used,
        "partial": partial,
        "error_code": error_code,
        "note": note,
        "data": data,
    }


class SnapshotAggregator:
    """Build and cache a non-mutating Watch Tower snapshot."""

    def __init__(
        self,
        portal_root: Path | str,
        station_root: Path | str,
        ttl_sec: int = DEFAULT_TTL_SEC,
    ) -> None:
        self.portal_root = Path(portal_root).resolve()
        self.station_root = Path(station_root).resolve()
        self.ttl_sec = max(10, min(int(ttl_sec), 15))
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0

    def _file_source(self, name: str, path: Path, required: bool) -> Dict[str, Any]:
        value, error, mtime = _json_file(path)
        now = time.time()
        observed = _iso_from_timestamp(mtime)
        age = now - mtime if mtime is not None else None
        if error == "missing":
            return _source(
                name,
                state="unknown",
                required=required,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source=str(path),
                source_mode="file",
                fallback_used=False,
                partial=False,
                error_code="missing",
                note="Source file is not available",
                data=None,
            )
        if error:
            return _source(
                name,
                state="unknown",
                required=required,
                observed_at=observed,
                age_sec=age,
                ttl_sec=self.ttl_sec,
                source=str(path),
                source_mode="file",
                fallback_used=False,
                partial=False,
                error_code=error,
                note="Source file could not be decoded",
                data=None,
            )
        state = "stale" if age is not None and age > self.ttl_sec else "healthy"
        return _source(
            name,
            state=state,
            required=required,
            observed_at=observed,
            age_sec=age,
            ttl_sec=self.ttl_sec,
            source=str(path),
            source_mode="file",
            fallback_used=False,
            partial=False,
            error_code=None,
            note="Persisted source",
            data=value,
        )

    def _http_source(self, name: str, url: str, required: bool) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            request = Request(url, headers={"Accept": "application/json"}, method="GET")
            with urlopen(request, timeout=0.8) as response:
                body = response.read(MAX_BODY_BYTES + 1)
                if len(body) > MAX_BODY_BYTES:
                    return _source(
                        name,
                        state="degraded",
                        required=required,
                        observed_at=None,
                        age_sec=None,
                        ttl_sec=self.ttl_sec,
                        source=url,
                        source_mode="http",
                        fallback_used=False,
                        partial=False,
                        error_code="body_too_large",
                        note="HTTP response exceeded size limit",
                        data=None,
                    )
                if not 200 <= response.status < 300:
                    return _source(
                        name,
                        state="degraded",
                        required=required,
                        observed_at=None,
                        age_sec=None,
                        ttl_sec=self.ttl_sec,
                        source=url,
                        source_mode="http",
                        fallback_used=False,
                        partial=False,
                        error_code="http_status",
                        note=f"HTTP status {response.status}",
                        data=None,
                    )
                try:
                    value = json.loads(body.decode("utf-8")) if body else None
                except (UnicodeError, json.JSONDecodeError):
                    return _source(
                        name,
                        state="degraded",
                        required=required,
                        observed_at=None,
                        age_sec=None,
                        ttl_sec=self.ttl_sec,
                        source=url,
                        source_mode="http",
                        fallback_used=False,
                        partial=False,
                        error_code="invalid_json",
                        note="HTTP response is not JSON",
                        data=None,
                    )
                observed = datetime.now(timezone.utc).isoformat()
                return _source(
                    name,
                    state="healthy",
                    required=required,
                    observed_at=observed,
                    age_sec=round(time.perf_counter() - started, 3),
                    ttl_sec=self.ttl_sec,
                    source=url,
                    source_mode="http",
                    fallback_used=False,
                    partial=False,
                    error_code=None,
                    note="Live HTTP source",
                    data=value,
                )
        except HTTPError as exc:
            return _source(
                name,
                state="degraded",
                required=required,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source=url,
                source_mode="http",
                fallback_used=False,
                partial=False,
                error_code="http_status",
                note=f"HTTP status {exc.code}",
                data=None,
            )
        except (URLError, TimeoutError, OSError) as exc:
            return _source(
                name,
                state="down",
                required=required,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source=url,
                source_mode="http",
                fallback_used=False,
                partial=False,
                error_code="unavailable",
                note=str(exc),
                data=None,
            )

    def _history_rows(
        self, table: str, day: date
    ) -> Tuple[Optional[list], Optional[str]]:
        """Read one historical table without mutating the production database."""
        db_path = self.station_root / "news_collector" / "news.db"
        if not db_path.exists():
            return None, "missing"
        try:
            uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2.0) as db:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    return None, "missing_table"
                columns = [
                    row[1] for row in db.execute("PRAGMA table_info(%s)" % table)
                ]
                if not columns:
                    return [], None
                date_columns = [
                    name for name in ("date", "ts", "created_at") if name in columns
                ]
                if not date_columns:
                    return [], None
                clauses = [
                    "substr(CAST(%s AS TEXT), 1, 10) = ?" % name
                    for name in date_columns
                ]
                query = "SELECT * FROM %s WHERE %s ORDER BY rowid LIMIT ?" % (  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                    table,
                    " OR ".join(clauses),
                )
                rows = db.execute(
                    query, (day.isoformat(),) * len(clauses) + (2000,)
                ).fetchall()
                return [dict(zip(columns, row)) for row in rows], None
        except (OSError, sqlite3.Error):
            return None, "read_error"

    def _history_source(
        self, name: str, table: str, day: date, required: bool
    ) -> Dict[str, Any]:
        rows, error = self._history_rows(table, day)
        db_path = self.station_root / "news_collector" / "news.db"
        source = str(db_path)
        if error:
            return _source(
                name,
                state="unknown",
                required=required,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source=source,
                source_mode="replay",
                fallback_used=False,
                partial=False,
                error_code=error,
                note="Historical source is unavailable",
                data=None,
            )
        observed = day.isoformat() + "T00:00:00+00:00"
        payload = {
            "date": day.isoformat(),
            "count": len(rows or []),
            "rows": rows or [],
        }
        if not rows:
            return _source(
                name,
                state="unknown",
                required=required,
                observed_at=observed,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source=source,
                source_mode="replay",
                fallback_used=False,
                partial=False,
                error_code="no_data",
                note="No rows for requested replay date",
                data=payload,
            )
        return _source(
            name,
            state="replay",
            required=required,
            observed_at=observed,
            age_sec=None,
            ttl_sec=self.ttl_sec,
            source=source,
            source_mode="replay",
            fallback_used=False,
            partial=False,
            error_code=None,
            note="Historical SQLite rows",
            data=payload,
        )

    def _build_replay(self, replay_at: datetime) -> Dict[str, Any]:
        day = replay_at.date()
        # Deliberately do not call _file_source or _http_source here: current
        # qd/quotes files and live health endpoints must never contaminate replay.
        sources = [
            self._history_source("quotes", "quotes", day, True),
            self._history_source("news", "news", day, False),
            self._history_source("runs", "runs", day, False),
            self._history_source("ai_reviews", "ai_reviews", day, False),
            _source(
                "qd_scan",
                state="unknown",
                required=False,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source="historical_source_unavailable",
                source_mode="replay",
                fallback_used=False,
                partial=False,
                error_code="no_historical_source",
                note="No historical qd scan source is registered",
                data=None,
            ),
            _source(
                "portal_health",
                state="unknown",
                required=False,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source="historical_source_unavailable",
                source_mode="replay",
                fallback_used=False,
                partial=False,
                error_code="no_historical_source",
                note="No historical portal health source is registered",
                data=None,
            ),
            _source(
                "jev_stack",
                state="unknown",
                required=False,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source="historical_source_unavailable",
                source_mode="replay",
                fallback_used=False,
                partial=False,
                error_code="no_historical_source",
                note="No historical JEV stack source is registered",
                data=None,
            ),
            _source(
                "ingest",
                state="unknown",
                required=False,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source="not_implemented",
                source_mode="replay",
                fallback_used=False,
                partial=False,
                error_code="not_implemented",
                note="Ingest source is not implemented in snapshot v1",
                data=None,
            ),
        ]
        try:
            linkage = LinkageReader(self.station_root).build(replay_at)
            linkage_state = (
                "replay" if linkage.get("source_quality") == "historical" else "unknown"
            )
            linkage_error = (
                None if linkage_state == "replay" else "no_historical_source"
            )
            sources.append(
                _source(
                    "linkage",
                    state=linkage_state,
                    required=False,
                    observed_at=linkage.get("as_of"),
                    age_sec=None,
                    ttl_sec=self.ttl_sec,
                    source=str(LinkageReader(self.station_root).tdx_root),
                    source_mode="replay",
                    fallback_used=False,
                    partial=linkage_state != "replay",
                    error_code=linkage_error,
                    note="Historical sector/index linkage"
                    if linkage_state == "replay"
                    else "Historical linkage unavailable",
                    data=linkage,
                )
            )
        except Exception as exc:
            sources.append(
                _source(
                    "linkage",
                    state="unknown",
                    required=False,
                    observed_at=None,
                    age_sec=None,
                    ttl_sec=self.ttl_sec,
                    source="historical_source_unavailable",
                    source_mode="replay",
                    fallback_used=False,
                    partial=True,
                    error_code="read_error",
                    note="Historical linkage reader failed: %s" % str(exc)[:120],
                    data=None,
                )
            )
        news_rows = next(
            (
                s["data"]["rows"]
                for s in sources
                if s["name"] == "news" and s.get("data")
            ),
            [],
        )
        run_rows = next(
            (
                s["data"]["rows"]
                for s in sources
                if s["name"] == "runs" and s.get("data")
            ),
            [],
        )
        events = [
            {
                "kind": "news",
                "date": day.isoformat(),
                "id": row.get("id"),
                "title": row.get("title"),
                "source": row.get("source"),
                "ts": row.get("ts"),
            }
            for row in news_rows[:100]
        ]
        events.extend(
            {
                "kind": "run",
                "date": day.isoformat(),
                "id": row.get("id"),
                "run_kind": row.get("kind"),
                "ok": row.get("ok"),
                "ts": row.get("ts"),
                "new_count": row.get("new_count"),
                "push_count": row.get("push_count"),
            }
            for row in run_rows[:100]
        )
        required_states = [item["state"] for item in sources if item["required"]]
        overall = (
            "degraded"
            if any(state == "replay" for state in required_states)
            else "unknown"
        )
        if any(state == "unknown" for state in required_states):
            overall = "unknown"
        warnings = [
            f"{item['name']}:{item['error_code']}"
            for item in sources
            if item.get("error_code")
        ]
        return {
            "schema": SCHEMA,
            "snapshot_id": uuid.uuid4().hex,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "poll_after_sec": DEFAULT_POLL_AFTER_SEC,
            "overall": overall,
            "replay": True,
            "replay_at": replay_at.isoformat(),
            "sources": sources,
            "events": events,
            "warnings": warnings,
        }

    def _build(self) -> Dict[str, Any]:
        quotes_path = self.station_root / "news_collector" / "quotes_status.json"
        qd_path = self.portal_root / "qd_scan_cache.json"
        sources = [
            self._file_source("quotes", quotes_path, True),
            self._file_source("qd_scan", qd_path, True),
            self._http_source(
                "portal_health", "http://127.0.0.1:8091/api/health", True
            ),
            self._http_source(
                "jev_stack", "http://127.0.0.1:8091/api/jev-stack", False
            ),
            _source(
                "ingest",
                state="unknown",
                required=False,
                observed_at=None,
                age_sec=None,
                ttl_sec=self.ttl_sec,
                source="not_implemented",
                source_mode="none",
                fallback_used=False,
                partial=False,
                error_code="not_implemented",
                note="Ingest source is not implemented in snapshot v1",
                data=None,
            ),
        ]
        required_states = [item["state"] for item in sources if item["required"]]
        if all(state == "healthy" for state in required_states):
            overall = "healthy"
        elif any(state == "down" for state in required_states):
            overall = "down"
        elif any(state in {"degraded", "stale"} for state in required_states):
            overall = "degraded"
        else:
            overall = "unknown"
        warnings = [
            f"{item['name']}:{item['error_code']}"
            for item in sources
            if item.get("error_code")
        ]
        events = []
        for item in sources:
            data = item.get("data")
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                events.extend(data["events"])
        return {
            "schema": SCHEMA,
            "snapshot_id": uuid.uuid4().hex,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "poll_after_sec": DEFAULT_POLL_AFTER_SEC,
            "overall": overall,
            "sources": sources,
            "events": events,
            "warnings": warnings,
        }

    def snapshot(self, replay_at: Optional[datetime | str] = None) -> Dict[str, Any]:
        """Return a live cached snapshot or an uncached historical replay."""
        if isinstance(replay_at, str):
            replay_at = parse_replay_at(replay_at)
        if replay_at is not None:
            if replay_at.tzinfo is None or replay_at.utcoffset() is None:
                raise ReplayArgumentError("replay_at must include a timezone offset")
            replay_at = replay_at.astimezone(timezone.utc)
            if replay_at > datetime.now(timezone.utc):
                raise ReplayArgumentError("replay_at cannot be in the future")
            return self._build_replay(replay_at)
        now = time.monotonic()
        with self._lock:
            if self._cached is None or now - self._cached_at >= self.ttl_sec:
                self._cached = self._build()
                self._cached_at = now
            return self._cached
