# -*- coding: utf-8 -*-
"""TaskStore 与 legacy devtask 账本的只读双读适配器。

本模块只读取事实源，不调用 TaskStore 写接口，也不会修改事实源。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ADAPTER_VERSION = "m0-task-readonly-v1"
STATUS_MAP = {
    "todo": "open",
    "open": "open",
    "assigned": "assigned",
    "doing": "doing",
    "blocked": "blocked",
    "done": "done",
    "cancelled": "cancelled",
    "aborted": "cancelled",
    "rejected": "rejected",
    # review is a worktree review phase, not a TaskStore terminal state.
    "review": None,
}

# Cross-source reporting status. Keep raw/source_status alongside this value;
# these are reporting categories, not write-back instructions.
NORMALIZED_STATUS = {
    "todo": "active",
    "open": "active",
    "assigned": "active",
    "doing": "active",
    "blocked": "active",
    "done": "done",
    "cancelled": "aborted",
    "aborted": "aborted",
    "rejected": "aborted",
    "review": "review",
}


def _normalized_status(status: Any) -> str | None:
    return NORMALIZED_STATUS.get(status)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _freshness(path: Path) -> dict[str, Any]:
    mtime = _iso_mtime(path)
    if mtime is None:
        return {"state": "missing", "modified_at": None, "age_seconds": None}
    age = max(0.0, (_utc_now() - datetime.fromisoformat(mtime)).total_seconds())
    return {
        "state": "current",
        "modified_at": mtime,
        "age_seconds": round(age, 3),
    }


def _canonical_files(root: Path) -> list[Path]:
    return sorted(
        (root / "agent_platform" / "data" / "org" / "tasks").glob("tasks_*.jsonl")
    )


def _read_canonical(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    files = _canonical_files(root)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append({"kind": "read_error", "path": str(path), "detail": str(exc)})
            continue
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("record must be object")
                # Event rows share the append-only file but are not snapshots;
                # they may not carry task_id, so filter before snapshot validation.
                if "event" in item:
                    continue
                if not item.get("task_id"):
                    raise ValueError("record must be object with task_id")
                records.append({"_path": str(path), "_line": line_no, **item})
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(
                    {
                        "kind": "invalid_record",
                        "path": str(path),
                        "line": line_no,
                        "detail": str(exc),
                    }
                )
    latest: dict[str, dict[str, Any]] = {}
    for item in records:
        latest[item["task_id"]] = item
    modified = [_iso_mtime(p) for p in files if _iso_mtime(p)]
    if not files:
        errors.append(
            {
                "kind": "missing_source",
                "source": "canonical_taskstore",
                "path": str(root / "agent_platform" / "data" / "org" / "tasks"),
            }
        )
    source = {
        "name": "taskstore",
        "source": "canonical_taskstore",
        "paths": [str(p) for p in files],
        "exists": bool(files),
        "record_count": len(latest),
        "line_count": len(records),
        "duplicate_snapshot_count": len(records) - len(latest),
        "snapshot_at": max(modified) if modified else None,
        "snapshot_freshness": _freshness(files[-1])
        if files
        else {"state": "missing", "modified_at": None, "age_seconds": None},
    }
    return list(latest.values()), source, errors


def _read_legacy(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    path = root / ".worktrees" / "tasks.json"
    errors: list[dict[str, Any]] = []
    if not path.exists():
        return (
            [],
            {
                "name": "legacy_devtask",
                "source": "devtask_registry",
                "path": str(path),
                "exists": False,
                "record_count": 0,
                "snapshot_at": None,
                "snapshot_freshness": _freshness(path),
            },
            [
                {
                    "kind": "missing_source",
                    "source": "devtask_registry",
                    "path": str(path),
                }
            ],
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            [],
            {
                "name": "legacy_devtask",
                "source": "devtask_registry",
                "path": str(path),
                "exists": True,
                "record_count": 0,
                "snapshot_at": _iso_mtime(path),
                "snapshot_freshness": _freshness(path),
            },
            [
                {
                    "kind": "invalid_source",
                    "source": "devtask_registry",
                    "path": str(path),
                    "detail": str(exc),
                }
            ],
        )
    if not isinstance(payload, dict):
        errors.append(
            {
                "kind": "invalid_source",
                "source": "devtask_registry",
                "path": str(path),
                "detail": "expected object",
            }
        )
        payload = {}
    records = []
    for task_id, item in payload.items():
        if not isinstance(item, dict):
            errors.append(
                {
                    "kind": "invalid_record",
                    "source": "devtask_registry",
                    "task_id": task_id,
                }
            )
            continue
        records.append({"task_id": str(task_id), **item})
    return (
        records,
        {
            "name": "legacy_devtask",
            "source": "devtask_registry",
            "path": str(path),
            "exists": True,
            "record_count": len(records),
            "snapshot_at": _iso_mtime(path),
            "snapshot_freshness": _freshness(path),
        },
        errors,
    )


def _canonical_view(item: dict[str, Any]) -> dict[str, Any]:
    task_id = str(item["task_id"])
    owner = item.get("assigned_agent") or item.get("opened_by")
    title = item.get("title") or item.get("desc")
    created = item.get("created_at") or item.get("created")
    merged = item.get("merged")
    return {
        "id": task_id,
        "task_id": task_id,
        "source": "canonical_taskstore",
        "source_id": task_id,
        "source_status": item.get("status"),
        "raw_status": item.get("status"),
        "normalized_status": _normalized_status(item.get("status")),
        "canonical_status": item.get("status"),
        "mapping_confidence": 1.0,
        "owner": owner,
        "agent": item.get("assigned_agent") or owner,
        "project": item.get("project"),
        "desc": item.get("body") or item.get("desc"),
        "title": title,
        "created": created,
        "merged": merged,
        "conflicts": [],
        "raw": item,
    }


def _legacy_view(
    item: dict[str, Any], canonical: dict[str, Any] | None
) -> dict[str, Any]:
    status = item.get("status")
    mapped = STATUS_MAP.get(status, None)
    conflicts: list[dict[str, Any]] = []
    confidence = 0.65 if mapped is not None else 0.0
    if status == "review":
        conflicts.append(
            {
                "field": "status",
                "reason": "legacy review 不是 TaskStore done，禁止自动升级",
            }
        )
    elif status not in STATUS_MAP:
        conflicts.append({"field": "status", "reason": "legacy 状态无安全映射"})
    if canonical is not None:
        if canonical.get("status") != status:
            conflicts.append(
                {
                    "field": "status",
                    "canonical": canonical.get("status"),
                    "legacy": status,
                }
            )
        confidence = (
            0.9 if mapped == canonical.get("status") and not conflicts else 0.35
        )
    task_id = str(item["task_id"])
    owner = item.get("owner") or item.get("agent")
    desc = item.get("desc") or item.get("title")
    return {
        "id": task_id,
        "task_id": task_id,
        "source": "devtask_registry",
        "source_id": task_id,
        "source_status": status,
        "raw_status": status,
        "normalized_status": _normalized_status(status),
        "canonical_status": mapped,
        "mapping_confidence": confidence,
        "owner": owner,
        "agent": item.get("agent") or owner,
        "project": item.get("project"),
        "desc": desc,
        "title": item.get("title") or desc,
        "branch": item.get("branch"),
        "worktree": item.get("worktree"),
        "created": item.get("created") or item.get("created_at"),
        "merged": item.get("merged"),
        "conflicts": conflicts,
        "raw": item,
    }


def build_report(root: str | Path) -> dict[str, Any]:
    """读取两套事实源并生成差异报告；不会写入输入路径。"""
    root = Path(root).resolve()
    canonical, canonical_source, errors_a = _read_canonical(root)
    legacy, legacy_source, errors_b = _read_legacy(root)
    canonical_by_id = {item["task_id"]: item for item in canonical}
    legacy_by_id = {item["task_id"]: item for item in legacy}
    records = [_canonical_view(item) for item in canonical]
    records.extend(
        _legacy_view(item, canonical_by_id.get(item["task_id"])) for item in legacy
    )
    conflicts = []
    for item in records:
        for conflict in item["conflicts"]:
            conflicts.append(
                {"task_id": item["task_id"], "source": item["source"], **conflict}
            )
    canonical_ids, legacy_ids = set(canonical_by_id), set(legacy_by_id)
    matched = canonical_ids & legacy_ids
    coverage = {
        "canonical_unique": len(canonical_ids),
        "legacy_unique": len(legacy_ids),
        "matched_task_ids": len(matched),
        "canonical_only": len(canonical_ids - legacy_ids),
        "legacy_only": len(legacy_ids - canonical_ids),
        "canonical_match_rate": round(len(matched) / len(canonical_ids), 4)
        if canonical_ids
        else 0.0,
        "legacy_match_rate": round(len(matched) / len(legacy_ids), 4)
        if legacy_ids
        else 0.0,
    }
    return {
        "adapter_version": ADAPTER_VERSION,
        "generated_at": _utc_now().isoformat(),
        "root": str(root),
        "read_only": True,
        "sources": {"canonical": canonical_source, "legacy": legacy_source},
        "records": records,
        "conflicts": conflicts,
        "source_errors": errors_a + errors_b,
        "coverage": coverage,
        "snapshot_freshness": {
            "canonical": canonical_source["snapshot_freshness"],
            "legacy": legacy_source["snapshot_freshness"],
        },
    }


def write_report(root: str | Path, output: str | Path) -> dict[str, Any]:
    report = build_report(root)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成 TaskStore 与 legacy devtask 只读差异报告"
    )
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--output", help="JSON 报告输出路径；省略则输出到 stdout")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.output:
        report = write_report(args.root, args.output)
        print(
            json.dumps(
                {
                    "output": str(Path(args.output).resolve()),
                    "coverage": report["coverage"],
                    "conflicts": len(report["conflicts"]),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(build_report(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
