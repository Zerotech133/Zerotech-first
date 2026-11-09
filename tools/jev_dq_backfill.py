# -*- coding: utf-8 -*-
"""HIT-DQ-20261107 / V1' 公告结构化前缀回填执行器。

把 39_ann_eligible_seqs.json 中「严格可写且上游 codes 为空」的 5,497 行，
把 news.db::news.stocks 从空值补为公告标题前缀解析出的 TS 码 JSON 数组，
供次日 07:10 的 tools/jev_audit.py::load_judged / upsert_judged 自然派生
dec_audit.companies + 后验 hit。

设计约束（HIT-DQ §3 / §8.3）：
  * 默认 --dry-run，只读打开（mode=ro + PRAGMA query_only=1），绝不写库。
  * --apply 必须同时给 --confirm，且必须已存在 --backup-only 生成的一致性快照。
  * 单事务 BEGIN IMMEDIATE，只 UPDATE news.stocks 一列，不 INSERT/DELETE/DDL。
  * 逐行 receipt 记录 before/after/source，可在 commit 前落 tmp、commit 后原子改名。
  * --rollback 按 receipt 按 id 反向还原，跳过已被后续写入改动过的行。
  * 幂等：receipt 已存在则拒绝重复 apply；重跑需先 rollback。
  * source=prefix_parse：标明这是结构化前缀解析补写，不是模型判读输出。

禁止：jev_audit.py --days 45、改 news.jev、直接改 dec_audit、refresh_curated.py --replace。
"""
from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NEWS = ROOT / "news_collector" / "news.db"
DEFAULT_MANIFEST = ROOT / "reports" / "_tmp_intel_impl_20261107" / "39_ann_eligible_seqs.json"
DEFAULT_ROWS = ROOT / "reports" / "_tmp_intel_impl_20261107" / "38_ann_manifest_rows.json"
DEFAULT_BACKUP = ROOT / "reports" / "backups" / "hit_dq_20261107"
BACKUP_NAME = "news.db.pre_dq.bak"
RAW_NAME = "news.db.pre_dq.raw"
RECEIPT_NAME = "receipt.json"
SOURCE_TAG = "prefix_parse"
EXPECTED_WRITE_SET = 5497

EMPTY_VALUES = {None, "", "[]", "[ ]", "[\n]"}


def jload(text, fallback):
    try:
        return json.loads(text or "")
    except (ValueError, TypeError):
        return fallback


def is_empty_stocks(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        if value in EMPTY_VALUES:
            return True
        try:
            return not bool(json.loads(value))
        except ValueError:
            return False
    return not bool(value)


def valid_target(text: str) -> bool:
    """目标必须是单元素 JSON 数组，元素为 6 位码（可带 .SH/.SZ/.BJ 后缀）。"""
    try:
        arr = json.loads(text)
    except ValueError:
        return False
    if not isinstance(arr, list) or len(arr) != 1 or not isinstance(arr[0], str):
        return False
    code = arr[0].split(".", 1)[0]
    return code.isdigit() and len(code) == 6


def ro_conn(path: Path, timeout: float = 30.0) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + Path(path).as_posix() + "?mode=ro",
                          uri=True, timeout=timeout)
    con.execute("PRAGMA query_only=1")
    return con


