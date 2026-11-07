# -*- coding: utf-8 -*-
"""SQLite 存储层：``track.db``（events / event_returns / cases / meta）。

- 路径：``--db`` 参数 > 环境变量 ``TRACK_DB`` > ``<work_root>/track.db``；
- 写入幂等：``events`` 以 ``event_id`` 为 PK、``(source, ref_id)`` 唯一；
  ``event_returns`` 以 ``(event_id, window)`` 为 PK；``cases`` 以 ``case_id`` 为 PK，
  全部 ``INSERT ... ON CONFLICT DO UPDATE``，重复跑同一窗口不会产生重复行；
- 一次 ``save_build`` 在单事务内完成 events + event_returns + cases + meta。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

from .config import Paths, default_paths
from .events import Event

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    ts_code TEXT NOT NULL,
    event_date TEXT NOT NULL,
    publish_phase TEXT NOT NULL,
    category TEXT NOT NULL,
    sentiment REAL NOT NULL,
    summary TEXT,
    source TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    dedupe_key TEXT,
    d0 TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (source, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_events_code_date ON events (ts_code, event_date);
CREATE INDEX IF NOT EXISTS idx_events_d0 ON events (d0);
CREATE INDEX IF NOT EXISTS idx_events_source ON events (source);

CREATE TABLE IF NOT EXISTS event_returns (
    event_id TEXT NOT NULL,
    window TEXT NOT NULL,
    car REAL,
    n_obs INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, window)
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    ts_code TEXT NOT NULL,
    name TEXT,
    event_date TEXT,
    d0 TEXT,
    category TEXT,
    sentiment REAL,
    confidence REAL,
    car_d0_1 REAL,
    car_d0_3 REAL,
    car_d0_5 REAL,
    car_d0_10 REAL,
    industry_index TEXT,
    industry_name TEXT,
    industry_method TEXT,
    divergence TEXT,
    explanation TEXT,
    payload_json TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_d0 ON cases (d0);
CREATE INDEX IF NOT EXISTS idx_cases_code ON cases (ts_code);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def evaluation_to_rows(event_id: str, evaluation: dict) -> list[dict]:
    """把 ``evaluate_event`` 的结果转成 ``event_returns`` 行。"""
    rows: list[dict] = []
    car = evaluation.get("car") or {}
    complete = evaluation.get("complete") or {}
    n_obs = evaluation.get("n_obs") or {}
    for window, value in car.items():
        rows.append(
            {
                "event_id": event_id,
                "window": window,
                "car": value,
                "n_obs": int(n_obs.get(window, 0) or 0),
                "complete": 1 if complete.get(window) else 0,
            }
        )
    return rows


class TrackStore:
    """track.db 读写封装。"""

    def __init__(self, db_path: str | None = None, paths: Paths | None = None):
        self.paths = paths or default_paths()
        self.db_path = db_path or self.paths.db_path

    # -- 连接与建表 -------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        if self.db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        return conn

    # -- events -----------------------------------------------------------
    def upsert_events(self, conn: sqlite3.Connection, events: list[Event]) -> dict:
        if not events:
            return {"total": 0, "inserted": 0, "updated": 0}
        ids = [e.event_id for e in events]
        existing: set[str] = set()
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start : chunk_start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(f"SELECT event_id FROM events WHERE event_id IN ({placeholders})", chunk)
            existing.update(row["event_id"] for row in rows)
        now = _now()
        payload = [
            (
                e.event_id,
                e.ts_code,
                e.event_date,
                e.publish_phase,
                e.category,
                float(e.sentiment),
                e.summary,
                e.source,
                e.ref_id,
                float(e.confidence),
                e.dedupe_key,
                e.d0,
                now,
            )
            for e in events
        ]
        conn.executemany(
            """
            INSERT INTO events (event_id, ts_code, event_date, publish_phase, category, sentiment,
                                summary, source, ref_id, confidence, dedupe_key, d0, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                ts_code = excluded.ts_code,
                event_date = excluded.event_date,
                publish_phase = excluded.publish_phase,
                category = excluded.category,
                sentiment = excluded.sentiment,
                summary = excluded.summary,
                source = excluded.source,
                ref_id = excluded.ref_id,
                confidence = excluded.confidence,
                dedupe_key = excluded.dedupe_key,
                d0 = COALESCE(excluded.d0, events.d0),
                updated_at = excluded.updated_at
            """,
            payload,
        )
        return {
            "total": len(events),
            "inserted": len(events) - len(existing),
            "updated": len(existing),
        }

    # -- event_returns ----------------------------------------------------
    def upsert_returns(self, conn: sqlite3.Connection, rows: list[dict]) -> int:
        if not rows:
            return 0
        now = _now()
        payload = [
            (
                str(r["event_id"]),
                str(r["window"]),
                None if r.get("car") is None else float(r["car"]),
                int(r.get("n_obs", 0) or 0),
                1 if r.get("complete") else 0,
                now,
            )
            for r in rows
        ]
        conn.executemany(
            """
            INSERT INTO event_returns (event_id, window, car, n_obs, complete, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, window) DO UPDATE SET
                car = excluded.car,
                n_obs = excluded.n_obs,
                complete = excluded.complete,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        return len(payload)

    # -- cases ------------------------------------------------------------
    def upsert_cases(self, conn: sqlite3.Connection, cases: list[dict]) -> int:
        if not cases:
            return 0
        now = _now()
        payload = []
        for case in cases:
            payload.append(
                (
                    str(case["case_id"]),
                    str(case.get("ts_code") or ""),
                    case.get("name"),
                    case.get("event_date"),
                    case.get("d0"),
                    case.get("category"),
                    None if case.get("sentiment") is None else float(case["sentiment"]),
                    None if case.get("confidence") is None else float(case["confidence"]),
                    case.get("car_d0_1"),
                    case.get("car_d0_3"),
                    case.get("car_d0_5"),
                    case.get("car_d0_10"),
                    case.get("industry_index"),
                    case.get("industry_name"),
                    case.get("industry_method"),
                    case.get("divergence"),
                    case.get("explanation"),
                    json.dumps(case.get("payload") or {}, ensure_ascii=False),
                    now,
                )
            )
        conn.executemany(
            """
            INSERT INTO cases (case_id, ts_code, name, event_date, d0, category, sentiment, confidence,
                               car_d0_1, car_d0_3, car_d0_5, car_d0_10, industry_index, industry_name,
                               industry_method, divergence, explanation, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                ts_code = excluded.ts_code,
                name = excluded.name,
                event_date = excluded.event_date,
                d0 = excluded.d0,
                category = excluded.category,
                sentiment = excluded.sentiment,
                confidence = excluded.confidence,
                car_d0_1 = excluded.car_d0_1,
                car_d0_3 = excluded.car_d0_3,
                car_d0_5 = excluded.car_d0_5,
                car_d0_10 = excluded.car_d0_10,
                industry_index = excluded.industry_index,
                industry_name = excluded.industry_name,
                industry_method = excluded.industry_method,
                divergence = excluded.divergence,
                explanation = excluded.explanation,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        return len(payload)

    # -- meta -------------------------------------------------------------
    def set_meta(self, conn: sqlite3.Connection, mapping: dict) -> int:
        if not mapping:
            return 0
        now = _now()
        payload = [
            (str(key), json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value, now)
            for key, value in mapping.items()
        ]
        conn.executemany(
            """
            INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            payload,
        )
        return len(payload)

    # -- 组合写入 ---------------------------------------------------------
    def save_build(
        self,
        events: list[Event],
        returns: list[dict] | None = None,
        cases: list[dict] | None = None,
        meta: dict | None = None,
    ) -> dict:
        """单事务写入 events/event_returns/cases/meta（幂等）。"""
        conn = self.connect()
        try:
            with conn:  # 事务
                event_stats = self.upsert_events(conn, events)
                return_count = self.upsert_returns(conn, returns or [])
                case_count = self.upsert_cases(conn, cases or [])
                meta = dict(meta or {})
                meta.setdefault("schema_version", SCHEMA_VERSION)
                meta.setdefault("built_at", _now())
                meta["event_count"] = self.count(conn, "events")
                meta["return_rows"] = self.count(conn, "event_returns")
                meta["case_count"] = self.count(conn, "cases")
                meta_count = self.set_meta(conn, meta)
            return {
                "events": event_stats,
                "returns": return_count,
                "cases": case_count,
                "meta": meta_count,
                "db_path": self.db_path,
            }
        finally:
            conn.close()

    # -- 读取 -------------------------------------------------------------
    @staticmethod
    def count(conn: sqlite3.Connection, table: str) -> int:
        if table not in {"events", "event_returns", "cases", "meta"}:
            raise ValueError(f"unknown table: {table}")
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"]) if row else 0

    def stats(self) -> dict:
        conn = self.connect()
        try:
            counts = {table: self.count(conn, table) for table in ("events", "event_returns", "cases", "meta")}
            counts["db_path"] = self.db_path
            return counts
        finally:
            conn.close()

    def get_meta(self) -> dict:
        conn = self.connect()
        try:
            result: dict = {}
            for row in conn.execute("SELECT key, value FROM meta"):
                try:
                    result[row["key"]] = json.loads(row["value"])
                except (TypeError, ValueError):
                    result[row["key"]] = row["value"]
            return result
        finally:
            conn.close()

    def list_events(self, ts_code: str | None = None, limit: int = 100) -> list[dict]:
        conn = self.connect()
        try:
            if ts_code:
                rows = conn.execute(
                    "SELECT * FROM events WHERE ts_code = ? ORDER BY event_date DESC LIMIT ?", (ts_code, limit)
                )
            else:
                rows = conn.execute("SELECT * FROM events ORDER BY event_date DESC LIMIT ?", (limit,))
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def list_cases(self, limit: int = 100) -> list[dict]:
        conn = self.connect()
        try:
            rows = conn.execute("SELECT * FROM cases ORDER BY d0 DESC, case_id LIMIT ?", (limit,))
            return [dict(row) for row in rows]
        finally:
            conn.close()
