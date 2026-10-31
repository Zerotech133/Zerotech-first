# -*- coding: utf-8 -*-
"""人工复核批次校验/回填器（WO-INTEL-HUMANAPPLY-20261031）。

用途：把老田填好的 50 条人工复核 CSV 写回 `news.db.jev_review_queue`，
**review_type='human'**；默认 dry-run，只有 `--apply` 才写。

纪律：
  · 只用 CSV 的 decision/reviewer/note；不接受 N1-AI / AI 代签；
  · 只更新 status='pending'，不覆盖已 resolved/legacy；
  · --apply 前 sqlite 一致性备份；单事务；写后校验 pending 减少数==写入数；
  · 不跑 A/B、不重启、不触发 JEV 重判/回填；
  · 缺列/重复 id/非法 decision/非 pending 一律 fail-visible。

CSV 列：id, decision, reviewer, note（#/source/title 等可忽略）
用法：
    python tools/intel_human_batch_apply.py --csv <filled.csv>
    python tools/intel_human_batch_apply.py --csv <filled.csv> --apply
    python tools/intel_human_batch_apply.py --csv <filled.csv> --json
退出码：0=校验通过/成功；1=数据错误；2=备份或写库失败。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "news_collector" / "news.db"
DEFAULT_BACKUP_DIR = Path(r"C:\Temp")
DEFAULT_MANIFEST = Path(r"C:\Temp\intel_human_batch_apply_manifest.json")
REVIEW_TYPE = "human"
AI_REVIEWER = "N1-AI"
DECISIONS = ("correct", "wrong", "uncertain")


def _connect(db, ro=False):
    p = Path(db).resolve()
    if not p.is_file():
        raise RuntimeError("news.db 不存在：%s" % p)
    if ro:
        con = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True, timeout=20)
        con.execute("PRAGMA query_only=ON")
    else:
        con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _has_review_type(con):
    return "review_type" in {str(r[1]) for r in con.execute(
        "PRAGMA table_info(jev_review_queue)")}


def _ensure_review_type(con):
    if not _has_review_type(con):
        con.execute("ALTER TABLE jev_review_queue ADD COLUMN review_type TEXT "
                    "NOT NULL DEFAULT ''")
        con.commit()


def load_csv(path):
    p = Path(path)
    if not p.is_file():
        raise RuntimeError("CSV 不存在：%s" % p)
    with p.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    required = ("id", "decision", "reviewer")
    if not rows:
        raise ValueError("CSV 为空")
    missing = [c for c in required if c not in (rows[0].keys() if rows else [])]
    if missing:
        raise ValueError("CSV 缺列：%s" % missing)
    return rows


def validate(rows, db=None):
    """→ (items, errors)。只读；不写库。"""
    errors = []
    seen = {}
    items = []
    con = _connect(db or DEFAULT_DB, ro=True)
    try:
        for i, row in enumerate(rows, 2):  # 第 2 行起（表头 1）
            nid = str(row.get("id") or "").strip()
            decision = str(row.get("decision") or "").strip().lower()
            reviewer = str(row.get("reviewer") or "").strip()
            note = str(row.get("note") or "").strip()
            if not nid:
                errors.append({"row": i, "error": "id_empty"})
                continue
            if nid in seen:
                errors.append({"row": i, "id": nid, "error": "duplicate_id"})
                continue
            seen[nid] = i
            if decision not in DECISIONS:
                errors.append({"row": i, "id": nid, "error": "bad_decision",
                               "got": decision})
                continue
            if not reviewer:
                errors.append({"row": i, "id": nid, "error": "reviewer_empty"})
                continue
            if reviewer == AI_REVIEWER or "AI" in reviewer.upper():
                errors.append({"row": i, "id": nid, "error": "ai_reviewer_forbidden"})
                continue
            r = con.execute(
                "SELECT id,status FROM jev_review_queue WHERE id=?", (nid,)
            ).fetchone()
            if not r:
                errors.append({"row": i, "id": nid, "error": "not_found"})
                continue
            if r["status"] != "pending":
                errors.append({"row": i, "id": nid, "error": "not_pending",
                               "status": r["status"]})
                continue
            items.append({"id": nid, "decision": decision,
                          "reviewer": reviewer, "note": note})
    finally:
        con.close()
    return items, errors


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_db(db, backup_dir):
    src = Path(db).resolve()
    bdir = Path(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = bdir / ("news.db.human.bak-%s" % stamp)
    s = sqlite3.connect(str(src), timeout=30)
    d = sqlite3.connect(str(dst), timeout=30)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    check = sqlite3.connect("file:" + dst.as_posix() + "?mode=ro", uri=True)
    try:
        n = check.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
    finally:
        check.close()
    return str(dst), int(n)


def apply_items(items, db=None, backup_dir=None, manifest=None, dry_run=True):
    if not items:
        raise ValueError("没有可写入的人工裁决")
    dbf = Path(db or DEFAULT_DB).resolve()
    if dry_run:
        return {"ok": True, "dry_run": True, "would_write": len(items),
                "review_type": REVIEW_TYPE}
    backup, backup_rows = backup_db(dbf, backup_dir or DEFAULT_BACKUP_DIR)
    payload = {
        "kind": "intel_human_batch_apply/v1",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "db": str(dbf), "backup": backup, "backup_rows": backup_rows,
        "review_type": REVIEW_TYPE, "written": 0, "ids": [],
        "mismatch": [],
    }
    con = sqlite3.connect(str(dbf), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        _ensure_review_type(con)
        before = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status='pending'"
        ).fetchone()[0]
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        con.execute("BEGIN IMMEDIATE")
        written = 0
        for it in items:
            cur = con.execute(
                "UPDATE jev_review_queue SET status='resolved', decision=?, "
                "reviewer=?, reviewed_at=?, note=?, review_type=? "
                "WHERE id=? AND status='pending'",
                (it["decision"], it["reviewer"], now, it.get("note") or "",
                 REVIEW_TYPE, it["id"]))
            if cur.rowcount == 1:
                written += 1
                payload["ids"].append({"id": it["id"],
                                       "decision": it["decision"],
                                       "reviewer": it["reviewer"]})
            else:
                payload["mismatch"].append(it["id"])
        after = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status='pending'"
        ).fetchone()[0]
        if before - after != written:
            con.rollback()
            raise RuntimeError("pending 减少数不符：before=%s after=%s written=%s"
                               % (before, after, written))
        con.commit()
        payload["written"] = written
        payload["pending_before"] = before
        payload["pending_after"] = after
    except Exception:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
    payload["backup_sha256"] = _sha256(backup)
    mf = Path(manifest or DEFAULT_MANIFEST)
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    payload["manifest"] = str(mf)
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="人工 50 条复核 CSV 校验/回填")
    ap.add_argument("--csv", required=True, help="填好 decision 的 CSV")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="真正写库（默认 dry-run）")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        rows = load_csv(args.csv)
        items, errors = validate(rows, args.db)
        if errors:
            if args.json:
                print(json.dumps({"ok": False, "errors": errors,
                                  "valid": len(items)}, ensure_ascii=False, indent=2))
            else:
                print("[ERR] 校验失败 %d 项：" % len(errors))
                for e in errors[:20]:
                    print("  - %s" % e)
            return 1
        out = apply_items(items, args.db, args.backup_dir, args.manifest,
                          dry_run=not args.apply)
        out["valid"] = len(items)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print("校验通过：%d 条人工裁决" % len(items))
            if not args.apply:
                print("[dry-run] 未写库；加 --apply 才写入 review_type=human")
            else:
                print("written=%s pending=%s→%s backup=%s"
                      % (out.get("written"), out.get("pending_before"),
                         out.get("pending_after"), out.get("backup")))
        return 0
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        print("[ERR] %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
