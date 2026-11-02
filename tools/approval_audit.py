#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审批抽样审计 v1（只读）：对 approvals.db 已决记录做确定性抽样。

用法：
  python -X utf8 tools/approval_audit.py [--n 10] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import hq_approvals  # noqa: E402


def build_audit(n: int = 10) -> dict:
    a = hq_approvals.build_approvals()
    records = list(a.get("latest") or [])
    # latest 只有 10 条；不足时直接全量，抽审以确定性 hash 排序。
    all_records = []
    try:
        # 通过 approvals_registry 取全量（hq_approvals 内部也调用它，这里不重复写 SQL）
        sys.path.insert(0, str(ROOT / "agent_platform"))
        from adapters.approvals_registry import build_registry  # noqa: E402
        reg = build_registry(ROOT, limit=1000)
        all_records = reg.get("records") or []
    except Exception:  # noqa: BLE001
        all_records = records
    ranked = sorted(all_records, key=lambda r: hashlib.sha256(
        str(r.get("approval_id") or r.get("decided_at") or "").encode()).hexdigest())
    sample = ranked[:max(1, min(n, len(ranked)))] if ranked else []
    summary = a.get("summary") or {}
    findings = []
    if summary.get("by_decision") == {"approved": summary.get("total_decisions")}:
        findings.append("100% approved / 无 rejected：审批可能过宽或只记录通过项")
    if summary.get("pending", 0) == 0:
        findings.append("无 pending 记录（飞书待办未同步；不可据此判断无待办）")
    by_decider = {}
    for r in sample:
        k = str(r.get("decided_by") or "?")
        by_decider[k] = by_decider.get(k, 0) + 1
    return {
        "schema": "approval/audit-sample/v1",
        "read_only": True,
        "total": summary.get("total_decisions", len(ranked)),
        "sample_n": len(sample),
        "by_decision": summary.get("by_decision"),
        "sample_by_decider": by_decider,
        "sample": [
            {"approval_id": r.get("approval_id"), "target_type": r.get("target_type"),
             "target_id": r.get("target_id"), "decision": r.get("decision"),
             "decided_by": r.get("decided_by"), "decided_at": r.get("decided_at")}
            for r in sample
        ],
        "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="审批抽样审计")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    a = build_audit(args.n)
    print(json.dumps(a, ensure_ascii=False, indent=1) if args.json else
          ("# 审批抽样审计\n\n" + f"- 总数 {a['total']} / 抽样 {a['sample_n']}\n" +
           "\n".join(f"- {x}" for x in a["findings"]) + "\n\n" +
           "\n".join(f"| {r['decided_at']} | {r['target_type']} | {r['target_id']} | {r['decision']} | {r['decided_by']} |"
                     for r in a["sample"])), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
