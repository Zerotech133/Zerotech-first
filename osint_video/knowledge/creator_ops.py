"""博主运营任务：历史回溯、阶段总结与每日增量扫描。"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from common import db

_ALLOWED_RUN_TYPES = {"history_backfill", "profile_summary", "daily_scan", "incremental_update"}
_TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "dead_letter"}
_OPERATION_STATES = {"link_received", "creator_resolved", "profile_created", "history_backfill_running", "profile_summarized", "daily_monitoring", "incremental_update", "retryable", "dead_letter"}
_STATE_EDGES = {
    "link_received": {"creator_resolved", "retryable", "dead_letter"},
    "creator_resolved": {"profile_created", "retryable", "dead_letter"},
    "profile_created": {"history_backfill_running", "retryable", "dead_letter"},
    "history_backfill_running": {"profile_summarized", "retryable", "dead_letter"},
    "profile_summarized": {"daily_monitoring", "incremental_update", "retryable", "dead_letter"},
    "daily_monitoring": {"incremental_update", "retryable", "dead_letter"},
    "incremental_update": {"daily_monitoring", "profile_summarized", "retryable", "dead_letter"},
    "retryable": {"creator_resolved", "profile_created", "history_backfill_running", "profile_summarized", "daily_monitoring", "incremental_update", "dead_letter"},
    "dead_letter": {"retryable"},
}


def _cursor_key(cursor: str | None) -> tuple[Any, ...] | None:
    if not cursor:
        return None
    raw = str(cursor)
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        if "|" in decoded:
            raw = decoded
    except (ValueError, TypeError, UnicodeError):
        pass
    if "|" in raw:
        stamp, item = raw.rsplit("|", 1)
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return (0, parsed.timestamp(), int(item))
        except (ValueError, TypeError, OverflowError):
            return (1, stamp, item)
    return (2, raw)


def _regression(new_cursor: str | None, old_cursor: str | None) -> bool:
    return bool(new_cursor and old_cursor and _cursor_key(new_cursor) < _cursor_key(old_cursor))


def _cursor_error() -> ValueError:
    return ValueError("CURSOR_REGRESSION: cursor 只能单调前进")


def _row(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    rows = db.query(conn, sql, params)
    return rows[0] if rows else None


def create_run(target_id: int, run_type: str, input_cursor: str | None = None) -> dict[str, Any]:
    if run_type not in _ALLOWED_RUN_TYPES:
        raise ValueError(f"INVALID_ARGUMENT: 不支持的运行类型: {run_type}")
    with db.transaction() as conn:
        target = _row(conn, "SELECT target_id, history_cursor, last_success_cursor FROM osint_target WHERE target_id=:target_id FOR UPDATE", {"target_id": target_id})
        if target is None:
            raise ValueError("NOT_FOUND: 博主不存在")
        baseline = target.get("last_success_cursor") or target.get("history_cursor")
        if _regression(input_cursor, baseline):
            raise _cursor_error()
        existing = _row(conn, "SELECT * FROM osint_creator_run WHERE target_id=:target_id AND run_type=:run_type AND input_cursor IS NOT DISTINCT FROM :input_cursor AND state IN ('pending','running') ORDER BY run_id DESC LIMIT 1", {"target_id": target_id, "run_type": run_type, "input_cursor": input_cursor})
        if existing is not None:
            return existing
        return _row(conn, "INSERT INTO osint_creator_run(target_id, run_type, state, input_cursor) VALUES (:target_id, :run_type, 'pending', :input_cursor) RETURNING *", {"target_id": target_id, "run_type": run_type, "input_cursor": input_cursor}) or {}


def start_run(run_id: int) -> dict[str, Any] | None:
    with db.transaction() as conn:
        row = _row(conn, "SELECT * FROM osint_creator_run WHERE run_id=:run_id FOR UPDATE", {"run_id": run_id})
        if row is None or row.get("state") != "pending":
            return None
        return _row(conn, "UPDATE osint_creator_run SET state='running', started_at=COALESCE(started_at, now()) WHERE run_id=:run_id AND state='pending' RETURNING *", {"run_id": run_id})


def finish_run(run_id: int, *, state: str, output_cursor: str | None = None, items_seen: int = 0, items_succeeded: int = 0, items_failed: int = 0, error_code: str | None = None, error_detail: str | None = None, summary_version: int | None = None) -> dict[str, Any] | None:
    if state not in _TERMINAL_RUN_STATES:
        raise ValueError(f"INVALID_ARGUMENT: 非法结束状态: {state}")
    if min(items_seen, items_succeeded, items_failed) < 0:
        raise ValueError("INVALID_ARGUMENT: 运行计数不能为负数")
    with db.transaction() as conn:
        row = _row(conn, "SELECT * FROM osint_creator_run WHERE run_id=:run_id FOR UPDATE", {"run_id": run_id})
        if row is None or row.get("state") != "running":
            return row
        target = _row(conn, "SELECT history_cursor, last_success_cursor FROM osint_target WHERE target_id=:target_id FOR UPDATE", {"target_id": row["target_id"]}) or {}
        baseline = target.get("last_success_cursor") or target.get("history_cursor")
        if _regression(output_cursor, row.get("input_cursor")) or _regression(output_cursor, baseline):
            raise _cursor_error()
        return _row(conn, "UPDATE osint_creator_run SET state=:state, output_cursor=:output_cursor, items_seen=:items_seen, items_succeeded=:items_succeeded, items_failed=:items_failed, error_code=:error_code, error_detail=:error_detail, summary_version=:summary_version, finished_at=now() WHERE run_id=:run_id AND state='running' RETURNING *", {"run_id": run_id, "state": state, "output_cursor": output_cursor, "items_seen": items_seen, "items_succeeded": items_succeeded, "items_failed": items_failed, "error_code": error_code, "error_detail": error_detail, "summary_version": summary_version})


def advance_target(target_id: int, operation_state: str, *, cursor: str | None = None, error: str | None = None, summary_version: int | None = None, previous_state: str | None = None) -> dict[str, Any] | None:
    if operation_state not in _OPERATION_STATES:
        raise ValueError(f"INVALID_ARGUMENT: 非法博主运营状态: {operation_state}")
    with db.transaction() as conn:
        target = _row(conn, "SELECT * FROM osint_target WHERE target_id=:target_id FOR UPDATE", {"target_id": target_id})
        if target is None:
            return None
        current = str(target.get("operation_state") or "creator_resolved")
        if current == "retryable":
            if previous_state not in _STATE_EDGES or previous_state == "retryable" or operation_state != previous_state:
                raise ValueError("CREATOR_STATE_TRANSITION_INVALID: retryable 必须显式恢复至失败前状态")
        elif operation_state not in _STATE_EDGES.get(current, set()):
            raise ValueError(f"CREATOR_STATE_TRANSITION_INVALID: {current} -> {operation_state}")
        baseline = target.get("last_success_cursor") or target.get("history_cursor")
        if _regression(cursor, baseline):
            raise _cursor_error()
        success = operation_state in {"profile_summarized", "daily_monitoring", "incremental_update"}
        return _row(conn, "UPDATE osint_target SET operation_state=:state, history_cursor=COALESCE(:cursor, history_cursor), last_success_cursor=CASE WHEN :success THEN COALESCE(:cursor, last_success_cursor) ELSE last_success_cursor END, last_success_at=CASE WHEN :success AND :cursor IS NOT NULL THEN now() ELSE last_success_at END, last_summary_version=CASE WHEN :summary_version IS NOT NULL THEN GREATEST(last_summary_version, :summary_version) ELSE last_summary_version END, next_scan_at=CASE WHEN :state IN ('daily_monitoring','incremental_update') THEN now() + (scan_interval_minutes || ' minutes')::interval ELSE next_scan_at END, last_scan_error=:error WHERE target_id=:target_id RETURNING *", {"target_id": target_id, "state": operation_state, "cursor": cursor, "success": success, "summary_version": summary_version, "error": error})


def save_summary(target_id: int, *, summary_text: str, topics: Any = None, viewpoints: Any = None, evidence_count: int = 0, input_cursor: str | None = None, model_version: str | None = None, snapshot_hash: str) -> dict[str, Any]:
    if not snapshot_hash:
        raise ValueError("INVALID_ARGUMENT: snapshot_hash 不能为空")
    if evidence_count < 0:
        raise ValueError("INVALID_ARGUMENT: evidence_count 不能为负数")
    with db.transaction() as conn:
        target = _row(conn, "SELECT * FROM osint_target WHERE target_id=:target_id FOR UPDATE", {"target_id": target_id})
        if target is None:
            raise ValueError("NOT_FOUND: 博主不存在")
        existing = _row(conn, "SELECT * FROM osint_creator_summary WHERE target_id=:target_id AND snapshot_hash=:snapshot_hash", {"target_id": target_id, "snapshot_hash": snapshot_hash})
        if existing is not None:
            return existing
        max_row = _row(conn, "SELECT COALESCE(MAX(summary_version), 0) AS version FROM osint_creator_summary WHERE target_id=:target_id", {"target_id": target_id}) or {"version": 0}
        version = int(max_row.get("version") or 0) + 1
        row = _row(conn, "INSERT INTO osint_creator_summary(target_id, summary_version, summary_text, topics, viewpoints, evidence_count, input_cursor, model_version, snapshot_hash) VALUES (:target_id, :version, :summary_text, CAST(:topics AS JSONB), CAST(:viewpoints AS JSONB), :evidence_count, :input_cursor, :model_version, :snapshot_hash) RETURNING *", {"target_id": target_id, "version": version, "summary_text": summary_text, "topics": json.dumps(topics or [], ensure_ascii=False), "viewpoints": json.dumps(viewpoints or [], ensure_ascii=False), "evidence_count": evidence_count, "input_cursor": input_cursor, "model_version": model_version, "snapshot_hash": snapshot_hash}) or {}
        db.query(conn, "UPDATE osint_target SET last_summary_version=:version, last_summary_hash=:snapshot_hash, summary_updated_at=now() WHERE target_id=:target_id", {"target_id": target_id, "version": version, "snapshot_hash": snapshot_hash})
        return row

upsert_summary = save_summary


def due_targets(limit: int = 50) -> list[dict[str, Any]]:
    return db.fetch_all("SELECT target_id, nickname, finder_id, operation_state, history_cursor, last_success_cursor, next_scan_at, scan_interval_minutes FROM osint_target WHERE enabled AND operation_state IN ('daily_monitoring','incremental_update') AND (next_scan_at IS NULL OR next_scan_at <= now()) ORDER BY COALESCE(next_scan_at, TIMESTAMPTZ 'epoch'), target_id LIMIT :limit", {"limit": min(max(limit, 1), 100)})


def claim_due_targets(limit: int = 50, lease_minutes: int = 30) -> list[dict[str, Any]]:
    """Claim due targets using a durable lease recoverable after crashes."""
    if lease_minutes < 1:
        raise ValueError("lease_minutes must be positive")
    token = uuid.uuid4().hex
    with db.transaction() as conn:
        rows = db.query(conn, "SELECT target_id, nickname, finder_id, operation_state, history_cursor, last_success_cursor, next_scan_at, scan_interval_minutes FROM osint_target WHERE enabled AND operation_state IN ('daily_monitoring','incremental_update') AND (next_scan_at IS NULL OR next_scan_at <= now()) AND (lease_until IS NULL OR lease_until <= now()) ORDER BY COALESCE(next_scan_at, TIMESTAMPTZ 'epoch'), target_id LIMIT :limit FOR UPDATE SKIP LOCKED", {"limit": min(max(limit, 1), 100)})
        for row in rows:
            updated = _row(conn, "UPDATE osint_target SET claim_token=:token, lease_until=now() + (:lease_minutes || ' minutes')::interval, next_scan_at=now() + (scan_interval_minutes || ' minutes')::interval WHERE target_id=:target_id RETURNING target_id, nickname, finder_id, operation_state, history_cursor, last_success_cursor, next_scan_at, scan_interval_minutes, claim_token, lease_until", {"target_id": int(row["target_id"]), "token": token, "lease_minutes": lease_minutes})
            if updated:
                row.clear(); row.update(updated)
        return rows


def release_target_claim(target_id: int, claim_token: str, *, success: bool = False) -> bool:
    """Release a lease only for its owner token."""
    with db.transaction() as conn:
        rows = db.query(conn, "UPDATE osint_target SET claim_token=NULL, lease_until=NULL, next_scan_at=CASE WHEN :success THEN now() + (scan_interval_minutes || ' minutes')::interval ELSE now() END WHERE target_id=:target_id AND claim_token=:claim_token RETURNING target_id", {"target_id": target_id, "claim_token": claim_token, "success": success})
        return bool(rows)


_QUERY_MAX_SIZE = 100
_QUERY_DEFAULT_SIZE = 20


def _query_size(size: int | None) -> int:
    return min(max(int(size or _QUERY_DEFAULT_SIZE), 1), _QUERY_MAX_SIZE)


def _query_cursor(cursor: str | None) -> tuple[str, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, ident = base64.urlsafe_b64decode(padded.encode()).decode().rsplit("|", 1)
        return stamp, int(ident)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("INVALID_ARGUMENT: 非法 cursor") from None


def _query_page(rows: list[dict[str, Any]], size: int, time_key: str, id_key: str) -> dict[str, Any]:
    page_rows = rows[:size]
    next_cursor = None
    if len(rows) > size and page_rows:
        stamp = page_rows[-1].get(time_key)
        stamp = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp or "")
        next_cursor = base64.urlsafe_b64encode(f"{stamp}|{page_rows[-1].get(id_key, 0)}".encode()).decode().rstrip("=")
    return {"items": page_rows, "next_cursor": next_cursor, "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


def get_creator(target_id: int) -> dict[str, Any] | None:
    rows = db.fetch_all("SELECT target_id, platform, nickname, monitor_level, source_type, whitelist, enabled, note, operation_state, history_cursor, last_success_cursor, last_success_at, last_summary_version, summary_updated_at, next_scan_at, last_scan_error, created_at FROM osint_target WHERE target_id=:target_id", {"target_id": target_id})
    return rows[0] if rows else None


def get_run(run_id: int) -> dict[str, Any] | None:
    """Return one creator run without mutating its lifecycle state."""
    rows = db.fetch_all(
        "SELECT run_id, target_id, run_type, state, input_cursor, output_cursor, items_seen, items_succeeded, items_failed, summary_version, error_code, error_detail, started_at, finished_at, created_at FROM osint_creator_run WHERE run_id=:run_id",
        {"run_id": run_id},
    )
    return rows[0] if rows else None


def list_runs(target_id: int, *, state: str | None = None, run_type: str | None = None, cursor: str | None = None, size: int = _QUERY_DEFAULT_SIZE) -> dict[str, Any]:
    limit = _query_size(size); params: dict[str, Any] = {"target_id": target_id, "limit": limit + 1}; clauses = ["target_id=:target_id"]
    if state: clauses.append("state=:state"); params["state"] = state
    if run_type: clauses.append("run_type=:run_type"); params["run_type"] = run_type
    decoded = _query_cursor(cursor)
    if decoded: clauses.append("(created_at, run_id) < (:cursor_time, :cursor_id)"); params.update({"cursor_time": decoded[0], "cursor_id": decoded[1]})
    rows = db.fetch_all(f"SELECT run_id, target_id, run_type, state, input_cursor, output_cursor, items_seen, items_succeeded, items_failed, summary_version, error_code, started_at, finished_at, created_at FROM osint_creator_run WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, run_id DESC LIMIT :limit", params)
    return _query_page(rows, limit, "created_at", "run_id")


def list_summaries(target_id: int, *, cursor: str | None = None, size: int = _QUERY_DEFAULT_SIZE) -> dict[str, Any]:
    limit = _query_size(size); params: dict[str, Any] = {"target_id": target_id, "limit": limit + 1}; clauses = ["target_id=:target_id"]
    decoded = _query_cursor(cursor)
    if decoded: clauses.append("(created_at, summary_id) < (:cursor_time, :cursor_id)"); params.update({"cursor_time": decoded[0], "cursor_id": decoded[1]})
    rows = db.fetch_all(f"SELECT summary_id, target_id, summary_version, summary_text, topics, viewpoints, evidence_count, model_version, snapshot_hash, created_at FROM osint_creator_summary WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, summary_id DESC LIMIT :limit", params)
    return _query_page(rows, limit, "created_at", "summary_id")


def list_creator_timeline(target_id: int, *, from_: str | None = None, to: str | None = None, cursor: str | None = None, size: int = _QUERY_DEFAULT_SIZE) -> dict[str, Any]:
    # osint_target is keyed by target_id; blogger_key is a knowledge/history
    # field and is not present on the target registry table.
    target = db.fetch_all("SELECT target_id FROM osint_target WHERE target_id=:target_id", {"target_id": target_id})
    if not target: return {"items": [], "next_cursor": None, "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    limit = _query_size(size); params: dict[str, Any] = {"blogger_key": str(target_id), "limit": limit + 1}; clauses = ["blogger_key=:blogger_key"]
    if from_: clauses.append("observed_at >= :from_"); params["from_"] = from_
    if to: clauses.append("observed_at <= :to"); params["to"] = to
    decoded = _query_cursor(cursor)
    if decoded: clauses.append("(observed_at, history_id) < (:cursor_time, :cursor_id)"); params.update({"cursor_time": decoded[0], "cursor_id": decoded[1]})
    rows = db.fetch_all(f"SELECT history_id, history_key, observed_at, event_type, summary, source_video_id, source_unit_id, created_at FROM osint_blogger_history WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC, history_id DESC LIMIT :limit", params)
    return _query_page(rows, limit, "observed_at", "history_id")