def load_plan(manifest_path: Path, rows_path: Path,
              expected: int = EXPECTED_WRITE_SET,
              expected_excluded: int | None = 17) -> tuple[dict, list[dict]]:
    el = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    rows = el["rows"]
    seqs = [r["seq"] for r in rows]
    digest = hashlib.sha256(
        json.dumps(seqs, separators=(",", ":")).encode("ascii")).hexdigest()
    if digest != el["sha256"]:
        raise RuntimeError("manifest sha256 mismatch: %s != %s" % (digest, el["sha256"]))
    if el["count"] != len(rows):
        raise RuntimeError("manifest count mismatch: %s != %s" % (el["count"], len(rows)))
    for r in rows:
        if not valid_target(r["companies_new"]):
            raise RuntimeError("invalid target for seq=%s: %r" % (r["seq"], r["companies_new"]))

    classified = json.loads(Path(rows_path).read_text(encoding="utf-8"))
    # 38_ 是全量 6,016 行分类，manifest 只含 5,514 严格子集 → 必须取交集，
    # 否则会把子集外的 has_codes 行也算成「越界」而误判。
    has_codes = {r["seq"] for r in classified if r["upstream_has_codes"]}
    eligible = {r["seq"] for r in rows}
    excluded = has_codes & eligible
    plan = [{"seq": r["seq"], "news_id": r["news_id"], "target": r["companies_new"]}
            for r in rows if r["seq"] not in excluded]
    if len(plan) != expected:
        raise RuntimeError("write set size %d != %d" % (len(plan), expected))
    if expected_excluded is not None and len(excluded) != expected_excluded:
        raise RuntimeError("excluded upstream_has_codes %d != %d"
                           % (len(excluded), expected_excluded))
    if not eligible <= {r["seq"] for r in classified}:
        raise RuntimeError("manifest seq not covered by classified rows")
    el["_plan_meta"] = {
        "classified_rows": len(classified),
        "eligible_rows": len(eligible),
        "excluded_upstream_has_codes": len(excluded),
        "write_set": len(plan),
    }
    return el, plan


def scan(news: Path, plan: list[dict]) -> dict:
    """只读扫描：分类每一行，不写任何东西。"""
    con = ro_conn(news)
    try:
        stats = {"plan": len(plan), "would_write": 0, "already_target": 0,
                 "skip_nonempty_stocks": 0, "skip_jev_companies": 0, "skip_missing": 0}
        samples = []
        for item in plan:
            row = con.execute("SELECT stocks, jev FROM news WHERE id=?",
                              (item["news_id"],)).fetchone()
            if row is None:
                stats["skip_missing"] += 1
                continue
            before, jev = row
            if before == item["target"]:
                stats["already_target"] += 1
                continue
            if (jload(jev, {}) or {}).get("companies"):
                stats["skip_jev_companies"] += 1
                continue
            if not is_empty_stocks(before):
                stats["skip_nonempty_stocks"] += 1
                continue
            stats["would_write"] += 1
            if len(samples) < 5:
                samples.append({"news_id": item["news_id"], "before": before,
                                "after": item["target"]})
        total = (stats["would_write"] + stats["already_target"]
                 + stats["skip_jev_companies"] + stats["skip_nonempty_stocks"]
                 + stats["skip_missing"])
        if total != stats["plan"]:
            raise RuntimeError("scan not conserved: %d != %d" % (total, stats["plan"]))
        stats["samples"] = samples
        stats["news_total_rows"] = con.execute("SELECT count(*) FROM news").fetchone()[0]
        stats["integrity"] = con.execute("PRAGMA integrity_check").fetchone()[0]
        return stats
    finally:
        con.close()


