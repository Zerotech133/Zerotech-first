# -*- coding: utf-8 -*-
"""M0 事实源只读适配器。

读取 portal.db/events、llm_tool_audit.jsonl 与 push_flow.jsonl，生成内部
canonical v2 事件。适配器绝不初始化、写入或修改任何事实源。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ADAPTER_VERSION = "m0-event-readonly-v2"
SCHEMA_VERSION = "2.0"
REQUIRED_FIELDS = (
    "source", "source_id", "event_id", "ts", "kind", "actor", "object_id",
    "object_type", "trace_id", "causation_id", "idempotency_key", "payload",
    "provenance", "event_hash", "raw",
)


class _PayloadError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _freshness(path: Path) -> dict[str, Any]:
    value = _mtime(path)
    if value is None:
        return {"state": "missing", "modified_at": None, "age_seconds": None}
    age = max(0.0, (_now() - datetime.fromisoformat(value)).total_seconds())
    return {"state": "current", "modified_at": value, "age_seconds": round(age, 3)}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 不允许 NaN 或无穷数")
        return value
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return str(value)


def _parse_ts(value: Any) -> str | None:
    """解析为 UTC；naive 或非法时间返回 None，不静默补时区。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _time_flags(value: Any) -> tuple[bool, bool]:
    if value is None or value == "":
        return False, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            datetime.fromtimestamp(value, timezone.utc)
            return False, False
        except (OverflowError, OSError, ValueError):
            return False, True
    text = str(value).strip()
    if not text:
        return False, False
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False, True
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return True, False
    return False, False


