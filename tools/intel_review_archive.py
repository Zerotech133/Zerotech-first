# -*- coding: utf-8 -*-
"""情报复核队列归档：pending 预算分层 → unreviewed_legacy（WO-INTEL-REVIEW-20261031）。

执行拍板 2/4（2026-10-31）：JEV 复核预算 = 人工 50 + 机器预审 200；news.db 里
10,525 条 pending 一次分层，预算内保留 pending，其余归档为 unreviewed_legacy。
2026-11-07 裁定（INTEL-DEC-20261107 A1 + ①a）：复核产能 200 条/日，本工具的归档
保留预算同步由 250 对齐为 200；上面 10-31 的「人工 50 + 机器预审 200」是历史
裁定原文，保留不改。

分层规则（keep 优先序，全部可审计）：
  1) 高影响优先：解析 jev_review_queue.original_jev 的 impact（缺失时回退
     news.jev 同名字段）。high > medium > low > 缺失。
  2) 低置信优先：conf <= 0.4 加分 > conf <= 0.6 加分（低置信更需要人工看）。
  3) 新来源优先：news.source 首次出现（MIN(ts)）在最近 novel-days 天内加分。
  4) 近 recent-days 天入队优先；同分按 queued_at 新→旧、id 升序稳定排序。
  预算内（默认 200，2026-11-07 起按 A1 对齐；原 250）保留 pending；预算外 → status='unreviewed_legacy'。

安全纪律：
  - 默认 --dry-run（只读连接 mode=ro，不做任何写操作）；
  - --apply 前必须成功做一次 news.db 一致性备份（sqlite backup API，不是简单
    copy，能带上 WAL 内容），备份默认落 C:\\Temp，可用 --backup-dir 改；
  - 只 UPDATE status 一列，标题/original_jev/裁决字段/note 全部保留，可重开
    （portal/api_jev_review.py POST /api/jev/review/reopen: legacy → pending）；
  - 写事务内先校验 keep/legacy/总数，不符则 rollback 并报错；
  - 备份旁写 <backup>.manifest.json（计数/样本/规则版本），回滚=停写后把备份
    还原回 news.db（本工具不自动删库/不改 schema/不删行）。

用法：
    python tools/intel_review_archive.py --dry-run
    python tools/intel_review_archive.py --db <news.db> --dry-run --samples 10
    python tools/intel_review_archive.py --apply --backup-dir C:\\Temp
    python tools/intel_review_archive.py --apply --human-budget 50 --machine-budget 150
退出码：0=成功；1=参数/数据错误；2=备份/写库失败。
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
DEFAULT_BACKUP_DIR = Path(r"C:\Temp")

RULE_VERSION = "intel-review-archive/v1"
PENDING_STATUS = "pending"
LEGACY_STATUS = "unreviewed_legacy"
HUMAN_BUDGET = 50
# 2026-11-07 INTEL-DEC-20261107 A1（复核产能 200 条/日）+ ①a 裁定：归档保留预算
# 与日产能对齐，合计由 250 降为 200（human 50 + machine 150）。**仅本工具**；
# portal/api_jev_review.py 仍为 50+200=250，属已知口径差异，登记在
# reports/intel_decision_impl_request_20261107.md §6。
MACHINE_BUDGET = 150
PENDING_BUDGET = HUMAN_BUDGET + MACHINE_BUDGET  # 200

IMPACT_WEIGHT = {"high": 40, "medium": 20, "low": 5}
CONF_LOW_STRONG_BONUS = 15   # conf <= 0.4
CONF_LOW_WEAK_BONUS = 8      # conf <= 0.6
RECENT_DAYS = 7
RECENT_BONUS = 20
NOVEL_DAYS = 7
NOVEL_BONUS = 10

MAX_SAMPLES = 20
_TIME_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _connect_ro(db_path) -> sqlite3.Connection:
    p = Path(db_path).resolve()
    if not p.is_file():
        raise RuntimeError("news.db 不存在：%s" % p)
    uri = "file:" + p.as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def _connect_rw(db_path) -> sqlite3.Connection:
    p = Path(db_path).resolve()
    if not p.is_file():
        raise RuntimeError("news.db 不存在：%s" % p)
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _parse_json_obj(raw) -> dict:
    """解析 JSON 对象；坏串/非对象 → {}（不抛，交由 impact 缺失降级处理）。"""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _parse_time(text):
    s = str(text or "").strip()
    for fmt in _TIME_FMTS:
        try:
            return dt.datetime.strptime(s[:len(dt.datetime.now().strftime(fmt))], fmt)
        except ValueError:
            continue
    return None


def _now_naive(now=None) -> dt.datetime:
    """--now / 默认当前时间 → naive 本地时间（queued_at 存的是 naive 本地串）。"""
    if now is None:
        return dt.datetime.now()
    if isinstance(now, dt.datetime):
        return now.replace(tzinfo=None)
    text = str(now).strip().replace(" ", "T")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("--now 不是合法 ISO8601：%r" % (now,)) from exc
    return parsed.astimezone().replace(tzinfo=None)


def _impact_of(original_jev, news_jev) -> str:
    """impact 取值：original_jev 优先，回退 news.jev；未知/缺失 → ''。"""
    for raw in (original_jev, news_jev):
        val = str(_parse_json_obj(raw).get("impact") or "").strip().lower()
        if val in IMPACT_WEIGHT:
            return val
    return ""


def _source_first_seen(con: sqlite3.Connection) -> dict:
    """news.source → 首次出现时间（MIN(ts)，解析失败的 ts 忽略）。"""
    first = {}
    for row in con.execute("SELECT source, MIN(ts) AS t FROM news GROUP BY source"):
        src = str(row["source"] or "").strip() or "(unknown)"
        t = _parse_time(row["t"])
        if t is None:
            continue
        if src not in first or t < first[src]:
            first[src] = t
    return first


def _recent_bonus(queued_at, now: dt.datetime, recent_days: int) -> int:
    t = _parse_time(queued_at)
    if t is None:
        return 0
    return RECENT_BONUS if (now - t).total_seconds() <= recent_days * 86400 else 0


def _score(row: dict, now: dt.datetime, recent_days: int,
           first_seen: dict, novel_days: int) -> dict:
    impact = _impact_of(row.get("original_jev"), row.get("news_jev"))
    score = IMPACT_WEIGHT.get(impact, 0)
    conf = row.get("conf")
    if isinstance(conf, (int, float)):
        if conf <= 0.4:
            score += CONF_LOW_STRONG_BONUS
        elif conf <= 0.6:
            score += CONF_LOW_WEAK_BONUS
    score += _recent_bonus(row.get("queued_at"), now, recent_days)
    src = str(row.get("source") or "").strip() or "(unknown)"
    seen = first_seen.get(src)
    novel = bool(seen and (now - seen).total_seconds() <= novel_days * 86400)
    if novel:
        score += NOVEL_BONUS
    return {"impact": impact or "none", "source": src,
            "novel_source": novel, "score": score, "recent": _recent_bonus(
                row.get("queued_at"), now, recent_days) > 0}


def _row_samples(rows: list, limit: int) -> list:
    out = []
    for r in rows[:max(0, limit)]:
        title = str(r.get("title") or "")
        title = "".join(ch if ch.isprintable() else " " for ch in title)[:60]
        out.append({"id": r["id"], "queued_at": r.get("queued_at"),
                    "conf": r.get("conf"), "impact": r.get("impact"),
                    "source": r.get("source"), "novel_source": r.get("novel_source"),
                    "score": r.get("score"), "title": title})
    return out


def build_plan(db_path=None, now=None, human_budget: int = HUMAN_BUDGET,
               machine_budget: int = MACHINE_BUDGET, recent_days: int = RECENT_DAYS,
               novel_days: int = NOVEL_DAYS, samples: int = 5) -> dict:
    """只读生成分层计划（不写库）。返回 counts/keep/legacy 样本与规则参数。"""
    if human_budget < 0 or machine_budget < 0:
        raise ValueError("预算不能为负")
    keep_budget = human_budget + machine_budget
    clk = _now_naive(now)
    con = _connect_ro(db_path or DEFAULT_DB)
    try:
        pending_rows = con.execute(
            "SELECT id, queued_at, conf, bias, title, original_jev "
            "FROM jev_review_queue WHERE status=?", (PENDING_STATUS,)
        ).fetchall()
        nde = {r["id"]: r for r in con.execute(
            "SELECT id, source, ts, jev FROM news WHERE id IN "
            "(SELECT id FROM jev_review_queue WHERE status=?)", (PENDING_STATUS,))}
        total = con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        legacy_now = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=?", (LEGACY_STATUS,)
        ).fetchone()[0]
        resolved_now = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status='resolved'"
        ).fetchone()[0]
        first_seen = _source_first_seen(con)
    finally:
        con.close()

    rows = []
    for r in pending_rows:
        nrow = nde.get(r["id"])
        item = {
            "id": r["id"], "queued_at": r["queued_at"], "conf": r["conf"],
            "bias": r["bias"], "title": r["title"],
            "original_jev": r["original_jev"],
            "news_jev": nrow["jev"] if nrow else None,
            "source": (nrow["source"] if nrow else "") or "",
            "news_ts": (nrow["ts"] if nrow else None),
        }
        item.update(_score(item, clk, recent_days, first_seen, novel_days))
        rows.append(item)
    # 稳定排序：score desc → queued_at desc → id asc
    rows.sort(key=lambda x: str(x.get("id") or ""))
    rows.sort(key=lambda x: _parse_time(x.get("queued_at")) or dt.datetime.min, reverse=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    kept, legacy = rows[:keep_budget], rows[keep_budget:]
    pending_after = len(kept)
    # 预算档位（与 portal/api_jev_review.tier_counts 同口径）
    human = min(pending_after, human_budget)
    machine = min(max(pending_after - human_budget, 0), machine_budget)
    over = max(pending_after - keep_budget, 0)
    return {
        "ok": True, "rule_version": RULE_VERSION, "db": str(Path(db_path or DEFAULT_DB)),
        "now": clk.strftime("%Y-%m-%d %H:%M:%S"),
        "budgets": {"human_budget": human_budget,
                    "machine_budget": machine_budget},
        "rules": {"impact_weights": IMPACT_WEIGHT,
                  "conf_low_strong": CONF_LOW_STRONG_BONUS,
                  "conf_low_weak": CONF_LOW_WEAK_BONUS,
                  "recent_days": recent_days, "recent_bonus": RECENT_BONUS,
                  "novel_days": novel_days, "novel_bonus": NOVEL_BONUS},
        "counts": {"total": total, "pending_before": len(rows),
                   "pending_after": pending_after,
                   "keep": pending_after,
                   "legacy_to_mark": len(legacy),
                   "legacy_before": legacy_now,
                   "legacy_after": legacy_now + len(legacy),
                   "resolved": resolved_now},
        "tier_counts": {"human": human, "machine": machine, "over_budget": over,
                        "pending": pending_after,
                        "unreviewed_legacy": legacy_now + len(legacy),
                        "resolved": resolved_now, "total": total},
        "impact": {"keep": _impact_dist(kept), "legacy": _impact_dist(legacy)},
        "novel_sources_kept": sum(1 for x in kept if x["novel_source"]),
        "samples": {"keep": _row_samples(kept, samples),
                    "legacy": _row_samples(legacy, samples)},
        "keep_ids": [x["id"] for x in kept],
        "legacy_ids": [x["id"] for x in legacy],
    }


def _impact_dist(rows: list) -> dict:
    out = {}
    for r in rows:
        k = r.get("impact") or "none"
        out[k] = out.get(k, 0) + 1
    return out


def _backup_db(db_path, backup_dir) -> Path:
    """sqlite backup API 一致性备份（含 WAL）；返回备份路径。"""
    src = _connect_ro(db_path)
    try:
        bdir = Path(backup_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest_path = bdir / ("news.db.bak-%s" % stamp)
        if dest_path.exists():
            raise RuntimeError("备份文件已存在：%s" % dest_path)
        dst = sqlite3.connect(str(dest_path))
        try:
            src.backup(dst)  # 一致性快照（含 WAL 内容）
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    # 备份自检：可打开 + 行数与源一致 + integrity_check ok
    con = _connect_ro(dest_path)
    try:
        n = con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        src_con = _connect_ro(db_path)
        try:
            n_src = src_con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        finally:
            src_con.close()
        if n != n_src:
            raise RuntimeError("备份行数不符：backup=%s src=%s" % (n, n_src))
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if str(integrity).lower() != "ok":
            raise RuntimeError("备份 integrity_check 失败：%s" % integrity)
    finally:
        con.close()
    return dest_path


def apply_plan(plan: dict, db_path=None, backup_dir=None) -> dict:
    """执行分层：先备份，再单事务把 legacy_ids 的 pending → unreviewed_legacy。"""
    db = db_path or plan.get("db") or DEFAULT_DB
    legacy_ids = list(plan.get("legacy_ids") or [])
    counts = plan.get("counts") or {}
    if not legacy_ids:
        return {"ok": True, "applied": False, "marked_legacy": 0,
                "reason": "no_pending_out_of_budget",
                "counts": counts, "backup": None}
    backup = _backup_db(db, backup_dir or DEFAULT_BACKUP_DIR)
    con = _connect_rw(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        marked = 0
        chunk = 500
        for i in range(0, len(legacy_ids), chunk):
            part = legacy_ids[i:i + chunk]
            ph = ",".join("?" * len(part))
            cur = con.execute(
                "UPDATE jev_review_queue SET status=? "
                "WHERE status=? AND id IN (%s)" % ph,
                [LEGACY_STATUS, PENDING_STATUS] + part,
            )
            marked += cur.rowcount
        # 事务内先校验再 commit（不符即 rollback，不留半成品）
        pending_after = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=?", (PENDING_STATUS,)
        ).fetchone()[0]
        legacy_after = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status=?", (LEGACY_STATUS,)
        ).fetchone()[0]
        total_after = con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        expected_pending = counts.get("pending_after")
        expected_legacy = counts.get("legacy_after")
        expected_total = counts.get("total")
        if (expected_pending is not None and pending_after != expected_pending) or \
                (expected_legacy is not None and legacy_after != expected_legacy) or \
                (expected_total is not None and total_after != expected_total):
            con.rollback()
            return {"ok": False, "error": "verify_mismatch", "backup": str(backup),
                    "marked_legacy": marked,
                    "got": {"pending": pending_after, "legacy": legacy_after,
                            "total": total_after},
                    "expected": {"pending": expected_pending,
                                 "legacy": expected_legacy, "total": expected_total}}
        con.commit()
    except Exception:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
    manifest = {
        "schema": "intel-review-archive-manifest/v1",
        "rule_version": plan.get("rule_version"),
        "db": str(Path(db).resolve()), "backup": str(backup),
        "applied_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "budgets": plan.get("budgets"), "rules": plan.get("rules"),
        "counts": {**counts, "marked_legacy": marked,
                   "pending_after_check": pending_after,
                   "legacy_after_check": legacy_after,
                   "total_after_check": total_after},
        # 归档 id 清单只存指纹（可审计/可比对，不把 1 万条原文再抄一份）
        "legacy_ids_count": len(legacy_ids),
        "legacy_ids_sha256": hashlib.sha256(
            "\n".join(legacy_ids).encode("utf-8")).hexdigest(),
        "samples": plan.get("samples"),
    }
    Path(str(backup) + ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "applied": True, "marked_legacy": marked,
            "backup": str(backup), "manifest": str(backup) + ".manifest.json",
            "pending_after": pending_after, "legacy_after": legacy_after,
            "total_after": total_after}


def _fmt_plan(plan: dict) -> str:
    c = plan["counts"]
    t = plan["tier_counts"]
    lines = [
        "[%s] 规则=%s  db=%s" % ("DRY-RUN" if not plan.get("_apply") else "APPLY",
                                plan["rule_version"], plan["db"]),
        "预算: human=%s machine=%s（合计 %s）" % (
            plan["budgets"]["human_budget"], plan["budgets"]["machine_budget"],
            plan["budgets"]["human_budget"] + plan["budgets"]["machine_budget"]),
        "pending: %s → 保留 %s / 归档 legacy %s（legacy %s → %s）" % (
            c["pending_before"], c["pending_after"], c["legacy_to_mark"],
            c["legacy_before"], c["legacy_after"]),
        "tier: human=%s machine=%s over_budget=%s legacy=%s resolved=%s total=%s" % (
            t["human"], t["machine"], t["over_budget"],
            t["unreviewed_legacy"], t["resolved"], t["total"]),
        "keep impact: %s" % json.dumps(plan["impact"]["keep"], ensure_ascii=False, sort_keys=True),
        "legacy impact: %s" % json.dumps(plan["impact"]["legacy"], ensure_ascii=False, sort_keys=True),
        "新来源保留数: %s" % plan["novel_sources_kept"],
    ]
    for kind in ("keep", "legacy"):
        for s in plan["samples"][kind]:
            lines.append("  %-6s id=%s conf=%s impact=%s source=%s queued=%s title=%s" % (
                kind, s["id"], s["conf"], s["impact"], s["source"],
                s["queued_at"], s["title"]))
    return "\n".join(lines)


def _emit(obj: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文可读（失败不影响逻辑）
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        description="JEV 复核队列分层归档（预算内保留 pending，其余 unreviewed_legacy）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="news.db 路径")
    ap.add_argument("--dry-run", action="store_true",
                    help="只读预览（默认行为；与 --apply 互斥）")
    ap.add_argument("--apply", action="store_true",
                    help="执行写库（默认 dry-run；执行前强制备份）")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR),
                    help="apply 前备份目录（默认 C:\\Temp）")
    ap.add_argument("--human-budget", type=int, default=HUMAN_BUDGET)
    ap.add_argument("--machine-budget", type=int, default=MACHINE_BUDGET)
    ap.add_argument("--recent-days", type=int, default=RECENT_DAYS)
    ap.add_argument("--novel-days", type=int, default=NOVEL_DAYS)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--now", default=None, help="注入时钟（ISO8601，测试/回放用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认文本摘要）")
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        print(json.dumps({"ok": False, "error": "--apply 与 --dry-run 互斥"},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    try:
        plan = build_plan(db_path=args.db, now=args.now,
                          human_budget=args.human_budget,
                          machine_budget=args.machine_budget,
                          recent_days=args.recent_days,
                          novel_days=args.novel_days,
                          samples=max(0, min(MAX_SAMPLES, args.samples)))
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    if not args.apply:
        plan["_apply"] = False
        if args.json:
            _emit({k: v for k, v in plan.items() if k not in ("keep_ids", "legacy_ids")},
                  True)
        else:
            print(_fmt_plan(plan))
        return 0

    if plan["counts"]["legacy_to_mark"] <= 0:
        if args.json:
            _emit({"ok": True, "applied": False,
                   "reason": "no_pending_out_of_budget", "counts": plan["counts"]}, True)
        else:
            print("无待归档 pending（预算内已收敛，零写库）")
        return 0
    try:
        result = apply_plan(plan, db_path=args.db, backup_dir=args.backup_dir)
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    if args.json:
        _emit(result, True)
    else:
        print(_fmt_plan({**plan, "_apply": True}))
        print("apply: marked_legacy=%s backup=%s" % (
            result.get("marked_legacy"), result.get("backup")))
        print("回滚：停写后把备份还原回 --db 路径（原 pending 状态即恢复）")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
