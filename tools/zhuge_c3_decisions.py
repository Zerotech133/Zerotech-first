# -*- coding: utf-8 -*-
"""zhuge_c3_decisions.py — C3 采纳信号 ↔ P1-4 决策记录。

C3 账本（portal/data/zhuge_adoption.jsonl）每行是页面 beacon：
{at, domain, ua, received_at}。它只有「建议域」没有决策 ID，因此：
- 决策记录可声明 `adoption_domains=[...]`（本单新增字段）；
- 同步时按 domain 聚合采纳次数，回填到声明了该 domain 的决策记录：
  `adoption_id="C3:<domain>"`、`outcome="C3 采纳信号 N 条（最近 <at>）"`；
- 未匹配的 domain 只报告（不猜测、不强行关联）。

只读 C3 账本、只写 P1-4 账本；不改 C3；不派单/不执行。

用法：
  python tools/zhuge_c3_decisions.py --report
  python tools/zhuge_c3_decisions.py --sync [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADOPTION_REL = Path("portal") / "data" / "zhuge_adoption.jsonl"


def _root() -> Path:
    override = os.environ.get("ZHUGE_C3DEC_REPO")
    return Path(override).resolve() if override else REPO_ROOT


def _load_adoptions(path: Path | None = None) -> list[dict]:
    path = Path(path) if path else (_root() / ADOPTION_REL)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("domain"):
            rows.append(row)
    return rows


def aggregate(adoptions: list[dict]) -> dict:
    result = {}
    for row in adoptions:
        domain = str(row.get("domain") or "")[:80]
        if not domain:
            continue
        item = result.setdefault(domain, {"count": 0, "last_at": ""})
        item["count"] += 1
        at = str(row.get("at") or row.get("received_at") or "")
        if at > item["last_at"]:
            item["last_at"] = at
    return result


def _dr():
    tools = str(Path(__file__).resolve().parent)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import zhuge_decision_record as dr  # noqa: E402
    return dr


def sync_adoptions(adoption_path: Path | None = None, dry_run: bool = False) -> dict:
    dr = _dr()
    adoptions = _load_adoptions(adoption_path)
    by_domain = aggregate(adoptions)
    records = dr._fold(dr._load_events())
    linked = []
    unmatched = sorted(by_domain)
    for rid, rec in records.items():
        domains = rec.get("adoption_domains") or []
        if not domains:
            continue
        hits = [d for d in domains if d in by_domain]
        if not hits:
            continue
        domain = max(hits, key=lambda d: by_domain[d]["count"])
        stat = by_domain[domain]
        unmatched = [d for d in unmatched if d != domain]
        outcome = "C3 采纳信号 %d 条（最近 %s）" % (stat["count"], stat["last_at"] or "unknown")
        adoption_id = "C3:%s" % domain
        if dry_run:
            linked.append({"id": rid, "domain": domain, "count": stat["count"],
                           "action": "would_update"})
            continue
        res = dr.update(rid, outcome=outcome, adoption_id=adoption_id)
        if res.get("ok"):
            linked.append({"id": rid, "domain": domain, "count": stat["count"],
                           "action": "updated"})
        else:
            linked.append({"id": rid, "domain": domain, "count": stat["count"],
                           "action": "skipped", "error": res.get("error")})
    return {
        "ok": True,
        "adoption_events": len(adoptions),
        "domains": len(by_domain),
        "linked": linked,
        "unmatched_domains": unmatched[:50],
        "unmatched_count": len(unmatched),
        "dry_run": dry_run,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C3 adoption ↔ decision record bridge")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--adoption-ledger", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.adoption_ledger) if args.adoption_ledger else None
    if args.sync:
        out = sync_adoptions(path, dry_run=args.dry_run)
    else:
        adoptions = _load_adoptions(path)
        by_domain = aggregate(adoptions)
        out = {"ok": True, "adoption_events": len(adoptions),
               "domains": len(by_domain),
               "top_domains": sorted(
                   [{"domain": d, **v} for d, v in by_domain.items()],
                   key=lambda x: -x["count"])[:50]}
    print(json.dumps(out, ensure_ascii=False, indent=2) if not args.json
          else json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