def _first(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = item.get(name)
        if value is not None and value != "":
            return value
    return None


def _parse_payload_value(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise _PayloadError(f"payload JSON 解析错误: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _PayloadError("payload 必须是 JSON 对象")
    return _json_value(value)


def _payload(item: dict[str, Any], consumed: set[str], source: str) -> dict[str, Any]:
    payload = _parse_payload_value(item.get("payload"))
    residual = {str(k): _json_value(v) for k, v in item.items() if k not in consumed}
    if source == "push_flow":
        # push_flow 的计费/路由字段是事实，不能因通用字段映射而丢失。
        for name in ("kind", "channel", "target", "count", "ok", "tenant"):
            if name in item:
                payload[name] = _json_value(item[name])
    payload.update(residual)
    return payload


def _envelope(adapter_source: str, item: dict[str, Any], line_no: int, path: Path) -> dict[str, Any]:
    if adapter_source == "portal_db.events":
        event_source = _first(item, "source") or adapter_source
        kind_value = _first(item, "kind", "event", "event_type", "eventType", "type", "action")
        actor_value = _first(item, "actor", "actor_id", "actorId", "user", "agent", "caller")
    elif adapter_source == "llm_tool_audit":
        event_source = "llm_tool_audit/heartbeat"
        kind_value = _first(item, "action", "kind", "event", "event_type", "type")
        actor_value = _first(item, "caller", "actor", "actor_id", "agent", "user")
    else:
        event_source = "push_flow"
        kind_value = _first(item, "kind", "event", "event_type", "eventType", "type", "action")
        actor_value = _first(item, "actor", "actor_id", "actorId", "user", "agent", "caller")

    source_id_value = _first(item, "source_id", "sourceId", "id")
    event_id_value = _first(item, "event_id", "eventId", "id")
    source_id = str(source_id_value) if source_id_value is not None else None
    event_id = str(event_id_value) if event_id_value is not None else None
    time_value = _first(item, "ts", "timestamp", "occurred_at", "occurredAt", "created_at", "createdAt", "time", "available_at")
    ts = _parse_ts(time_value)
    timezone_missing, timezone_invalid = _time_flags(time_value)
    kind = str(kind_value) if kind_value is not None else "unknown"
    actor = str(actor_value) if actor_value is not None else None
    object_id = _first(item, "object_id", "objectId", "entity_id", "entityId", "task_id", "order_id")
    object_type = _first(item, "object_type", "objectType", "entity_type", "entityType")
    trace_id = _first(item, "trace_id", "traceId", "correlation_id", "correlationId")
    causation_id = _first(item, "causation_id", "causationId", "parent_event_id", "parentEventId")
    idempotency = _first(item, "idempotency_key", "idempotencyKey", "dedupe_key")
    consumed = {
        "source_id", "sourceId", "id", "event_id", "eventId", "ts", "timestamp",
        "occurred_at", "occurredAt", "created_at", "createdAt", "time", "available_at",
        "kind", "event", "event_type", "eventType", "type", "action", "actor",
        "actor_id", "actorId", "user", "agent", "caller", "object_id", "objectId",
        "entity_id", "entityId", "task_id", "order_id", "object_type", "objectType",
        "entity_type", "entityType", "trace_id", "traceId", "correlation_id",
        "correlationId", "causation_id", "causationId", "parent_event_id",
        "parentEventId", "idempotency_key", "idempotencyKey", "dedupe_key", "payload", "source",
    }
    raw = _json_value(item)
    payload = _payload(item, consumed, adapter_source)
    provenance = {
        "adapter_source": adapter_source,
        "path": str(path),
        "line": line_no,
        "identity_missing": source_id is None and event_id is None,
        "derived_source_id": source_id is None,
        "timezone_missing": timezone_missing,
        "timezone_invalid": timezone_invalid,
        "environment_derived": _first(item, "environment", "env") is None,
    }
    envelope = {
        "source": event_source,
        "source_id": source_id,
        "event_id": event_id,
        "ts": ts,
        "kind": kind,
        "actor": actor,
        "object_id": str(object_id) if object_id is not None else None,
        "object_type": str(object_type) if object_type is not None else None,
        "trace_id": str(trace_id) if trace_id is not None else None,
        "causation_id": str(causation_id) if causation_id is not None else None,
        "idempotency_key": str(idempotency) if idempotency is not None else None,
        "payload": payload,
        "provenance": provenance,
        "raw": raw,
    }
    hash_body = {key: value for key, value in envelope.items() if key not in {"event_hash", "raw"}}
    canonical = json.dumps(hash_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    envelope["event_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return envelope


def _missing_meta(source: str, path: Path) -> dict[str, Any]:
    return {"source": source, "path": str(path), "exists": False, "record_count": 0, "freshness": _freshness(path)}


def _read_jsonl(path: Path, source: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if not path.exists():
        return [], _missing_meta(source, path), [{"kind": "missing_source", "source": source, "path": str(path)}]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [], {"source": source, "path": str(path), "exists": True, "record_count": 0, "freshness": _freshness(path)}, [{"kind": "decode_error", "source": source, "path": str(path), "detail": str(exc)}]
    except OSError as exc:
        return [], {"source": source, "path": str(path), "exists": True, "record_count": 0, "freshness": _freshness(path)}, [{"kind": "read_error", "source": source, "path": str(path), "detail": str(exc)}]
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"不允许 JSON 常量 {value}")))
            if not isinstance(item, dict):
                raise ValueError("record must be an object")
            records.append(_envelope(source, item, line_no, path))
        except _PayloadError as exc:
            errors.append({"kind": "payload_parse_error", "source": source, "path": str(path), "line": line_no, "detail": str(exc)})
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            errors.append({"kind": "invalid_record", "source": source, "path": str(path), "line": line_no, "detail": str(exc)})
    return records, {"source": source, "path": str(path), "exists": True, "record_count": len(records), "freshness": _freshness(path)}, errors


def _read_portal(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    path = root / "portal" / "data" / "portal.db"
    source = "portal_db.events"
    if not path.exists():
        return [], _missing_meta(source, path), [{"kind": "missing_source", "source": source, "path": str(path)}]
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    con = None
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM events ORDER BY id").fetchall()
        for row_no, row in enumerate(rows, 1):
            try:
                records.append(_envelope(source, dict(row), row_no, path))
            except _PayloadError as exc:
                errors.append({"kind": "payload_parse_error", "source": source, "path": str(path), "line": row_no, "detail": str(exc)})
            except (ValueError, TypeError) as exc:
                errors.append({"kind": "invalid_record", "source": source, "path": str(path), "line": row_no, "detail": str(exc)})
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        errors.append({"kind": "read_error", "source": source, "path": str(path), "detail": str(exc)})
    finally:
        if con is not None:
            con.close()
    return records, {"source": source, "path": str(path), "exists": True, "record_count": len(records), "freshness": _freshness(path)}, errors


def build_report(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    all_records: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for reader in (
        lambda: _read_portal(root),
        lambda: _read_jsonl(root / "agent_platform" / "data" / "llm_tool_audit.jsonl", "llm_tool_audit"),
        lambda: _read_jsonl(root / "portal" / "work" / "push_flow.jsonl", "push_flow"),
    ):
        records, meta, source_errors = reader()
        all_records.extend(records)
        sources[meta["source"]] = meta
        errors.extend(source_errors)
    counts: dict[str, int] = {name: meta["record_count"] for name, meta in sources.items()}
    counts["total"] = len(all_records)
    canonical_errors: list[dict[str, Any]] = []
    for index, event in enumerate(all_records):
        missing = [field for field in REQUIRED_FIELDS if field not in event]
        if missing:
            canonical_errors.append({"index": index, "kind": "missing_fields", "fields": missing})
            continue
        try:
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            canonical_errors.append({"index": index, "kind": "non_canonical_json", "detail": str(exc)})
    canonical_valid = not canonical_errors
    return {
        "adapter_version": ADAPTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now().isoformat(),
        "root": str(root),
        "read_only": True,
        "sources": sources,
        "counts": counts,
        "events": all_records,
        "records": all_records,
        "raw": [event["raw"] for event in all_records],
        "source_errors": errors,
        "freshness": {name: meta["freshness"] for name, meta in sources.items()},
        "canonical_valid": canonical_valid,
        "canonical_errors": canonical_errors,
        "v1_compatibility": {
            "status": "partial" if all_records else "false",
            "reasons": ["内部事件为 schema_version=2，不能直接冒充 ai_operator v1 顶层事件", "v2 扩展字段仅保留在 payload/provenance/metadata 内"],
            "allowed_top_level": ["schema_version", "kind", "environment", "source", "source_id", "occurred_at", "available_at", "payload"],
        },
    }


def write_report(root: str | Path, output: str | Path) -> dict[str, Any]:
    report = build_report(root)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 M0 EventEnvelope 只读统一报告")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--output", help="JSON 报告输出路径；省略则输出到 stdout")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.output:
        report = write_report(args.root, args.output)
        print(json.dumps({"output": str(Path(args.output).resolve()), "counts": report["counts"], "source_errors": len(report["source_errors"])}, ensure_ascii=False))
    else:
        print(json.dumps(build_report(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
