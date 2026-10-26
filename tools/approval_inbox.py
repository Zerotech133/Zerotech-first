# -*- coding: utf-8 -*-
"""approval-inbox：L2 审批队列（本地 JSON，append 决策留痕）。

用法：
  python -X utf8 tools/approval_inbox.py request --kind deploy --reason "..." --payload "{}"
  python -X utf8 tools/approval_inbox.py list [--status pending] [--json]
  python -X utf8 tools/approval_inbox.py approve <id> --by 老田 [--note "..."]
  python -X utf8 tools/approval_inbox.py reject <id> --by 老田 --note "原因"
数据：work/approvals/approvals.json（不入 git）；只有 pending 可决策。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STORE = REPO / "work" / "approvals" / "approvals.json"


def _load(path: Path | None = None) -> list:
    p = Path(path) if path else STORE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(rows: list, path: Path | None = None) -> None:
    p = Path(path) if path else STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def request(kind: str, reason: str, payload=None, requested_by: str = "田丰",
            path: Path | None = None) -> dict:
    kind = str(kind or "").strip()
    if not kind or not str(reason or "").strip():
        raise ValueError("kind/reason 必填")
    rows = _load(path)
    item = {"schema": "approval_inbox/v1", "id": "apr-" + uuid.uuid4().hex[:8],
            "kind": kind, "reason": str(reason)[:500],
            "payload": payload if isinstance(payload, (dict, list)) else {"raw": str(payload)[:2000]},
            "requested_by": requested_by, "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": "pending", "decided_by": None, "decided_at": None, "note": ""}
    rows.append(item)
    _save(rows, path)
    return item


def decide(apr_id: str, approved: bool, by: str = "老田", note: str = "",
           path: Path | None = None) -> dict:
    rows = _load(path)
    for r in rows:
        if r.get("id") == apr_id:
            if r.get("status") != "pending":
                raise ValueError("该审批已决策: %s" % r.get("status"))
            r["status"] = "approved" if approved else "rejected"
            r["decided_by"] = str(by or "老田")
            r["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            r["note"] = str(note or "")[:500]
            _save(rows, path)
            return r
    raise ValueError("approval not found: %s" % apr_id)


def list_rows(status: str = "", path: Path | None = None) -> list:
    rows = _load(path)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 approval-inbox（L2 审批队列）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("request")
    r.add_argument("--kind", required=True)
    r.add_argument("--reason", required=True)
    r.add_argument("--payload", default="{}")
    r.add_argument("--by", default="田丰")
    r.add_argument("--file", default="")
    l = sub.add_parser("list")
    l.add_argument("--status", default="")
    l.add_argument("--json", action="store_true")
    l.add_argument("--file", default="")
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("id")
        d.add_argument("--by", default="老田")
        d.add_argument("--note", default="")
        d.add_argument("--file", default="")
    args = ap.parse_args(argv)
    path = Path(args.file) if getattr(args, "file", "") else None
    try:
        if args.cmd == "request":
            try:
                payload = json.loads(args.payload)
            except ValueError:
                payload = {"raw": args.payload}
            item = request(args.kind, args.reason, payload, args.by, path)
            print(json.dumps(item, ensure_ascii=False, indent=1))
        elif args.cmd == "list":
            rows = list_rows(args.status, path)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            else:
                for x in rows:
                    print("%s [%s] %s | %s | by=%s" % (x["id"], x["status"], x["kind"],
                                                       x["reason"][:40], x["requested_by"]))
        else:
            item = decide(args.id, args.cmd == "approve", args.by, args.note, path)
            print(json.dumps(item, ensure_ascii=False, indent=1))
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
