"""Read-only PostgreSQL queries for the management platform.

PostgreSQL is the authoritative source.  This module deliberately returns
small DTO dictionaries and never exposes media paths, encrypted values, or
collector payloads.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from common import db

MAX_SIZE = 100
DEFAULT_SIZE = 20

# Keys that must never cross the management/API boundary.  Filtering is
# recursive because collector payloads and evidence metadata can be nested.
_SENSITIVE_KEY_PARTS = frozenset({
    "raw_path", "value_enc", "cookie", "token", "authorization",
    "temporary_media_path",
})
_VALID_JUDGMENTS = frozenset({
    "supported", "refuted", "refuting", "contradicting", "contested",
    "uncertain", "unverified", "unknown", "correct", "incorrect",
})


def _sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEY_PARTS or any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any) -> Any:
    """Recursively remove sensitive collector values from JSON-like data."""
    if isinstance(value, dict):
        return {
            key: _sanitize_value(item)
            for key, item in value.items()
            if not _sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, datetime):
        return _utc(value)
    return value


def _utc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _as_of() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _size(size: int | None) -> int:
    return min(max(int(size or DEFAULT_SIZE), 1), MAX_SIZE)


def _decode_cursor(cursor: str | None) -> tuple[str, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts, ident = raw.rsplit("|", 1)
        return ts, int(ident)
    except (ValueError, TypeError, UnicodeError):
        return None


def _encode_cursor(value: Any, ident: Any) -> str:
    payload = f"{_utc(value) or ''}|{int(ident)}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _page(rows: list[dict[str, Any]], size: int, time_key: str = "updated_at", id_key: str = "id") -> dict[str, Any]:
    page_rows = rows[:size]
    next_cursor = None
    if len(rows) > size and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor(last.get(time_key), last.get(id_key, 0))
    return {"items": page_rows, "next_cursor": next_cursor, "as_of": _as_of()}


def _json_safe(row: dict[str, Any], *, sensitive: set[str] | None = None) -> dict[str, Any]:
    blocked = sensitive or set()
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in blocked:
            continue
        result[key] = _sanitize_value(value)
    return result


def list_creators(nickname: str | None = None, topic: str | None = None, entity: str | None = None,
                  finder_id: str | None = None, enabled: bool | None = None, monitor_level: int | None = None,
                  cursor: str | None = None, size: int = DEFAULT_SIZE) -> dict[str, Any]:
    if topic or entity:
        raise ValueError("topic/entity filters are unavailable for creator records")
    limit = _size(size)
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit + 1}
    if nickname:
        where.append("nickname ILIKE :nickname")
        params["nickname"] = f"%{nickname}%"
    if finder_id:
        where.append("finder_id = :finder_id")
        params["finder_id"] = finder_id
    if enabled is not None:
        where.append("enabled = :enabled")
        params["enabled"] = enabled
    if monitor_level is not None:
        where.append("monitor_level = :monitor_level")
        params["monitor_level"] = monitor_level
    decoded = _decode_cursor(cursor)
    if decoded:
        where.append("(created_at, target_id) < (:cursor_time, :cursor_id)")
        params.update({"cursor_time": decoded[0], "cursor_id": decoded[1]})
    clause = " WHERE " + " AND ".join(where) if where else ""
    rows = db.fetch_all(f"SELECT target_id, nickname, finder_id, monitor_level, source_type, enabled, note, created_at FROM osint_target{clause} ORDER BY created_at DESC, target_id DESC LIMIT :limit", params)
    return _page([_json_safe(r, sensitive={"finder_id"}) for r in rows], limit, "created_at", "target_id")


def list_units(q: str | None = None, topic: str | None = None, entity: str | None = None,
               target_id: str | None = None, status: str | None = None, unit_type: str | None = None,
               from_: str | None = None, to: str | None = None, cursor: str | None = None,
               size: int = DEFAULT_SIZE) -> dict[str, Any]:
    limit = _size(size)
    where: list[str] = ["u.status <> 'deleted'"]
    params: dict[str, Any] = {"limit": limit + 1}
    for key, value, expression in (("q", q, "(u.title ILIKE :q OR u.content ILIKE :q)"), ("topic", topic, "CAST(u.topics AS TEXT) ILIKE :topic"), ("entity", entity, "CAST(u.entities AS TEXT) ILIKE :entity"), ("unit_type", unit_type, "u.unit_type = :unit_type"), ("status", status, "u.review_status = :status"), ("from_", from_, "u.created_at >= :from_"), ("to", to, "u.created_at <= :to")):
        if value:
            where.append(expression)
            params[key] = f"%{value}%" if key in {"q", "topic", "entity"} else value
    if target_id:
        where.append("(u.blogger_key = :target_id OR u.resource_key = :target_id OR u.source_video_id = :target_id)")
        params["target_id"] = target_id
    decoded = _decode_cursor(cursor)
    if decoded:
        where.append("(u.updated_at, u.unit_id) < (:cursor_time, :cursor_id)")
        params.update({"cursor_time": decoded[0], "cursor_id": decoded[1]})
    rows = db.fetch_all(f"""SELECT u.unit_id, u.unit_key, u.unit_version,
      u.unit_version AS version, u.unit_type, u.title, u.content,
      u.source_channel, u.source_video_id, u.source_url, u.start_sec, u.end_sec,
      u.review_status, u.review_status AS public_status, u.blogger_key, u.resource_key,
      u.topics, u.entities, u.evidence_refs, u.quality, u.status, u.created_at,
      u.updated_at, u.updated_at AS pg_updated_at,
      (SELECT count(*) FROM osint_strategy_evidence e
        WHERE e.knowledge_unit_key=u.unit_key AND e.knowledge_unit_version=u.unit_version
          AND e.evidence_status <> 'rejected') AS evidence_count,
      COALESCE((SELECT CASE WHEN count(*) FILTER (WHERE i.status='failed') > 0 THEN 'failed'
          WHEN count(*) FILTER (WHERE i.status IN ('pending','running')) > 0 THEN 'pending'
          WHEN count(*) > 0 THEN 'ready' ELSE 'unknown' END
        FROM osint_index_task i JOIN osint_knowledge_resource r ON r.resource_key=i.resource_key
        WHERE r.resource_key=u.resource_key), 'unknown') AS index_status,
      (SELECT max(i.error_message) FROM osint_index_task i WHERE i.resource_key=u.resource_key AND i.status='failed') AS index_error
      FROM osint_knowledge_unit u WHERE {' AND '.join(where)}
      ORDER BY u.updated_at DESC, u.unit_id DESC LIMIT :limit""", params)
    return _page([_json_safe(r, sensitive={"source_url"}) for r in rows], limit, "updated_at", "unit_id")


def list_timeline(target_id: str | None = None, topic: str | None = None, entity: str | None = None,
                  event_type: str | None = None, from_: str | None = None, to: str | None = None,
                  cursor: str | None = None, size: int = DEFAULT_SIZE) -> dict[str, Any]:
    limit = _size(size)
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit + 1}
    for key, value, expression in (("target_id", target_id, "h.blogger_key = :target_id"), ("event_type", event_type, "h.event_type = :event_type"), ("from_", from_, "h.observed_at >= :from_"), ("to", to, "h.observed_at <= :to")):
        if value:
            where.append(expression); params[key] = value
    if topic:
        where.append("CAST(h.attributes AS TEXT) ILIKE :topic"); params["topic"] = f"%{topic}%"
    if entity:
        where.append("CAST(h.attributes AS TEXT) ILIKE :entity"); params["entity"] = f"%{entity}%"
    decoded = _decode_cursor(cursor)
    if decoded:
        where.append("(h.observed_at, h.history_id) < (:cursor_time, :cursor_id)"); params.update({"cursor_time": decoded[0], "cursor_id": decoded[1]})
    clause = " WHERE " + " AND ".join(where) if where else ""
    rows = db.fetch_all(f"SELECT h.history_id, h.blogger_key, h.history_key, h.observed_at, h.event_type, h.summary, h.attributes, h.source_video_id, h.source_unit_id FROM osint_blogger_history h{clause} ORDER BY h.observed_at DESC, h.history_id DESC LIMIT :limit", params)
    return _page([_json_safe(r) for r in rows], limit, "observed_at", "history_id")


def list_jobs(stage: str | None = None, state: str | None = None, cursor: str | None = None, size: int = DEFAULT_SIZE) -> dict[str, Any]:
    limit = _size(size)
    params: dict[str, Any] = {"limit": limit + 1}
    # Normalize all index fan-out targets to the public ``index`` stage.  The
    # filter is applied in SQL before LIMIT so filtered pages are complete.
    filters: list[str] = []
    if stage:
        filters.append("stage = :stage")
        params["stage"] = "index" if stage in {"pg", "qdrant", "graph", "index"} else stage
    if state:
        filters.append("state = :state")
        params["state"] = state
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = db.fetch_all(f"""WITH all_jobs AS (
      SELECT 'collection' AS stage, CAST(id AS TEXT) AS id, status AS state,
             retry_count AS attempt, error_code, error_msg AS error,
             created_at AS started_at, updated_at AS finished_at,
             id AS sort_id, updated_at AS sort_time
        FROM osint_link_task
      UNION ALL
      SELECT 'processing' AS stage, CAST(run_id AS TEXT) AS id, status AS state,
             0 AS attempt, NULL AS error_code, error_message AS error,
             started_at, finished_at, run_id AS sort_id, updated_at AS sort_time
        FROM osint_processing_run
      UNION ALL
      SELECT 'index' AS stage, CAST(task_id AS TEXT) AS id, status AS state,
             attempt_count AS attempt, NULL AS error_code, error_message AS error,
             created_at AS started_at, updated_at AS finished_at,
             task_id AS sort_id, updated_at AS sort_time
        FROM osint_index_task
    )
    SELECT * FROM all_jobs {where}
    ORDER BY sort_time DESC, sort_id DESC LIMIT :limit""", params)
    return _page([_json_safe(r, sensitive={"sort_id", "sort_time"}) for r in rows], limit, "finished_at", "id")


def changes(cursor: str | None = None, scope: str = "dashboard") -> dict[str, Any]:
    params: dict[str, Any] = {"limit": 200}
    since = _decode_cursor(cursor)
    if since:
        params["since"] = since[0]
    time_filter = "AND updated_at > :since" if since else ""
    tables = {"links": "osint_link_task", "knowledge": "osint_knowledge_unit", "jobs": "osint_index_task"}
    selected = tables if scope == "dashboard" else ({scope: tables[scope]} if scope in tables else {})
    ids: list[str] = []
    for name, table in selected.items():
        id_col = "id" if name == "links" else ("unit_id" if name == "knowledge" else "task_id")
        rows = db.fetch_all(f"SELECT CAST({id_col} AS TEXT) AS id FROM {table} WHERE 1=1 {time_filter} ORDER BY updated_at DESC LIMIT :limit", params)
        ids.extend(f"{name}:{r['id']}" for r in rows)
    if scope in {"dashboard", "jobs"}:
        rows = db.fetch_all(f"SELECT 'processing:' || CAST(run_id AS TEXT) AS changed_id FROM osint_processing_run WHERE 1=1 {time_filter} ORDER BY updated_at DESC LIMIT :limit", params)
        ids.extend(str(r["changed_id"]) for r in rows)
        rows = db.fetch_all(f"SELECT 'index:' || CAST(task_id AS TEXT) AS changed_id FROM osint_index_task WHERE 1=1 {time_filter} ORDER BY updated_at DESC LIMIT :limit", params)
        ids.extend(str(r["changed_id"]) for r in rows)
    return {"cursor": _encode_cursor(datetime.now(timezone.utc), 0), "changed_ids": ids, "as_of": _as_of()}


def _number(value: Any) -> float | None:
    """Coerce a JSON/SQL number to a finite float, otherwise return None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _unit_content_fields(unit: dict[str, Any]) -> dict[str, Any]:
    """Derive additive proposition fields from legacy ``facts`` JSON.

    ``facts.judgment`` historically appeared as either a status string or a
    mapping.  The public projection is always a structured mapping so callers
    can safely consume the status, reasoning and confidence independently.
    Raw source content is never treated as a positive judgment: absent or
    invalid statuses are explicitly exposed as ``unverified``.
    """
    facts = unit.get("facts") or {}
    if not isinstance(facts, dict):
        facts = {}

    extension = facts.get("extension")
    if not isinstance(extension, dict):
        extension = unit.get("extension") if isinstance(unit.get("extension"), dict) else {}

    judgment_value = facts.get("judgment")
    if judgment_value is None:
        judgment_value = facts.get("truth_status")
        # A source-only fact is context, not a claim judgment.  Legacy unit
        # truth_status remains usable when no source_content-only payload exists.
        if judgment_value is None and "source_content" not in facts:
            judgment_value = unit.get("truth_status")
    reasoning: Any = None
    confidence: Any = None
    if isinstance(judgment_value, dict):
        status_value = judgment_value.get("status") or judgment_value.get("truth_status")
        reasoning = judgment_value.get("reasoning")
        confidence = judgment_value.get("confidence")
    else:
        status_value = judgment_value
        reasoning = facts.get("judgment_reasoning") or facts.get("reasoning")
        confidence = facts.get("judgment_confidence")
        if confidence is None:
            confidence = facts.get("confidence")
    status = str(status_value or "unverified").strip().lower()
    if status not in _VALID_JUDGMENTS:
        status = "unverified"

    proposition = facts.get("proposition")
    if proposition is None:
        proposition = facts.get("claim")
    if proposition is None:
        proposition = unit.get("content") or unit.get("title")
    proposition_type = facts.get("proposition_type") or facts.get("type") or unit.get("unit_type")
    source_content = facts.get("source_content")
    if source_content is None:
        source_content = unit.get("content")
    return {
        "proposition": _sanitize_value(proposition),
        "proposition_type": _sanitize_value(proposition_type),
        "source_content": _sanitize_value(source_content),
        "extension": _sanitize_value(extension),
        "judgment": {
            "status": status,
            "reasoning": _sanitize_value(reasoning),
            "confidence": _sanitize_value(confidence),
        },
    }


