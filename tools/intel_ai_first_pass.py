# -*- coding: utf-8 -*-
"""N1 AI 初筛写入器（WO-INTEL-AI1ST-20261031）。

用途：把 50 条人工复核批次（`C:\\Temp\\intel_review_batch_<date>.jsonl`）按机器预审
建议映射为 AI 初筛裁决，**以 review_type='ai_first_pass' 独立审计档**写入
`news.db.jev_review_queue`；**不计入人工 50**、不冒充人工复核。

映射（与拍板 2/4 一致）：
    accept          → correct
    reject          → wrong
    needs_evidence  → uncertain
    unavailable/缺失 → uncertain（缺机器 JSONL 时全部走本条，明确降级）

安全纪律：
  - 默认 dry-run（只读 DB、不写文件）；`--apply` 才落盘；
  - `--apply` 前用 sqlite backup API 做一致性备份（默认 C:\\Temp），备份自检
    行数 + integrity_check；失败即中止；
  - 幂等迁移 `review_type TEXT NOT NULL DEFAULT ''`（旧 schema 兼容）；
  - 单事务：仅 `UPDATE ... WHERE status='pending'`，**不覆盖非 pending**；
    事务内校验 pending 减少数 == 写入数，不符 rollback；
  - 写后落 manifest（计数/映射/备份路径/id 指纹/回滚提示），**不跑 A/B、不重启、
    不触发 JEV 重判/回填**；人工复核仍走 portal/api_jev_review.resolve。

用法：
    python tools/intel_ai_first_pass.py --dry-run
    python tools/intel_ai_first_pass.py --json
    python tools/intel_ai_first_pass.py --apply            # 授权窗口执行
    python tools/intel_ai_first_pass.py --apply --backup-dir C:\\Temp
退出码：0=成功（含 no-op / 降级）；1=参数/数据错误；2=备份或写库失败。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "news_collector" / "news.db"
DEFAULT_BATCH = Path(r"C:\Temp\intel_review_batch_20261031.jsonl")
DEFAULT_MACHINE = Path(r"C:\Temp\intel_pre_review_20261031.jsonl")
DEFAULT_BACKUP_DIR = Path(r"C:\Temp")
DEFAULT_MANIFEST_DIR = Path(r"C:\Temp")

RULE_VERSION = "intel-ai-first-pass/v1"
PENDING_STATUS = "pending"
RESOLVED_STATUS = "resolved"
LEGACY_STATUS = "unreviewed_legacy"
REVIEW_TYPE_COLUMN = "review_type"
REVIEW_TYPE_HUMAN = "human"
REVIEW_TYPE_AI_FIRST_PASS = "ai_first_pass"
REVIEWER = "N1-AI"
MAX_BATCH = 50
DECISIONS = ("correct", "wrong", "uncertain")
MACHINE_MAP = {"accept": "correct", "reject": "wrong",
               "needs_evidence": "uncertain", "unavailable": "uncertain"}


def _connect_ro(db_path) -> sqlite3.Connection:
    p = Path(db_path).resolve()
    if not p.is_file():
        raise RuntimeError("news.db 不存在：%s" % p)
    con = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def has_review_type(con: sqlite3.Connection) -> bool:
    try:
        cols = [str(r[1]) for r in con.execute(
            "PRAGMA table_info(jev_review_queue)")]
    except sqlite3.Error:
        return False
    return REVIEW_TYPE_COLUMN in cols


def ensure_review_type_column(con: sqlite3.Connection) -> bool:
    """幂等补列（与 portal/api_jev_review.ensure_review_type_column 同 SQL）。"""
    if has_review_type(con):
        return False
    con.execute("ALTER TABLE jev_review_queue ADD COLUMN %s TEXT NOT NULL "
                "DEFAULT ''" % REVIEW_TYPE_COLUMN)
    con.commit()
    return True


def load_jsonl(path, id_key="id"):
    """→ (rows, meta)。缺文件/坏行 fail-visible（不抛，由调用方决定降级/报错）。"""
    p = Path(path)
    meta = {"path": str(p), "exists": p.is_file(), "lines": 0, "bad_lines": 0}
    rows = []
    if not p.is_file():
        return rows, meta
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        meta["exists"] = False
        return rows, meta
    for line in text.splitlines():
        if not line.strip():
            continue
        meta["lines"] += 1
        try:
            row = json.loads(line)
        except ValueError:
            meta["bad_lines"] += 1
            continue
        if not isinstance(row, dict) or not str(row.get(id_key) or "").strip():
            meta["bad_lines"] += 1
            continue
        rows.append(row)
    return rows, meta


def _decision_for(rec: dict, machine: dict) -> tuple[str, str, str]:
    """→ (decision, machine_decision, reason)。批次建议优先，machine JSONL 兜底。"""
    mid = str(rec.get("id"))
    batch_md = str(rec.get("machine_decision") or "").strip().lower()
    machine_md = str((machine.get(mid) or {}).get("machine_decision") or "").strip()
    used = batch_md if batch_md in MACHINE_MAP else (
        machine_md if machine_md in MACHINE_MAP else "unavailable")
    decision = MACHINE_MAP.get(used, "uncertain")
    if used == "unavailable":
        reason = "机器建议缺失（批次与 machine JSONL 均无）→ 按 uncertain 保守处理"
    else:
        reason = "machine_decision=%s → %s" % (used, decision)
    return decision, used, reason


def build_plan(db_path=None, batch_path=None, machine_path=None,
               limit=MAX_BATCH) -> dict:
    """只读计划：批次 ↔ DB 状态交集 + 映射（不写库/不写文件）。"""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("--limit 必须是正整数")
    limit_capped = limit > MAX_BATCH
    effective = min(limit, MAX_BATCH)
    db = Path(db_path or DEFAULT_DB)
    batch_rows, batch_meta = load_jsonl(batch_path or DEFAULT_BATCH)
    machine_rows, machine_meta = load_jsonl(machine_path or DEFAULT_MACHINE)
    if not batch_meta["exists"]:
        raise RuntimeError("批次 JSONL 不存在：%s" % batch_meta["path"])
    if not batch_rows:
        raise RuntimeError("批次 JSONL 无有效记录（bad_lines=%s）：%s"
                           % (batch_meta["bad_lines"], batch_meta["path"]))
    machine = {str(r.get("id")): r for r in machine_rows}

    seen = set()
    uniq = []
    duplicates = 0
    for rec in batch_rows:
        rid = str(rec.get("id")).strip()
        if rid in seen:
            duplicates += 1
            continue
        seen.add(rid)
        uniq.append(rec)
    records = uniq[:effective]

    con = _connect_ro(db)
    try:
        status_map = {}
        chunk = 400
        ids = [str(r["id"]) for r in records]
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            ph = ",".join("?" * len(part))
            for row in con.execute(
                    "SELECT id, status FROM jev_review_queue WHERE id IN (%s)"
                    % ph, part):
                status_map[str(row["id"])] = str(row["status"])
        total = con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        state = {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
        rt_present = has_review_type(con)
        resolved_ai = 0
        resolved_human = 0
        if rt_present:
            resolved_ai = con.execute(
                "SELECT COUNT(*) FROM jev_review_queue WHERE status=? AND %s=?"
                % REVIEW_TYPE_COLUMN,
                (RESOLVED_STATUS, REVIEW_TYPE_AI_FIRST_PASS)).fetchone()[0]
            resolved_human = con.execute(
                "SELECT COUNT(*) FROM jev_review_queue WHERE status=? AND "
                "COALESCE(%s,'')!=?" % REVIEW_TYPE_COLUMN,
                (RESOLVED_STATUS, REVIEW_TYPE_AI_FIRST_PASS)).fetchone()[0]
        else:
            resolved_human = state.get(RESOLVED_STATUS, 0)
    finally:
        con.close()

    items = []
    counts = {"batch_records": len(batch_rows), "unique_ids": len(uniq),
              "selected": len(records), "duplicates": duplicates,
              "writable": 0, "skipped_non_pending": 0,
              "decisions_all": {d: 0 for d in DECISIONS},
              "decisions_writable": {d: 0 for d in DECISIONS},
              "status_seen": {}}
    for rec in records:
        rid = str(rec["id"])
        decision, machine_md, reason = _decision_for(rec, machine)
        st = status_map.get(rid, "missing")
        counts["decisions_all"][decision] += 1
        counts["status_seen"][st] = counts["status_seen"].get(st, 0) + 1
        writable = st == PENDING_STATUS
        if writable:
            counts["writable"] += 1
            counts["decisions_writable"][decision] += 1
        else:
            counts["skipped_non_pending"] += 1
        items.append({
            "id": rid, "status_in_db": st, "writable": writable,
            "machine_decision": machine_md, "decision": decision,
            "reason": reason,
            "note": ("ai_first_pass:%s;machine=%s;batch=%s"
                     % (REVIEWER, machine_md,
                        str(rec.get("batch_id") or "")))[:500],
        })
    return {
        "ok": True, "rule_version": RULE_VERSION,
        "db": str(db.resolve()), "batch": batch_meta, "machine": machine_meta,
        "limit_requested": limit, "limit": effective,
        "limit_capped": limit_capped,
        "total_rows": total, "status_counts": state,
        "review_type_column": rt_present,
        "resolved_before": {"human": resolved_human,
                            "ai_first_pass": resolved_ai,
                            "total": state.get(RESOLVED_STATUS, 0)},
        "counts": counts, "items": items,
        "db_written": False,
        "notes": [
            "AI 初筛≠人工复核：review_type=ai_first_pass、reviewer=N1-AI；不计人工 50",
            "只写 status/decision/reviewer/reviewed_at/note/review_type；不跑 A/B",
        ],
    }


def _backup_db(db_path, backup_dir) -> Path:
    src = sqlite3.connect(str(Path(db_path).resolve()), timeout=30)
    bdir = Path(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / ("news.db.ai1st.bak-%s"
                   % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    if dest.exists():
        raise RuntimeError("备份文件已存在：%s" % dest)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    # 自检：可打开 + 行数一致 + integrity ok
    b = sqlite3.connect("file:" + dest.resolve().as_posix() + "?mode=ro", uri=True)
    try:
        n_backup = b.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        integrity = b.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        b.close()
    s = sqlite3.connect("file:" + Path(db_path).resolve().as_posix() + "?mode=ro",
                        uri=True)
    try:
        n_src = s.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
    finally:
        s.close()
    if n_backup != n_src or str(integrity).lower() != "ok":
        raise RuntimeError("备份自检失败：backup_rows=%s src_rows=%s integrity=%s"
                           % (n_backup, n_src, integrity))
    return dest


def apply_plan(plan: dict, db_path=None, backup_dir=None,
               manifest_path=None) -> dict:
    """执行：备份 → 幂等补列 → 单事务写 AI 初筛（仅 pending）→ manifest。"""
    db = Path(db_path or plan.get("db") or DEFAULT_DB)
    writable = [x for x in plan["items"] if x["writable"]]
    if not writable:
        return {"ok": True, "applied": False, "written": 0,
                "reason": "no_writable_pending", "counts": plan["counts"],
                "backup": None, "manifest": None}
    backup = _backup_db(db, backup_dir or DEFAULT_BACKUP_DIR)
    con = sqlite3.connect(str(db.resolve()), timeout=30)
    con.row_factory = sqlite3.Row
    added_col = False
    try:
        added_col = ensure_review_type_column(con)
        pending_before = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=?",
            (PENDING_STATUS,)).fetchone()[0]
        ai_before = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=? AND %s=?"
            % REVIEW_TYPE_COLUMN,
            (RESOLVED_STATUS, REVIEW_TYPE_AI_FIRST_PASS)).fetchone()[0]
        con.execute("BEGIN IMMEDIATE")
        written = 0
        applied_ids = []
        for it in writable:
            cur = con.execute(
                "UPDATE jev_review_queue SET status=?, decision=?, reviewer=?, "
                "reviewed_at=datetime('now','localtime'), note=?, %s=? "
                "WHERE id=? AND status=?" % REVIEW_TYPE_COLUMN,
                (RESOLVED_STATUS, it["decision"], REVIEWER, it["note"],
                 REVIEW_TYPE_AI_FIRST_PASS, it["id"], PENDING_STATUS))
            if cur.rowcount:
                written += 1
                applied_ids.append(it["id"])
        pending_after = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=?",
            (PENDING_STATUS,)).fetchone()[0]
        ai_after = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=? AND %s=?"
            % REVIEW_TYPE_COLUMN,
            (RESOLVED_STATUS, REVIEW_TYPE_AI_FIRST_PASS)).fetchone()[0]
        expected = len(writable)
        if (written != expected or pending_before - pending_after != written
                or ai_after - ai_before != written):
            con.rollback()
            return {"ok": False, "error": "verify_mismatch", "backup": str(backup),
                    "written": written, "expected": expected,
                    "pending_before": pending_before,
                    "pending_after": pending_after,
                    "ai_before": ai_before, "ai_after": ai_after}
        con.commit()
    except Exception:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
    # 写后状态 + manifest
    con = sqlite3.connect("file:" + db.resolve().as_posix() + "?mode=ro", uri=True)
    try:
        post = {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
        post_ai = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=? AND %s=?"
            % REVIEW_TYPE_COLUMN,
            (RESOLVED_STATUS, REVIEW_TYPE_AI_FIRST_PASS)).fetchone()[0]
        post_human = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=? AND "
            "COALESCE(%s,'')!=?" % REVIEW_TYPE_COLUMN,
            (RESOLVED_STATUS, REVIEW_TYPE_AI_FIRST_PASS)).fetchone()[0]
    finally:
        con.close()
    manifest = {
        "schema": "intel-ai-first-pass-manifest/v1",
        "rule_version": plan.get("rule_version"),
        "applied_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "db": str(db.resolve()), "backup": str(backup),
        "batch": plan.get("batch"), "machine": plan.get("machine"),
        "mapping": MACHINE_MAP, "reviewer": REVIEWER,
        "review_type": REVIEW_TYPE_AI_FIRST_PASS,
        "review_type_column_was_present": bool(plan.get("review_type_column")),
        "review_type_column_added": bool(added_col),
        "counts": {**plan.get("counts", {}), "written": written,
                   "pending_before": pending_before,
                   "pending_after": pending_after},
        "applied_ids_count": len(applied_ids),
        "applied_ids_sha256": hashlib.sha256(
            "\n".join(applied_ids).encode("utf-8")).hexdigest(),
        "post_state": {"status_counts": post,
                       "resolved_human": post_human,
                       "resolved_ai_first_pass": post_ai},
        "notes": [
            "AI 初筛（review_type=ai_first_pass）≠ 人工复核；人工 50 只数 human",
            "只适用于提前跑 A/B/审计；不可当作人工裁决；不跑 A/B",
            "回滚：停写后还原 backup（news.db.ai1st.bak-*）；本工具未删改原文",
        ],
    }
    mp = Path(manifest_path) if manifest_path else (
        DEFAULT_MANIFEST_DIR / ("intel_ai_first_pass_%s.manifest.json"
                                % dt.datetime.now().strftime("%Y%m%d")))
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    return {"ok": True, "applied": True, "written": written,
            "backup": str(backup), "manifest": str(mp),
            "pending_after": pending_after,
            "resolved_ai_first_pass": post_ai,
            "resolved_human": post_human, "post_state": post}


def _fmt(plan: dict, applied=None) -> str:
    c = plan["counts"]
    lines = [
        "[%s] %s" % ("APPLY" if applied else "DRY-RUN", plan["rule_version"]),
        "db=%s（review_type 列=%s）" % (
            plan["db"], "已有" if plan["review_type_column"] else "待迁移"),
        "批次：%s（lines=%s bad=%s）" % (
            plan["batch"]["path"], plan["batch"]["lines"],
            plan["batch"]["bad_lines"]),
        "机器：%s（%s）" % (
            plan["machine"]["path"],
            "可用" if plan["machine"]["exists"] else "缺失→uncertain 降级"),
        "记录=%s 去重后=%s 可写=%s 跳过非pending=%s 重复=%s" % (
            c["batch_records"], c["unique_ids"], c["writable"],
            c["skipped_non_pending"], c["duplicates"]),
        "映射（可写）：correct=%s wrong=%s uncertain=%s" % (
            c["decisions_writable"]["correct"],
            c["decisions_writable"]["wrong"],
            c["decisions_writable"]["uncertain"]),
        "库现状：pending=%s resolved(human=%s ai=%s) legacy=%s" % (
            plan["status_counts"].get("pending"), plan["resolved_before"]["human"],
            plan["resolved_before"]["ai_first_pass"],
            plan["status_counts"].get(LEGACY_STATUS)),
    ]
    if applied:
        lines.append("apply：written=%s pending_after=%s resolved_ai=%s" % (
            applied.get("written"), applied.get("pending_after"),
            applied.get("resolved_ai_first_pass")))
        lines.append("备份：%s" % applied.get("backup"))
        lines.append("manifest：%s" % applied.get("manifest"))
    for it in plan["items"][:5]:
        lines.append("  %-14s %s status=%s machine=%s" % (
            it["decision"], it["id"], it["status_in_db"],
            it["machine_decision"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        description="N1 AI 初筛写入器：批次+机器建议 → review_type=ai_first_pass（默认 dry-run）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="news.db 路径")
    ap.add_argument("--batch", default=str(DEFAULT_BATCH), help="批次 JSONL")
    ap.add_argument("--machine", default=str(DEFAULT_MACHINE), help="机器预审 JSONL")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    ap.add_argument("--manifest", default=None, help="manifest 输出路径")
    ap.add_argument("--limit", type=int, default=MAX_BATCH,
                    help="本批最多写入条数（默认/上限 50）")
    ap.add_argument("--dry-run", action="store_true", help="只预览（默认）")
    ap.add_argument("--apply", action="store_true", help="执行写入（先备份）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.dry_run and args.apply:
        print(json.dumps({"ok": False, "error": "--apply 与 --dry-run 互斥"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    if args.limit < 1:
        print(json.dumps({"ok": False, "error": "--limit 必须是正整数"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    try:
        plan = build_plan(db_path=args.db, batch_path=args.batch,
                          machine_path=args.machine, limit=args.limit)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (
            type(exc).__name__, exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if not args.apply:
        plan["mode"] = "dry_run"
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
        else:
            print(_fmt(plan))
        return 0
    try:
        applied = apply_plan(plan, db_path=args.db, backup_dir=args.backup_dir,
                             manifest_path=args.manifest)
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (
            type(exc).__name__, exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": applied.get("ok"), "mode": "apply",
                          "plan": plan, "applied": applied},
                         ensure_ascii=False, indent=1))
    else:
        print(_fmt(plan, applied))
    return 0 if applied.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
