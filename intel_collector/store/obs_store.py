# -*- coding: utf-8 -*-
"""采集即服务平台 · 观测存储 (store/obs_store.py)

Observation 幂等落盘：(source_id, snapshot_id) 唯一。区分"最新观察"与"as-of 时点观察"。
原始观察不覆盖、不累加；评论身份 (video_id, comment.id) 由 clean 层处理。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable, Optional

from core.contracts import Observation, SourceClock, UseCase


class ObservationStore:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS observations(
            source_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            collected_at TEXT,
            status TEXT,
            completeness TEXT,
            allowed_use_cases TEXT,
            source_field TEXT,
            payload_json TEXT,
            raw_sha256 TEXT,
            source_payload_sha256 TEXT,
            PRIMARY KEY(source_id, snapshot_id)
        )""")
        # 兼容旧库：缺列则补（原型阶段临时库每次新建，本段为稳健兜底）
        try:
            self._conn.execute("ALTER TABLE observations ADD COLUMN source_field TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    def put(self, obs: Observation) -> None:
        self._conn.execute(
            """INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_id, snapshot_id) DO UPDATE SET
                 entity_type=excluded.entity_type,
                 collected_at=excluded.collected_at,
                 status=excluded.status,
                 completeness=excluded.completeness,
                 allowed_use_cases=excluded.allowed_use_cases,
                 source_field=excluded.source_field,
                 payload_json=excluded.payload_json,
                 raw_sha256=excluded.raw_sha256,
                 source_payload_sha256=excluded.source_payload_sha256""",
            (obs.source_id, obs.snapshot_id, obs.entity_type, obs.entity_id,
             obs.clock.collected_at, obs.status, obs.completeness,
             ",".join(obs.allowed_use_cases), obs.source_field,
             json.dumps(obs.payload, ensure_ascii=False),
             obs.raw_sha256, obs.source_payload_sha256),
        )
        self._conn.commit()

    def put_many(self, obs: Iterable[Observation]) -> None:
        for o in obs:
            self.put(o)

    def all(self) -> list[Observation]:
        """全库读取（原型态、数据量小）。PIT 时点回溯用。"""
        rows = self._conn.execute("SELECT * FROM observations").fetchall()
        return [o for o in (self._row_to_obs(r) for r in rows) if o]

    def latest(self, source_id: str, entity_type: str, entity_id: str) -> Optional[Observation]:
        row = self._conn.execute(
            """SELECT * FROM observations WHERE source_id=? AND entity_type=? AND entity_id=?
               ORDER BY collected_at DESC LIMIT 1""",
            (source_id, entity_type, entity_id)).fetchone()
        return self._row_to_obs(row)

    def as_of(self, source_id: str, entity_type: str, entity_id: str, ts: str) -> Optional[Observation]:
        row = self._conn.execute(
            """SELECT * FROM observations WHERE source_id=? AND entity_type=? AND entity_id=?
               AND collected_at <= ? ORDER BY collected_at DESC LIMIT 1""",
            (source_id, entity_type, entity_id, ts)).fetchone()
        return self._row_to_obs(row)

    def query(self, source_id: str, use_case: str) -> list[Observation]:
        """受控查询：只返回该用途被允许且权利校验通过的观察。"""
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE source_id=?", (source_id,)).fetchall()
        out = []
        for r in rows:
            o = self._row_to_obs(r)
            if o and use_case in o.allowed_use_cases:
                out.append(o)
        return out

    def stats(self, source_id: Optional[str] = None) -> dict:
        if source_id:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM observations WHERE source_id=?", (source_id,)).fetchone()[0]
        else:
            total = self._conn.execute("SELECT COUNT(*) FROM observations", ()).fetchone()[0]
        return {"total_observations": total}

    @staticmethod
    def _row_to_obs(row) -> Optional[Observation]:
        if not row:
            return None
        (source_id, snapshot_id, entity_type, entity_id, collected_at, status,
         completeness, allowed, source_field, payload_json, raw_sha256,
         source_payload_sha256) = row
        return Observation(
            source_id=source_id, snapshot_id=snapshot_id, entity_type=entity_type,
            entity_id=entity_id,
            clock=SourceClock(collected_at=collected_at),
            status=status, completeness=completeness,
            payload=json.loads(payload_json),
            raw_sha256=raw_sha256, source_payload_sha256=source_payload_sha256,
            allowed_use_cases=tuple(allowed.split(",")) if allowed else (),
            source_field=source_field,
        )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()
