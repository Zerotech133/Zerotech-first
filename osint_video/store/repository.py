"""Idempotent repositories for durable structured knowledge records."""

from __future__ import annotations

import json
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from common import db

_ACTIVE_TRANSACTION: ContextVar[Any | None] = ContextVar("knowledge_repository_transaction", default=None)


@contextmanager
def transaction() -> Iterator[Any]:
    """Run knowledge resource/unit/evidence/index writes in one PG transaction."""
    if _ACTIVE_TRANSACTION.get() is not None:
        yield _ACTIVE_TRANSACTION.get()
        return
    with db.transaction() as conn:
        token = _ACTIVE_TRANSACTION.set(conn)
        try:
            yield conn
        finally:
            _ACTIVE_TRANSACTION.reset(token)


def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Query current atomic transaction when present, otherwise use db helper."""
    conn = _ACTIVE_TRANSACTION.get()
    return db.query(conn, sql, params) if conn is not None else db.fetch_all(sql, params)


def _write(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute returning DML in current transaction or a standalone one."""
    conn = _ACTIVE_TRANSACTION.get()
    if conn is not None:
        return db.query(conn, sql, params)
    with db.transaction() as standalone:
        return db.query(standalone, sql, params)
from store.models import (
    BloggerHistory,
    IndexTask,
    KnowledgeResource,
    KnowledgeUnit,
    StrategyEvidence,
    row_to_blogger_history,
    row_to_index_task,
    row_to_knowledge_resource,
    row_to_knowledge_unit,
    row_to_strategy_evidence,
)


