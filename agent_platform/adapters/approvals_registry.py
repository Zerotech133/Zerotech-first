# -*- coding: utf-8 -*-
"""M1-阶段二 Approval 注册表只读数据面（供门户 /api/platform/approvals 与 CLI）。

设计约定（承 M0/M1 判例）：
- **只读直读**：sqlite mode=ro 打开 approvals/approvals.db，
  不实例化写侧 engine/approvals.ApprovalStore（其 __init__ 含
  CREATE TABLE IF NOT EXISTS 写路径）；表缺失/库缺失 fail-visible。
- 参数白名单；target 过滤为完全匹配；无缓存（审批低频、直读毫秒级）。
- 输出记录不做语义改写：from_status/to_status/decided_by/reason 原样，
  语义判定（跳级/回退）是 UI/审计层职责，不在数据面越权。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REGISTRY_VERSION = "1.0.0"

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

_SORTABLE = ("decided_at", "created_at", "rowid")

_CREDENTIAL_FIELDS = (
    "approval_id",
    "idempotency_key",
    "target_type",
    "target_id",
    "from_status",
    "to_status",
    "decision",
    "decided_by",
    "reason",
    "decided_at",
    "schema_version",
    "created_at",
    "trace_id",
)


def _db_path(root: Path) -> Path:
    return root / "agent_platform" / "data" / "approvals" / "approvals.db"


def _ro_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build_registry(
    root: str | Path,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    decided_by: str | None = None,
    limit: int = DEFAULT_LIMIT,
    order: str = "desc",
) -> dict[str, Any]:
    root = Path(root).resolve()
    if order not in ("asc", "desc"):
        raise ValueError(f"order 须为 asc/desc，收到 {order!r}")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit 须为整数，收到 {limit!r}")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 须在 1..{MAX_LIMIT}，收到 {limit}")

    filters = {}
    for name in ("target_type", "target_id", "decided_by"):
        value = locals().get(name)
        if value not in (None, ""):
            filters[name] = str(value)

    db = _db_path(root)
    missing_db = not db.exists()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    by_domain: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    by_target: dict[str, int] = {}
    first_seen: str | None = None
    last_seen: str | None = None

    if missing_db:
        errors.append(
            {"kind": "missing_source", "source": "approvals.db", "path": str(db)}
        )
    else:
        con = None
        try:
            con = _ro_connect(db)
            where = " AND ".join(f"{k} = ?" for k in filters)
            sql = (
                "SELECT rowid AS _rowid, * FROM approvals"  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                + (f" WHERE {where}" if where else "")
                + f" ORDER BY decided_at {'DESC' if order == 'desc' else 'ASC'}, rowid {'DESC' if order == 'desc' else 'ASC'}"
            )
            rows = con.execute(sql, tuple(filters.values())).fetchall()
            for row in rows:
                item = {k: row[k] for k in row.keys() if k != "_rowid"}
                records.append(item)
                domain = str(item.get("target_type") or "unknown")
                by_domain[domain] = by_domain.get(domain, 0) + 1
                dec = str(item.get("decision") or "unknown")
                by_decision[dec] = by_decision.get(dec, 0) + 1
                tgt = str(item.get("target_id") or "unknown")
                by_target[tgt] = by_target.get(tgt, 0) + 1
                when = item.get("decided_at")
                if when:
                    if first_seen is None or str(when) < first_seen:
                        first_seen = str(when)
                    if last_seen is None or str(when) > last_seen:
                        last_seen = str(when)
        except sqlite3.Error as exc:
            errors.append(
                {"kind": "read_error", "source": "approvals.db", "detail": str(exc)}
            )
        finally:
            if con is not None:
                con.close()

    truncated = len(records) > limit
    page = records[:limit]
    return {
        "registry_version": REGISTRY_VERSION,
        "generated_at": __import__("datetime")
        .datetime.now()
        .isoformat(timespec="seconds"),
        "root": str(root),
        "read_only": True,
        "query": {"filters": filters, "limit": limit, "order": order},
        "source": {"path": str(db), "exists": db.exists()},
        "errors": errors,
        "total": len(records),
        "returned": len(page),
        "truncated": truncated,
        "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
        "by_decision": by_decision,
        "by_target": dict(sorted(by_target.items(), key=lambda kv: -kv[1])[:50]),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "records": page,
        "_fields": _CREDENTIAL_FIELDS,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 Approval 注册表（只读）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--target-type", default=None)
    ap.add_argument("--target-id", default=None)
    ap.add_argument("--decided-by", default=None)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--order", default="desc", choices=("asc", "desc"))
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)
    try:
        registry = build_registry(
            args.root,
            target_type=args.target_type,
            target_id=args.target_id,
            decided_by=args.decided_by,
            limit=args.limit,
            order=args.order,
        )
    except ValueError as exc:
        print(
            json.dumps(
                {"error": "invalid_query", "message": str(exc)}, ensure_ascii=False
            )
        )
        return 2
    rendered = json.dumps(registry, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
