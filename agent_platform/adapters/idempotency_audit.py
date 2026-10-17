# -*- coding: utf-8 -*-
"""M0 P3 幂等基线只读审计器。

按 docs/audit/2026-10-17_统一幂等语义盘点.md 实施顺序第 1 步：

- 扫描 Paper 旧库 orders 表：总订单、空 ref、重复 ref、跨账户同 ref 冲突；
- 扫描 agent_platform/data/shadow/shadow_ledger.jsonl：状态分布、
  parse_ok=false 的 done、date+symbol 重复 done；
- 只读：SQLite mode=ro；缺失数据源显式报告 missing_source；
- 输出 JSON 报告，绝不修改任何事实源。

用法:
  python agent_platform/adapters/idempotency_audit.py \
      --root <项目根> [--paper-db <paper.db>] --output reports/m0_idempotency_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = "1.0"
READ_ONLY = True


def _audit_paper_db(db_path: Path) -> dict:
    out = {
        "source": "paper_db",
        "path": str(db_path),
        "exists": db_path.exists(),
        "read_only": True,
    }
    if not db_path.exists():
        out["status"] = "missing_source"
        out["orders_total"] = 0
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT ref, account_id FROM orders"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        out["orders_total"] = 0
        return out

    total = len(rows)
    empty_refs = [r["account_id"] or "" for r in rows if not (r["ref"] or "").strip()]
    ref_counter = Counter(r["ref"] for r in rows if (r["ref"] or "").strip())
    dup_refs = {ref: n for ref, n in ref_counter.items() if n > 1}
    # 跨账户同 ref：同一 ref 出现且涉及多个 account_id
    ref_accounts = {}
    for r in rows:
        ref = (r["ref"] or "").strip()
        if not ref:
            continue
        ref_accounts.setdefault(ref, set()).add(r["account_id"] or "")
    cross_account = {
        ref: sorted(accs) for ref, accs in ref_accounts.items() if len(accs) > 1
    }
    out.update(
        {
            "status": "ok",
            "orders_total": total,
            "empty_ref_count": len(empty_refs),
            "duplicate_ref_count": len(dup_refs),
            "duplicate_refs_sample": dict(sorted(dup_refs.items())[:20]),
            "cross_account_ref_count": len(cross_account),
            "cross_account_refs_sample": dict(sorted(cross_account.items())[:20]),
        }
    )
    return out


def _audit_shadow_ledger(ledger_path: Path) -> dict:
    out = {
        "source": "shadow_ledger",
        "path": str(ledger_path),
        "exists": ledger_path.exists(),
        "read_only": True,
    }
    if not ledger_path.exists():
        out["status"] = "missing_source"
        out["total"] = 0
        return out
    status_counter = Counter()
    done_pairs = Counter()
    dup_done_pairs = {}
    parse_fail_done = 0
    bad_lines = 0
    total = 0
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        out["total"] = 0
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if not isinstance(rec, dict):
            bad_lines += 1
            continue
        total += 1
        st = str(rec.get("status", ""))
        status_counter[st] += 1
        pair = (str(rec.get("date", "")), str(rec.get("symbol", "")))
        if st == "done":
            done_pairs[pair] += 1
            if rec.get("parse_ok") is False:
                parse_fail_done += 1
    for pair, n in done_pairs.items():
        if n > 1:
            dup_done_pairs["%s|%s" % pair] = n
    out.update(
        {
            "status": "ok",
            "total": total,
            "by_status": dict(status_counter),
            "bad_lines": bad_lines,
            "parse_fail_done_count": parse_fail_done,
            "duplicate_done_pair_count": len(dup_done_pairs),
            "duplicate_done_pairs_sample": dict(sorted(dup_done_pairs.items())[:20]),
        }
    )
    return out


def build_report(root: Path, paper_db: Path | None = None) -> dict:
    root = Path(root)
    paper_path = paper_db or (root / "k_stock" / "data" / "paper.db")
    ledger_path = root / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
    paper = _audit_paper_db(paper_path)
    shadow = _audit_shadow_ledger(ledger_path)
    blockers = []
    if paper["status"] == "ok" and paper.get("duplicate_ref_count"):
        blockers.append("paper_db has duplicate refs (must migrate via mapping, not reuse)")
    if paper["status"] == "ok" and paper.get("empty_ref_count"):
        blockers.append(
            "paper_db has empty-ref orders (manual orders; cannot be used as idempotency key)"
        )
    if shadow["status"] == "ok" and shadow.get("duplicate_done_pair_count"):
        blockers.append(
            "shadow ledger has duplicate date+symbol done rows (concurrency risk confirmed)"
        )
    if shadow["status"] == "ok" and shadow.get("parse_fail_done_count"):
        blockers.append(
            "shadow ledger has done rows with parse_ok=false (product decision required)"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": READ_ONLY,
        "root": str(root),
        "sources": {"paper_db": paper, "shadow_ledger": shadow},
        "blockers": blockers,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M0 幂等基线只读审计")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--paper-db", default="", help="Paper orders DB 路径（可选）")
    parser.add_argument("--output", default="", help="报告输出路径（可选，默认仅打印）")
    args = parser.parse_args(argv)

    report = build_report(
        Path(args.root), Path(args.paper_db) if args.paper_db else None
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