def _evidence_bucket(role: Any, relation: Any = None) -> str:
    """Normalize historical evidence role/relation to the public buckets."""
    value = str(relation or role or "context").strip().lower()
    if value in {"supporting", "support", "supports", "supported"}:
        return "supports"
    if value in {"refuting", "refute", "refuted", "contradicting", "contradiction", "contradicts"}:
        return "contradicts"
    return "context"


def _evidence_groups(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate evidence into canonical and backwards-compatible buckets."""
    grouped: dict[str, list[dict[str, Any]]] = {"supports": [], "contradicts": [], "context": []}
    for item in items:
        grouped[_evidence_bucket(item.get("evidence_role"), item.get("relation"))].append(item)
    return {
        **grouped,
        "supporting": grouped["supports"],
        "refuting": grouped["contradicts"],
        "counts": {key: len(value) for key, value in grouped.items()},
    }


def _evidence_dto(row: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    """Build the allow-listed EvidenceChain DTO and validate its association."""
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    start = _number(row.get("quote_start_sec"))
    end = _number(row.get("quote_end_sec"))
    unit_start = _number(unit.get("start_sec"))
    unit_end = _number(unit.get("end_sec"))
    duration = _number((row.get("resource") or {}).get("duration_sec"))
    time_valid = start is None or end is None or end > start
    if unit_start is not None and start is not None:
        time_valid = time_valid and start >= unit_start
    if unit_end is not None and end is not None:
        time_valid = time_valid and end <= unit_end
    if duration is not None and end is not None:
        time_valid = time_valid and end <= duration
    version_match = int(row.get("knowledge_unit_version", 1) or 1) == int(unit.get("unit_version", 1) or 1)
    reliability = _number(metadata.get("reliability"))
    if reliability is None:
        reliability = _number(row.get("confidence"))
    if reliability is None:
        reliability = _number(row.get("relevance"))
    status = str(row.get("evidence_status") or "draft")
    valid = status == "valid" and bool((row.get("quote") or "").strip()) and bool(row.get("snapshot_hash")) and time_valid and version_match
    raw_relation = metadata.get("relation") or metadata.get("relationship")
    canonical_relation = _evidence_bucket(row.get("evidence_role"), raw_relation)
    reference = metadata.get("reference") or metadata.get("source_reference")
    dto = {
        **_unit_content_fields(unit),
        "evidence_key": row.get("evidence_key"),
        "evidence_id": row.get("evidence_id"),
        "strategy_key": row.get("strategy_key"),
        "unit_key": row.get("knowledge_unit_key"),
        "unit_version": int(row.get("knowledge_unit_version", 1) or 1),
        # Keep evidence_role for backwards compatibility, while relation and
        # evidence_type expose one canonical public vocabulary.
        "evidence_role": row.get("evidence_role", "supporting"),
        "evidence_type": canonical_relation,
        "relation": canonical_relation,
        "reference": reference,
        "source_channel": unit.get("source_channel"),
        "quote": row.get("quote"),
        "start_sec": start,
        "end_sec": end,
        "reliability": reliability,
        "snapshot_hash": row.get("snapshot_hash"),
        "validity": {"status": status, "is_valid": valid, "time_valid": time_valid, "version_match": version_match},
        "source_resource": row.get("resource"),
        "created_at": _utc(row.get("created_at")),
    }
    return _sanitize_value(dto)


def _unit_resource(resource_key: str | None) -> dict[str, Any] | None:
    """Return public source metadata without temporary media paths or payloads."""
    if not resource_key:
        return None
    rows = db.fetch_all(
        """SELECT resource_key, blogger_key, source_url, platform_content_id,
                  published_at, duration_sec, status, asr_text, ocr_text, subtitle_text, updated_at
           FROM osint_knowledge_resource WHERE resource_key=:resource_key""", 
        {"resource_key": resource_key},
    )
    return _json_safe(rows[0]) if rows else None


def _index_status(resource_keys: list[str]) -> list[dict[str, Any]]:
    """Read PG index fan-out status; Qdrant failures remain ordinary data."""
    result: list[dict[str, Any]] = []
    for resource_key in sorted(set(key for key in resource_keys if key)):
        rows = db.fetch_all(
            """SELECT target, status, error_message, attempt_count, updated_at
               FROM osint_index_task WHERE resource_key=:resource_key ORDER BY target""",
            {"resource_key": resource_key},
        )
        result.extend({"resource_key": resource_key, **_json_safe(row)} for row in rows)
    return result


def get_knowledge_unit_detail(unit_key: str, version: int | None = None) -> dict[str, Any] | None:
    """Return versioned unit details, evidence and index status from PostgreSQL.

    The projection is deliberately allow-listed.  In particular, raw media paths,
    encrypted credentials and collector payloads never leave this function.
    """
    key = str(unit_key).strip()
    if not key:
        return None
    params: dict[str, Any] = {"unit_key": key}
    version_clause = " AND unit_version=:unit_version" if version is not None else ""
    if version is not None:
        params["unit_version"] = int(version)
    units = db.fetch_all(
        f"""SELECT unit_id, unit_key, unit_version, unit_type, title, content,
                  source_channel, source_video_id, source_url, source_link,
                  start_sec, end_sec, content_hash, review_status, blogger_key,
                  resource_key, facts, evidence_refs, quality, status, truth_status,
                  extension, created_at, updated_at
           FROM osint_knowledge_unit WHERE unit_key=:unit_key{version_clause}
           ORDER BY unit_version DESC""",
        params,
    )
    if not units:
        return None
    versions: list[dict[str, Any]] = []
    all_resource_keys: list[str] = []
    for unit in units:
        resource = _unit_resource(unit.get("resource_key"))
        if unit.get("resource_key"):
            all_resource_keys.append(str(unit["resource_key"]))
        evidence_rows = db.fetch_all(
            """SELECT e.evidence_id, e.strategy_key, e.knowledge_unit_key,
                      e.knowledge_unit_version, e.evidence_key, e.evidence_role,
                      e.quote, e.relevance, e.confidence, e.quote_start_sec,
                      e.quote_end_sec, e.evidence_status, e.snapshot_hash,
                      e.metadata, e.created_at
               FROM osint_strategy_evidence e
               WHERE e.knowledge_unit_key=:unit_key
                 AND e.knowledge_unit_version=:unit_version
               ORDER BY e.created_at DESC, e.evidence_id DESC""",
            {"unit_key": key, "unit_version": unit["unit_version"]},
        )
        for evidence in evidence_rows:
            evidence["resource"] = resource
        evidence = [_evidence_dto(row, unit) for row in evidence_rows]
        version_dto = {
            "unit_id": unit.get("unit_id"), "unit_key": unit["unit_key"],
            "unit_version": int(unit["unit_version"]), "unit_type": unit.get("unit_type"),
            **_unit_content_fields(unit),
            "title": unit.get("title"), "content": unit.get("content", ""),
            "source_channel": unit.get("source_channel"), "source_video_id": unit.get("source_video_id"),
            "source_url": unit.get("source_url"), "source_link": unit.get("source_link"),
            "start_sec": unit.get("start_sec"), "end_sec": unit.get("end_sec"),
            "content_hash": unit.get("content_hash"), "review_status": unit.get("review_status"),
            "blogger_key": unit.get("blogger_key"), "resource_key": unit.get("resource_key"),
            "evidence_refs": evidence, "quality": unit.get("quality") or {},
            "status": unit.get("status"), "source_resource": resource,
            "created_at": _utc(unit.get("created_at")), "updated_at": _utc(unit.get("updated_at")),
        }
        versions.append(_sanitize_value(version_dto))
    current = versions[0]
    aggregates = _evidence_groups(current["evidence_refs"])
    return {
        "unit_key": key, "current_version": current["unit_version"],
        "versions": versions, "source_resources": [v["source_resource"] for v in versions if v["source_resource"]],
        "evidence_refs": current["evidence_refs"], **aggregates,
        "index_status": _index_status(all_resource_keys),
        "content_channels": {
            "asr": current["source_resource"].get("asr_text") if current["source_resource"] else None,
            "ocr": current["source_resource"].get("ocr_text") if current["source_resource"] else None,
            "subtitle": current["source_resource"].get("subtitle_text") if current["source_resource"] else None,
        },
        "as_of": _as_of(),
    }


def get_evidence(evidence_key: str) -> dict[str, Any] | None:
    """Return one evidence edge joined to its exact knowledge-unit version."""
    rows = db.fetch_all(
        """SELECT e.evidence_id, e.strategy_key, e.knowledge_unit_key,
                  e.knowledge_unit_version, e.evidence_key, e.evidence_role,
                  e.quote, e.relevance, e.confidence, e.quote_start_sec,
                  e.quote_end_sec, e.evidence_status, e.snapshot_hash,
                  e.metadata, e.created_at,
                  u.unit_version, u.source_channel, u.start_sec, u.end_sec,
                  u.resource_key, u.content, u.title, u.unit_type, u.facts, u.extension
           FROM osint_strategy_evidence e
           JOIN osint_knowledge_unit u
             ON u.unit_key=e.knowledge_unit_key
            AND u.unit_version=e.knowledge_unit_version
           WHERE e.evidence_key=:evidence_key LIMIT 1""",
        {"evidence_key": str(evidence_key).strip()},
    )
    if not rows:
        return None
    row = rows[0]
    unit = {"unit_version": row["unit_version"], "source_channel": row["source_channel"], "start_sec": row.get("start_sec"), "end_sec": row.get("end_sec"), "content": row.get("content"), "title": row.get("title"), "unit_type": row.get("unit_type"), "facts": row.get("facts"), "extension": row.get("extension")}
    row["resource"] = _unit_resource(row.get("resource_key"))
    return _evidence_dto(row, unit)


def get_strategy_evidence_chain(strategy_key: str, version: int | None = None) -> dict[str, Any] | None:
    """Return all evidence for a strategy, with exact version/time validation."""
    params: dict[str, Any] = {"strategy_key": str(strategy_key).strip()}
    version_clause = " AND e.knowledge_unit_version=:unit_version" if version is not None else ""
    if version is not None:
        params["unit_version"] = int(version)
    rows = db.fetch_all(
        f"""SELECT e.evidence_id, e.strategy_key, e.knowledge_unit_key,
                  e.knowledge_unit_version, e.evidence_key, e.evidence_role,
                  e.quote, e.relevance, e.confidence, e.quote_start_sec,
                  e.quote_end_sec, e.evidence_status, e.snapshot_hash,
                  e.metadata, e.created_at, u.unit_version, u.source_channel,
                  u.start_sec, u.end_sec, u.resource_key, u.content, u.title,
                  u.unit_type, u.facts, u.extension
           FROM osint_strategy_evidence e
           JOIN osint_knowledge_unit u
             ON u.unit_key=e.knowledge_unit_key
            AND u.unit_version=e.knowledge_unit_version
           WHERE e.strategy_key=:strategy_key{version_clause}
           ORDER BY e.knowledge_unit_version DESC, e.created_at DESC, e.evidence_id DESC""",
        params,
    )
    if not rows:
        return None
    chain: list[dict[str, Any]] = []
    for row in rows:
        unit = {"unit_version": row["unit_version"], "source_channel": row["source_channel"], "start_sec": row.get("start_sec"), "end_sec": row.get("end_sec"), "content": row.get("content"), "title": row.get("title"), "unit_type": row.get("unit_type"), "facts": row.get("facts"), "extension": row.get("extension")}
        row["resource"] = _unit_resource(row.get("resource_key"))
        chain.append(_evidence_dto(row, unit))
    warnings = [item["evidence_key"] for item in chain if not item["validity"]["is_valid"]]
    aggregates = _evidence_groups(chain)
    return {
        "strategy_key": str(strategy_key).strip(), "evidence": chain,
        **aggregates, "quality_warnings": warnings, "as_of": _as_of(),
    }


def resource_publication_stats(blogger_key: str | None = None) -> dict[str, Any]:
    """Return durable video publication counts and date distribution.

    Only resource metadata is queried; temporary media paths and video bodies are
    intentionally excluded from the response.
    """
    params: dict[str, Any] = {}
    where = "WHERE published_at IS NOT NULL"
    if blogger_key:
        where += " AND blogger_key=:blogger_key"
        params["blogger_key"] = blogger_key
    rows = db.fetch_all(
        f"""SELECT date_trunc('day', published_at) AS published_day, count(*) AS count,
                   sum(duration_sec) AS duration_sec
            FROM osint_knowledge_resource {where}
            GROUP BY date_trunc('day', published_at)
            ORDER BY published_day DESC""", params,
    )
    total = db.fetch_all(f"SELECT count(*) AS count, sum(duration_sec) AS duration_sec FROM osint_knowledge_resource {where}", params)
    total_row = total[0] if total else {"count": 0, "duration_sec": 0}
    return {
        "total_videos": int(total_row.get("count") or 0),
        "total_duration_sec": float(total_row.get("duration_sec") or 0),
        "by_published_day": [_json_safe(row) for row in rows],
        "as_of": _as_of(),
    }


def dashboard_summary(window: str = "24h") -> dict[str, Any]:
    if window not in {"24h", "7d"}:
        window = "24h"
    stats = db.fetch_all("SELECT status, count(*) AS count FROM osint_link_task GROUP BY status")
    counts = {str(r["status"]): int(r["count"]) for r in stats}
    unit_rows = db.fetch_all("SELECT count(*) AS count FROM osint_knowledge_unit WHERE review_status='unreviewed' AND status <> 'deleted'")
    pending = sum(counts.get(s, 0) for s in ("pending", "parsed", "downloaded", "decrypted"))
    processing = counts.get("extracted", 0)
    index_rows = db.fetch_all("SELECT count(*) AS count FROM osint_index_task WHERE status <> 'succeeded'")
    evidence_rows = db.fetch_all("""SELECT count(*) FILTER (WHERE u.status='published') AS published,
      count(*) FILTER (WHERE u.status='published' AND EXISTS (
        SELECT 1 FROM osint_strategy_evidence e WHERE e.knowledge_unit_key=u.unit_key
          AND e.knowledge_unit_version=u.unit_version AND e.evidence_status='valid')) AS complete
      FROM osint_knowledge_unit u""")
    published = int(evidence_rows[0]["published"] or 0) if evidence_rows else 0
    complete = int(evidence_rows[0]["complete"] or 0) if evidence_rows else 0
    evidence_rate = round(complete / published, 4) if published else 1.0
    index_lag = int(index_rows[0]["count"] or 0) if index_rows else 0
    cards = [{"key": "pending_links", "label": "待处理链接", "value": pending}, {"key": "processing", "label": "处理中", "value": processing}, {"key": "knowledge_review", "label": "待审核知识", "value": int(unit_rows[0]["count"]) if unit_rows else 0}, {"key": "evidence_completeness", "label": "证据完整率", "value": evidence_rate}, {"key": "index_lag", "label": "索引延迟", "value": index_lag}, {"key": "dead_letters", "label": "死信/挂起", "value": sum(counts.get(s, 0) for s in ("dead", "waiting_cookie", "quarantine"))}]
    return {"cards": [dict(card, delta=0, level="normal", as_of=_as_of(), drilldown=f"/api/v1/{card['key']}") for card in cards], "health": {"pg": "ok"}, "alerts": [], "window": window, "as_of": _as_of()}