def make_backup(news: Path, backup_dir: Path, plan: list[dict]) -> dict:
    """SQLite backup() 一致性快照（WAL + 并发写下的唯一可靠回滚依据）。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / BACKUP_NAME
    if dst.exists():
        raise RuntimeError("backup already exists, refuse to overwrite: %s" % dst)
    src = ro_conn(news)
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()

    check = ro_conn(dst)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("backup integrity_check=%s" % integrity)
        found = check.execute(
            "SELECT count(*) FROM news WHERE id IN (%s)" % ",".join("?" * len(plan)),
            [r["news_id"] for r in plan]).fetchone()[0]
        empty = 0
        for item in plan:
            row = check.execute("SELECT stocks FROM news WHERE id=?",
                                (item["news_id"],)).fetchone()
            if row and is_empty_stocks(row[0]):
                empty += 1
        total = check.execute("SELECT count(*) FROM news").fetchone()[0]
    finally:
        check.close()
    # 目标行是否齐全「如实记录」，不在此硬失败：采集库里目标行可能刚被删，
    # 且备份职责只是生成一致性快照；强校验放到外部只读验收（36_/42_ 证据）。
    missing = len(plan) - found

    # 字节副本仅作留痕；并发写下可能不一致，如实记录而不判失败。
    raw = backup_dir / RAW_NAME
    shutil.copy2(news, raw)
    identical = filecmp.cmp(news, raw, shallow=False)
    wal = Path(str(news) + "-wal")
    shm = Path(str(news) + "-shm")
    wal_bytes = wal.stat().st_size if wal.exists() else None
    if wal.exists():
        shutil.copy2(wal, backup_dir / "news.db.pre_dq.wal")
    if shm.exists():
        shutil.copy2(shm, backup_dir / "news.db.pre_dq.shm")

    meta = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "source_db": str(news), "backup_db": str(dst),
            "method": "sqlite3.Connection.backup() consistency snapshot",
            "backup_bytes": dst.stat().st_size,
            "backup_integrity": integrity,
            "backup_news_rows": total,
            "target_rows_found": found,
            "target_rows_stocks_empty": empty,
            "target_rows_missing": missing,
            "expected_write_set": len(plan),
            "raw_copy_bytes": raw.stat().st_size,
            "raw_byte_identical": identical,
            "wal_bytes_at_backup": wal_bytes,
            "note": "raw_byte_identical=False 表示采集服务在复制窗口内并发写，"
                    "回滚以 backup_db 一致性快照 + receipt 按 id 反向还原为准。"}
    (backup_dir / "backup_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def require_backup(backup_dir: Path, plan: list[dict]) -> dict:
    dst = backup_dir / BACKUP_NAME
    meta_path = backup_dir / "backup_meta.json"
    if not dst.exists() or not meta_path.exists():
        raise RuntimeError("backup missing: run --backup-only first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("backup_integrity") != "ok" or meta.get("expected_write_set") != len(plan):
        raise RuntimeError("backup meta invalid: %r" % meta)
    con = ro_conn(dst)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup db integrity_check failed at apply time")
    finally:
        con.close()
    return meta


def apply_write(news: Path, backup_dir: Path, plan: list[dict], confirm: bool) -> dict:
    if not confirm:
        raise RuntimeError("--apply requires --confirm")
    meta = require_backup(backup_dir, plan)
    receipt_path = backup_dir / RECEIPT_NAME
    if receipt_path.exists():
        raise RuntimeError("receipt already exists, refuse duplicate apply: %s" % receipt_path)

    con = sqlite3.connect(str(news), timeout=30.0)
    con.execute("PRAGMA busy_timeout=30000")
    receipt_rows = []
    stats = {"plan": len(plan), "written": 0, "already_target": 0,
             "skip_nonempty_stocks": 0, "skip_jev_companies": 0, "skip_missing": 0}
    tmp = backup_dir / (RECEIPT_NAME + ".tmp")
    try:
        con.execute("BEGIN IMMEDIATE")
        for item in plan:
            row = con.execute("SELECT stocks, jev FROM news WHERE id=?",
                              (item["news_id"],)).fetchone()
            if row is None:
                stats["skip_missing"] += 1
                continue
            before, jev = row
            if before == item["target"]:
                stats["already_target"] += 1
                continue
            if (jload(jev, {}) or {}).get("companies"):
                stats["skip_jev_companies"] += 1
                continue
            if not is_empty_stocks(before):
                stats["skip_nonempty_stocks"] += 1
                continue
            con.execute("UPDATE news SET stocks=? WHERE id=?",
                        (item["target"], item["news_id"]))
            receipt_rows.append({
                "news_id": item["news_id"], "before": before,
                "after": item["target"], "source": SOURCE_TAG,
                "sha256": hashlib.sha256(
                    (str(item["news_id"]) + "|" + str(before) + "|" + item["target"])
                    .encode("utf-8")).hexdigest(),
            })
            stats["written"] += 1
        total = (stats["written"] + stats["already_target"]
                 + stats["skip_jev_companies"] + stats["skip_nonempty_stocks"]
                 + stats["skip_missing"])
        if total != stats["plan"]:
            raise RuntimeError("apply not conserved: %d != %d" % (total, stats["plan"]))
        receipt = {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "scope": "HIT-DQ-20261107 / V1' / news.db::news.stocks",
            "source_tag": SOURCE_TAG,
            "backup_db": meta["backup_db"],
            "stats": stats,
            "rows": receipt_rows,
        }
        # commit 前先落 tmp，保证进程被杀也有回滚依据
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        con.commit()
    except BaseException:
        con.rollback()
        if tmp.exists():
            tmp.unlink()
        raise
    finally:
        con.close()
    os.replace(tmp, receipt_path)
    receipt["receipt_path"] = str(receipt_path)
    return receipt


def rollback(news: Path, backup_dir: Path) -> dict:
    receipt_path = backup_dir / RECEIPT_NAME
    if not receipt_path.exists():
        raise RuntimeError("receipt missing, nothing to rollback: %s" % receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    con = sqlite3.connect(str(news), timeout=30.0)
    con.execute("PRAGMA busy_timeout=30000")
    stats = {"receipt_rows": len(receipt["rows"]), "restored": 0,
             "skip_changed_after_apply": 0, "skip_missing": 0}
    try:
        con.execute("BEGIN IMMEDIATE")
        for r in receipt["rows"]:
            row = con.execute("SELECT stocks FROM news WHERE id=?",
                              (r["news_id"],)).fetchone()
            if row is None:
                stats["skip_missing"] += 1
            elif row[0] != r["after"]:
                stats["skip_changed_after_apply"] += 1
            else:
                con.execute("UPDATE news SET stocks=? WHERE id=?",
                            (r["before"], r["news_id"]))
                stats["restored"] += 1
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "stats": stats, "receipt_path": str(receipt_path)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="HIT-DQ-20261107 V1' backfill executor")
    p.add_argument("--news-db", default=str(DEFAULT_NEWS))
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--rows", default=str(DEFAULT_ROWS))
    p.add_argument("--backup-dir", default=str(DEFAULT_BACKUP))
    p.add_argument("--expected-write-set", type=int, default=EXPECTED_WRITE_SET)
    p.add_argument("--expected-excluded", type=int, default=17)
    p.add_argument("--json-out")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--backup-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    p.add_argument("--confirm", action="store_true", help="--apply 必填")
    args = p.parse_args(argv)

    news = Path(args.news_db)
    backup_dir = Path(args.backup_dir)
    if not news.exists():
        raise SystemExit("news.db not found: %s" % news)
    if args.rollback:
        result = rollback(news, backup_dir)
        result["mode"] = "rollback"
    else:
        el, plan = load_plan(Path(args.manifest), Path(args.rows),
                             expected=args.expected_write_set,
                             expected_excluded=args.expected_excluded)
        if args.backup_only:
            result = make_backup(news, backup_dir, plan)
            result["mode"] = "backup-only"
        elif args.apply:
            result = apply_write(news, backup_dir, plan, args.confirm)
            result["mode"] = "apply"
        else:
            result = scan(news, plan)
            result["mode"] = "dry-run"
        result["manifest_sha256"] = el["sha256"]
        result["manifest_count"] = el["count"]
        result["write_set"] = len(plan)
        result["plan_meta"] = el["_plan_meta"]
    result["news_db"] = str(news)
    result["generated_at"] = result.get(
        "generated_at", dt.datetime.now().isoformat(timespec="seconds"))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