def _json(value: Any) -> str:
    """Serialize JSONB values consistently, preserving Unicode."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def upsert_knowledge_resource(resource: KnowledgeResource) -> KnowledgeResource:
    """Insert or update durable resource metadata; media path remains temporary."""
    sql = """
    INSERT INTO osint_knowledge_resource
      (resource_key, blogger_key, source_url, platform_content_id, published_at, duration_sec,
       status, temporary_media_path, media_expires_at, asr_text, ocr_text, subtitle_text, metadata)
    VALUES (:resource_key, :blogger_key, :source_url, :platform_content_id, :published_at, :duration_sec,
       :status, :temporary_media_path, :media_expires_at, :asr_text, :ocr_text, :subtitle_text, CAST(:metadata AS JSONB))
    ON CONFLICT (resource_key) DO UPDATE SET
      blogger_key=EXCLUDED.blogger_key, source_url=EXCLUDED.source_url,
      platform_content_id=EXCLUDED.platform_content_id, published_at=EXCLUDED.published_at,
      duration_sec=EXCLUDED.duration_sec, status=EXCLUDED.status,
      temporary_media_path=EXCLUDED.temporary_media_path, media_expires_at=EXCLUDED.media_expires_at,
      asr_text=EXCLUDED.asr_text, ocr_text=EXCLUDED.ocr_text, subtitle_text=EXCLUDED.subtitle_text,
      metadata=EXCLUDED.metadata, updated_at=now()
    RETURNING *
    """
    params = {
        "resource_key": resource.resource_key, "blogger_key": resource.blogger_key,
        "source_url": resource.source_url, "platform_content_id": resource.platform_content_id,
        "published_at": resource.published_at, "duration_sec": resource.duration_sec,
        "status": resource.status, "temporary_media_path": resource.temporary_media_path,
        "media_expires_at": resource.media_expires_at, "asr_text": resource.asr_text,
        "ocr_text": resource.ocr_text, "subtitle_text": resource.subtitle_text,
        "metadata": _json(resource.metadata),
    }
    rows = _write(sql, params)
    return row_to_knowledge_resource(rows[0])


def get_knowledge_resource(resource_key: str) -> KnowledgeResource | None:
    """Fetch one resource by stable key."""
    rows = db.fetch_all("SELECT * FROM osint_knowledge_resource WHERE resource_key=:key", {"key": resource_key})
    return row_to_knowledge_resource(rows[0]) if rows else None


def upsert_knowledge_unit(unit: KnowledgeUnit) -> KnowledgeUnit:
    """Insert immutable unit version; same content/version is idempotent."""
    sql = """
    INSERT INTO osint_knowledge_unit
      (unit_key, unit_version, unit_type, title, content, source_channel, source_video_id, source_url, source_link,
       start_sec, end_sec, content_hash, review_status, blogger_key, resource_key, facts, entities, topics, evidence_refs, quality, status, truth_status, extension)
    VALUES (:unit_key, :unit_version, :unit_type, :title, :content, :source_channel, :source_video_id, :source_url, :source_link,
       :start_sec, :end_sec, :content_hash, :review_status, :blogger_key, :resource_key, CAST(:facts AS JSONB), CAST(:entities AS JSONB), CAST(:topics AS JSONB),
       CAST(:evidence_refs AS JSONB), CAST(:quality AS JSONB), :status, :truth_status, CAST(:extension AS JSONB))
    ON CONFLICT (unit_key, unit_version) DO UPDATE SET unit_id=osint_knowledge_unit.unit_id
    RETURNING *
    """
    existing = _query(
        "SELECT unit_version, content_hash FROM osint_knowledge_unit WHERE unit_key=:unit_key ORDER BY unit_version DESC LIMIT 1",
        {"unit_key": unit.unit_key},
    )
    if existing and existing[0].get("content_hash") != unit.content_hash:
        unit.unit_version = int(existing[0].get("unit_version", 0) or 0) + 1
    params = {
        "unit_key": unit.unit_key, "unit_version": unit.unit_version, "unit_type": unit.unit_type, "title": unit.title,
        "content": unit.content, "source_channel": unit.source_channel, "source_video_id": unit.source_video_id,
        "source_url": unit.source_url, "source_link": unit.source_link, "start_sec": unit.start_sec,
        "end_sec": unit.end_sec, "content_hash": unit.content_hash, "review_status": unit.review_status,
        "blogger_key": unit.blogger_key, "resource_key": unit.resource_key, "facts": _json(unit.facts), "entities": _json(unit.entities),
        "topics": _json(unit.topics), "evidence_refs": _json(unit.evidence_refs),
        "quality": _json(unit.quality), "status": unit.status, "truth_status": unit.truth_status,
        "extension": _json(unit.extension),
    }
    rows = _write(sql, params)
    return row_to_knowledge_unit(rows[0])


def upsert_blogger_history(history: BloggerHistory) -> BloggerHistory:
    """Append observation; identical observation hash is idempotent."""
    sql = """
    INSERT INTO osint_blogger_history
      (blogger_key, history_key, observed_at, event_type, summary, attributes,
       source_video_id, source_unit_id, observation_hash)
    VALUES (:blogger_key, :history_key, :observed_at, :event_type, :summary,
       CAST(:attributes AS JSONB), :source_video_id, :source_unit_id, :observation_hash)
    ON CONFLICT (blogger_key, history_key, observation_hash) DO UPDATE SET history_id=osint_blogger_history.history_id
    RETURNING *
    """
    params = {
        "blogger_key": history.blogger_key, "history_key": history.history_key,
        "observed_at": history.observed_at, "event_type": history.event_type, "observation_hash": history.observation_hash,
        "summary": history.summary, "attributes": _json(history.attributes),
        "source_video_id": history.source_video_id, "source_unit_id": history.source_unit_id,
    }
    with db.transaction() as conn:
        rows = db.query(conn, sql, params)
    return row_to_blogger_history(rows[0])


def upsert_strategy_evidence(evidence: StrategyEvidence) -> StrategyEvidence:
    """Insert or update one strategy/evidence edge using ``evidence_key``."""
    sql = """
    INSERT INTO osint_strategy_evidence
      (strategy_key, knowledge_unit_key, knowledge_unit_version, evidence_key, evidence_role, quote,
       quote_start_sec, quote_end_sec, evidence_status, snapshot_hash, relevance, confidence, metadata)
    VALUES (:strategy_key, :knowledge_unit_key, :knowledge_unit_version, :evidence_key, :evidence_role, :quote,
       :quote_start_sec, :quote_end_sec, :evidence_status, :snapshot_hash, :relevance, :confidence, CAST(:metadata AS JSONB))
    ON CONFLICT (evidence_key) DO UPDATE SET
       strategy_key=EXCLUDED.strategy_key, knowledge_unit_key=EXCLUDED.knowledge_unit_key,
       knowledge_unit_version=EXCLUDED.knowledge_unit_version,
       evidence_role=EXCLUDED.evidence_role, quote=EXCLUDED.quote,
       quote_start_sec=EXCLUDED.quote_start_sec, quote_end_sec=EXCLUDED.quote_end_sec,
       evidence_status=EXCLUDED.evidence_status, snapshot_hash=EXCLUDED.snapshot_hash,
       relevance=EXCLUDED.relevance, confidence=EXCLUDED.confidence,
       metadata=EXCLUDED.metadata
    RETURNING *
    """
    params = {
        "strategy_key": evidence.strategy_key, "knowledge_unit_key": evidence.knowledge_unit_key,
        "knowledge_unit_version": int(getattr(evidence, "knowledge_unit_version", 1) or 1),
        "evidence_key": evidence.evidence_key, "evidence_role": evidence.evidence_role,
        "quote": evidence.quote, "quote_start_sec": evidence.quote_start_sec, "quote_end_sec": evidence.quote_end_sec,
        "evidence_status": evidence.evidence_status, "snapshot_hash": evidence.snapshot_hash,
        "relevance": evidence.relevance, "confidence": evidence.confidence,
        "metadata": _json(evidence.metadata),
    }
    rows = _write(sql, params)
    return row_to_strategy_evidence(rows[0])


def register_index_tasks(resource_key: str, processing_run_key: str | None = None) -> list[IndexTask]:
    """Register PG, Qdrant and graph fan-out tasks idempotently."""
    sql = """
    INSERT INTO osint_index_task (resource_key, target, status, processing_run_key)
    VALUES (:resource_key, :target, 'pending', :processing_run_key)
    ON CONFLICT (resource_key, target) DO UPDATE SET
      processing_run_key=EXCLUDED.processing_run_key, updated_at=now()
    RETURNING *
    """
    tasks: list[IndexTask] = []
    for target in ("pg", "qdrant", "graph"):
        rows = _write(sql, {"resource_key": resource_key, "target": target, "processing_run_key": processing_run_key})
        tasks.append(row_to_index_task(rows[0]))
    return tasks


def update_index_task(resource_key: str, target: str, status: str, error_message: str | None = None) -> IndexTask | None:
    """Update asynchronous index status without deleting PG facts on failure."""
    sql = """UPDATE osint_index_task SET status=:status, error_message=:error_message,
           attempt_count=attempt_count + 1, updated_at=now()
           WHERE resource_key=:resource_key AND target=:target RETURNING *"""
    params = {"resource_key": resource_key, "target": target, "status": status, "error_message": error_message}
    with db.transaction() as conn:
        rows = db.query(conn, sql, params)
    return row_to_index_task(rows[0]) if rows else None


def claim_index_tasks(limit: int = 100, target: str | None = None) -> list[IndexTask]:
    """Claim pending/failed fan-out tasks for replay.

    Claiming is atomic and skips tasks already running in another worker.  The
    authoritative resource/unit/evidence rows are never changed by this
    operation; external adapters must perform idempotent upserts.
    """
    bounded_limit = max(1, min(int(limit), 1000))
    target_clause = "AND target = :target" if target else ""
    params: dict[str, Any] = {"limit": bounded_limit}
    if target:
        params["target"] = target
    sql = f"""
    WITH candidates AS (
      SELECT task_id FROM osint_index_task
      WHERE status IN ('pending', 'failed') {target_clause}
      ORDER BY updated_at ASC, task_id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT :limit
    )
    UPDATE osint_index_task AS task
    SET status='running', error_message=NULL, updated_at=now()
    FROM candidates
    WHERE task.task_id = candidates.task_id
    RETURNING task.*
    """
    with db.transaction() as conn:
        rows = db.query(conn, sql, params)
    return [row_to_index_task(row) for row in rows]


def replay_index_tasks(
    adapter: Any,
    limit: int = 100,
    target: str | None = None,
) -> list[IndexTask]:
    """Replay pending/failed Qdrant or graph tasks through an injected adapter.

    ``adapter`` is called as ``adapter(resource_key, target)``.  A successful
    return (including ``None``) marks the task ``succeeded``; an exception marks
    it ``failed`` with a bounded diagnostic.  Since PG rows remain authoritative
    and adapters are expected to upsert by stable keys, retries are safe and
    idempotent.
    """
    if not callable(adapter):
        raise TypeError("adapter must be callable(resource_key, target)")
    claimed = claim_index_tasks(limit=limit, target=target)
    completed: list[IndexTask] = []
    for task in claimed:
        if task.target == "pg":
            # PG is authoritative and does not need an external replay adapter.
            completed_task = update_index_task(task.resource_key, task.target, "succeeded")
            if completed_task is not None:
                completed.append(completed_task)
            continue
        try:
            adapter(task.resource_key, task.target)
        except Exception as exc:  # noqa: BLE001 - worker must continue fan-out
            message = str(exc)[:2000] or exc.__class__.__name__
            updated = update_index_task(task.resource_key, task.target, "failed", message)
        else:
            updated = update_index_task(task.resource_key, task.target, "succeeded")
        if updated is not None:
            completed.append(updated)
    return completed


def can_mark_link_done(task_id: int) -> bool:
    """Done gate: PG resource/unit facts must exist and index tasks be registered.

    Index completion is intentionally not required; failed Qdrant/graph tasks do
    not remove authoritative PG facts and can be replayed asynchronously.
    """
    rows = db.fetch_all(
        """WITH linked_resource AS (
          SELECT r.resource_key
          FROM osint_link_task l
          JOIN osint_knowledge_resource r
            ON r.source_url = l.sph_url OR r.platform_content_id = l.video_id
          WHERE l.id=:task_id
        )
        SELECT EXISTS (SELECT 1 FROM linked_resource) AS resource_ok,
               EXISTS (SELECT 1 FROM osint_knowledge_unit u
                       JOIN linked_resource r ON r.resource_key = u.resource_key) AS unit_ok,
               EXISTS (SELECT 1 FROM osint_strategy_evidence e
                       JOIN osint_knowledge_unit u ON u.unit_key = e.knowledge_unit_key
                       JOIN linked_resource r ON r.resource_key = u.resource_key) AS evidence_ok,
               EXISTS (SELECT 1 FROM osint_index_task i
                       JOIN linked_resource r ON r.resource_key = i.resource_key) AS index_ok""",
        {"task_id": task_id},
    )
    return bool(rows and all(rows[0][name] for name in ("resource_ok", "unit_ok", "evidence_ok", "index_ok")))


def purge_media_file(path: str | None) -> tuple[bool, str | None]:
    """Best-effort unlink helper, returning success and error text for tests."""
    if not path:
        return True, None
    try:
        Path(path).unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return False, str(exc)


def purge_expired_media(resource_key: str | None = None) -> int:
    """Unlink expired files and persist deletion status/error without deleting facts."""
    where = "media_expires_at <= now()"
    params: dict[str, Any] = {}
    if resource_key:
        where += " AND resource_key=:resource_key"
        params["resource_key"] = resource_key
    assets = db.fetch_all(
        f"SELECT resource_key, temporary_media_path FROM osint_knowledge_resource WHERE {where}", params
    )
    count = 0
    for row in assets:
        ok, error = purge_media_file(row.get("temporary_media_path"))
        db.execute(
            "UPDATE osint_knowledge_resource SET temporary_media_path=NULL, updated_at=now() WHERE resource_key=:key",
            {"key": row["resource_key"]},
        )
        db.execute(
            """UPDATE osint_media_asset SET media_path=NULL, media_status=:status,
               deleted_at=now(), deletion_reason=:reason, updated_at=now()
               WHERE resource_key=:resource_key AND media_status <> 'deleted'""",
            {"resource_key": row["resource_key"], "status": "deleted" if ok else "expired",
             "reason": "retention_expired" if ok else f"unlink_failed: {error}"},
        )
        count += 1
    return count


def get_knowledge_unit(unit_key: str) -> KnowledgeUnit | None:
    """Fetch one unit by stable key."""
    rows = db.fetch_all("SELECT * FROM osint_knowledge_unit WHERE unit_key=:key ORDER BY unit_version DESC LIMIT 1", {"key": unit_key})
    return row_to_knowledge_unit(rows[0]) if rows else None


def list_blogger_history(blogger_key: str, limit: int = 100) -> list[BloggerHistory]:
    """List observations for a blogger, newest first."""
    rows = db.fetch_all(
        "SELECT * FROM osint_blogger_history WHERE blogger_key=:key ORDER BY observed_at DESC LIMIT :limit",
        {"key": blogger_key, "limit": max(1, min(limit, 1000))},
    )
    return [row_to_blogger_history(row) for row in rows]


def list_strategy_evidence(strategy_key: str, limit: int = 100) -> list[StrategyEvidence]:
    """List evidence edges for a strategy."""
    rows = db.fetch_all(
        "SELECT * FROM osint_strategy_evidence WHERE strategy_key=:key ORDER BY created_at DESC LIMIT :limit",
        {"key": strategy_key, "limit": max(1, min(limit, 1000))},
    )
    return [row_to_strategy_evidence(row) for row in rows]


__all__ = [
    "transaction", "upsert_knowledge_resource", "get_knowledge_resource", "upsert_knowledge_unit",
    "upsert_blogger_history", "upsert_strategy_evidence", "register_index_tasks", "update_index_task",
    "claim_index_tasks", "replay_index_tasks", "can_mark_link_done", "purge_expired_media",
    "get_knowledge_unit", "list_blogger_history", "list_strategy_evidence",
]
