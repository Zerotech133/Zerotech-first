# -*- coding: utf-8 -*-
"""M0 P4 事件回放验证（只读）。

跨三源核对，验证双轨接入后的链路一致性：

  1. Paper orders（k_stock/data/paper.db）——旧权威链
  2. Shadow ledger（agent_platform/data/shadow/shadow_ledger.jsonl）——旧权威链
  3. 统一幂等账本（agent_platform/data/idempotency/idempotency.db）——新账本

核对项：
  - 幂等账本 conflicts / dead_letters / 超时滞留 claimed（默认 >2h）；
  - Shadow done 行与账本 coverage（双轨起点后的行应有对应记录）；
  - Paper bridge pick-* ref 与账本 coverage；
  - 旧链完整性复检（重复 ref / 空 ref / 重复 done）。

只读：三源均以只读方式打开，缺失显式报告。绝不修改任何事实源。

用法:
  python tools/idem_replay_verify.py [--output reports/m0_idem_replay_report.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER_DB = ROOT / "k_stock" / "data" / "paper.db"
SHADOW_LEDGER = ROOT / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
IDEM_DB = ROOT / "agent_platform" / "data" / "idempotency" / "idempotency.db"
STALE_CLAIMED_HOURS = 2.0


def _load_paper(path: Path) -> dict:
    out = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        out["status"] = "missing_source"
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ref, account_id, status FROM orders"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        return out
    out["status"] = "ok"
    out["orders_total"] = len(rows)
    out["by_status"] = dict(Counter(r["status"] for r in rows))
    out["bridge_ref_rows"] = sum(
        1 for r in rows if (r["ref"] or "").startswith("pick-"))
    out["empty_ref_count"] = sum(1 for r in rows if not (r["ref"] or "").strip())
    ref_counter = Counter(r["ref"] for r in rows if (r["ref"] or "").strip())
    out["duplicate_ref_count"] = sum(1 for n in ref_counter.values() if n > 1)
    out["bridge_refs"] = sorted(
        r["ref"] for r in rows if (r["ref"] or "").startswith("pick-"))
    return out


def _load_shadow(path: Path) -> dict:
    out = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        out["status"] = "missing_source"
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        return out
    rows, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                rows.append(rec)
            else:
                bad += 1
        except json.JSONDecodeError:
            bad += 1
    done_pairs = Counter(
        (r.get("date"), r.get("symbol")) for r in rows if r.get("status") == "done")
    out["status"] = "ok"
    out["total"] = len(rows)
    out["bad_lines"] = bad
    out["by_status"] = dict(Counter(r.get("status", "") for r in rows))
    out["done_keys"] = sorted("%s|%s" % k for k, n in done_pairs.items() if n >= 1)
    out["duplicate_done_count"] = sum(1 for n in done_pairs.values() if n > 1)
    out["parse_fail_done"] = sum(
        1 for r in rows if r.get("status") == "done" and r.get("parse_ok") is False)
    return out


def _load_idem(path: Path) -> dict:
    out = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        out["status"] = "missing_source"
        out["note"] = "双轨账本尚未产生记录（首次接入后生成）"
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
        con.row_factory = sqlite3.Row
        records = con.execute("SELECT * FROM idempotency_records").fetchall()
        attempts = con.execute("SELECT * FROM idempotency_attempts").fetchall()
        conflicts = con.execute("SELECT * FROM idempotency_conflicts").fetchall()
        con.close()
    except sqlite3.Error as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        return out
    now = datetime.now(timezone.utc)
    stale_claimed = []
    dead_letters = 0
    completed_keys = set()
    for a in attempts:
        err = a["error"] or ""
        if err.startswith("dead-letter:"):
            dead_letters += 1
        if a["status"] == "claimed":
            try:
                age = (now - datetime.fromisoformat(a["updated_at"])).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            if age > STALE_CLAIMED_HOURS * 3600:
                stale_claimed.append(a["attempt_id"])
        if a["status"] == "completed":
            completed_keys.add(a["record_id"])
    rec_by_id = {r["record_id"]: r for r in records}
    completed_keys_of_scope = {}
    for rid in completed_keys:
        rec = rec_by_id.get(rid)
        if rec:
            completed_keys_of_scope.setdefault(rec["scope"], []).append(
                rec["idempotency_key"])
    out["status"] = "ok"
    out["records"] = len(records)
    out["attempts"] = len(attempts)
    out["conflicts"] = len(conflicts)
    out["dead_letters"] = dead_letters
    out["stale_claimed_over_%sh" % STALE_CLAIMED_HOURS] = stale_claimed
    out["completed_keys_by_scope"] = {
        k: sorted(v) for k, v in completed_keys_of_scope.items()}
    out["by_scope"] = dict(Counter(r["scope"] for r in records))
    return out


def build_report() -> dict:
    paper = _load_paper(PAPER_DB)
    shadow = _load_shadow(SHADOW_LEDGER)
    idem = _load_idem(IDEM_DB)

    findings = []
    # 旧链完整性
    if paper.get("duplicate_ref_count"):
        findings.append("paper 存在重复 ref（旧链完整性受损）")
    if shadow.get("duplicate_done_count"):
        findings.append("shadow ledger 存在重复 date+symbol done（并发风险实锤）")
    # 新账本健康
    if idem.get("conflicts"):
        findings.append("幂等账本存在 conflict 记录：%d 条" % idem["conflicts"])
    if idem.get("dead_letters"):
        findings.append("幂等账本存在死信：%d 条（死信=持有者崩溃，已自动重试）"
                        % idem["dead_letters"])
    if idem.get("stale_claimed_over_%sh" % STALE_CLAIMED_HOURS):
        findings.append("幂等账本存在超时滞留 claimed：%s"
                        % idem["stale_claimed_over_%sh" % STALE_CLAIMED_HOURS])
    # coverage：双轨起点之后才有意义，此处只报告数量供人工对照
    coverage = {
        "shadow_done_rows": len(shadow.get("done_keys", [])),
        "shadow_idem_completed": len(
            idem.get("completed_keys_by_scope", {}).get("shadow_daily", [])),
        "paper_bridge_ref_rows": paper.get("bridge_ref_rows", 0),
        "paper_bridge_idem_completed": len(
            idem.get("completed_keys_by_scope", {}).get("paper_bridge", [])),
        "note": "双轨接入起点 2026-10-17；此前历史行不要求有账本记录",
    }
    return {
        "schema_version": "1.0",
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {"paper": paper, "shadow": shadow, "idempotency": idem},
        "coverage": coverage,
        "findings": findings,
        "verdict": "PASS" if not findings else "ATTENTION",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M0 P4 幂等链路回放验证（只读）")
    ap.add_argument("--output", default=str(ROOT / "reports" / "m0_idem_replay_report.json"))
    args = ap.parse_args(argv)
    report = build_report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
