# -*- coding: utf-8 -*-
"""JEV 复核队列滚动刷新（WO-JEV-OPS-20261103，只读扫描 + 受控入队）。

背景（2026-11-03 实测）：`news.db.jev_review_queue` 10,525 行全部是 2026-10-30
一次建队（unreviewed_legacy 10,275 / pending 150 / resolved 100），此后不再滚动；
news 侧已判读 115,249 条、judged_at 非空 10,319 条，绝大多数从未进过复核队列。

口径（只读 news.db；默认 dry-run）：
  候选 = 已判读（`news.jev` 非空 或 `news.judged_at` 非空）且 id 不在
         `jev_review_queue` 的记录。
  分层（优先级从高到低；同一候选可命中多层，归入最高优先级层）：
    high_impact : jev.impact == 'high'（2026-10 后 JEV JSON 字段，覆盖 57%）
    low_conf    : jev.conf < 阈值（默认 0.7，env JEV_REVIEW_CONF）
    new_source  : 该 source 的首条新闻 ts 落在最近 N 天（默认 30 天；以库内
                  max(ts) 为基准，避免本机时钟漂移）
  本轮入队 = 分层去重排序后前 `--limit` 条（默认 500，避免一次灌爆复核队列）。
  排序：层优先级 → conf 升序（越高风险越先）→ ts 降序（新样本优先）。

安全设计：
  - dry-run 全程只读连接（`mode=ro` + `PRAGMA query_only=1`），不写库；
    `--apply` 必须同时给 `--confirm`，否则 rc=2 直接拒跑；
  - `--apply` 先做 sqlite3 一致性快照（backup API，WAL 安全）+ `integrity_check`
    到备份目录（默认 `C:\\Temp\\jev_queue_backups\\`），再单事务 `INSERT OR IGNORE`；
  - 幂等键 = `jev_review_queue.id`（news_id 主键唯一），重复执行 added=0；
  - 写失败自动回滚（事务），不留下半截队列；
  - **生产库 --apply 属 L2（写生产数据 + 计划任务注册），需老田批准**：
    见 `docs/ops/2026-11-03_JEV复核与校准L2申请单_v1.md`。

用法：
  python -X utf8 -B tools/jev_queue_refresh.py                    # dry-run（默认）
  python -X utf8 -B tools/jev_queue_refresh.py --json             # 机器可读
  python -X utf8 -B tools/jev_queue_refresh.py --apply --confirm  # 生产需 L2 审批
退出码：0=成功（含 dry-run / 无候选）；1=--apply 写库失败（已回滚）；
        2=前置错误（库/表/列缺失、参数非法、未 --confirm）；4=坏行且 --fail-on-bad。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("JEV_NEWS_DB") or (REPO / "news_collector" / "news.db"))
DEFAULT_BACKUP_DIR = Path(
    os.environ.get("JEV_QUEUE_BACKUP_DIR") or r"C:\Temp\jev_queue_backups")
DEFAULT_CONF = 0.7
DEFAULT_NEW_SOURCE_DAYS = 30
DEFAULT_LIMIT = 500
LAYER_ORDER = ("high_impact", "low_conf", "new_source")
LAYER_RANK = {name: idx for idx, name in enumerate(LAYER_ORDER)}
NEWS_REQUIRED = ("id", "source", "ts", "title", "stocks", "jev", "judged_at")
QUEUE_REQUIRED = ("id", "queued_at", "conf", "bias", "title", "stocks",
                  "original_jev", "status")
TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


class PreconditionError(RuntimeError):
    """前置条件不满足（缺库/缺表/缺列/参数非法）——fail-visible，rc=2。"""


class ApplyError(RuntimeError):
    """--apply 写库失败（事务已回滚）——rc=1。"""


# --------------------------------------------------------------------------- #
# 只读连接 / schema
# --------------------------------------------------------------------------- #
def ro_connect(db_path: Path) -> sqlite3.Connection:
    """只读连接（含 PRAGMA query_only=1），任何误写都会被 SQLite 拒绝。"""
    uri = "file:%s?mode=ro" % Path(db_path).as_posix()
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.execute("PRAGMA query_only=1")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute('PRAGMA table_info("%s")' % table)]


def require_schema(conn: sqlite3.Connection, db_path: Path) -> None:
    """缺表/缺列一律 fail-visible（rc=2），不静默降级。"""
    if not table_exists(conn, "news"):
        raise PreconditionError(
            "缺表 news：%s 不是 news.db 或 schema 未初始化（表=%s）"
            % (db_path, [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]))
    if not table_exists(conn, "jev_review_queue"):
        raise PreconditionError(
            "缺表 jev_review_queue：%s 无复核队列表（先由 news_collector/"
            "jev_review_queue.py --build 建队或迁移）" % db_path)
    news_cols = table_columns(conn, "news")
    missing = [c for c in NEWS_REQUIRED if c not in news_cols]
    if missing:
        raise PreconditionError(
            "news 缺列 %s（实际列=%s）" % (missing, news_cols))
    queue_cols = table_columns(conn, "jev_review_queue")
    missing = [c for c in QUEUE_REQUIRED if c not in queue_cols]
    if missing:
        raise PreconditionError(
            "jev_review_queue 缺列 %s（实际列=%s）" % (missing, queue_cols))


# --------------------------------------------------------------------------- #
# 解析辅助
# --------------------------------------------------------------------------- #
def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def parse_jev(raw) -> tuple[dict | None, str]:
    """返回 (obj, err)；err 非空 = 坏行（fail-visible 计数 + 样例）。"""
    if raw is None or str(raw).strip() == "":
        return None, "empty"
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return None, "json:%s" % str(exc).replace("\n", " ")[:120]
    if not isinstance(obj, dict):
        return None, "not_object"
    return obj, ""


def conf_of(obj: dict | None) -> tuple[float | None, str]:
    """取 conf；返回 (值, err)。err 非空 = conf 字段缺失/非法（不计为坏行）。"""
    if not isinstance(obj, dict) or "conf" not in obj:
        return None, "missing"
    val = obj.get("conf")
    if isinstance(val, bool) or val is None:
        return None, "invalid"
    try:
        return float(val), ""
    except (TypeError, ValueError):
        return None, "invalid"


def sha256_of(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        # 生产 news.db 由采集进程持有写锁时可能读不到字节流；哈希仅作证据，
        # 不因取不到而让只读扫描失败（只读性由 mode=ro/query_only 保证）。
        return ""


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def scan(conn: sqlite3.Connection, conf_threshold: float,
         new_source_days: int) -> dict:
    """只读扫描候选与分层；返回原始统计（无文件 IO）。"""
    by_status: dict[str, int] = {}
    for status, n in conn.execute(
            "SELECT status, count(*) FROM jev_review_queue GROUP BY status"):
        by_status[str(status)] = int(n)
    queue_total = sum(by_status.values())

    judged = conn.execute(
        "SELECT count(*) FROM news WHERE "
        "(jev IS NOT NULL AND trim(jev) <> '') OR "
        "(judged_at IS NOT NULL AND trim(judged_at) <> '')").fetchone()[0]

    latest_ts = conn.execute("SELECT max(ts) FROM news").fetchone()[0]
    latest_dt = parse_ts(latest_ts)
    cutoff = latest_dt - timedelta(days=int(new_source_days)) if latest_dt else None

    first_by_source: dict[str, tuple[str, datetime | None]] = {}
    for source, min_ts in conn.execute(
            "SELECT source, min(ts) FROM news GROUP BY source"):
        key = str(source or "")
        first_by_source[key] = (str(min_ts or ""), parse_ts(min_ts))
    new_sources = sorted(
        src for src, (_raw, dt) in first_by_source.items()
        if cutoff is not None and dt is not None and dt >= cutoff)

    layer_primary = {name: 0 for name in LAYER_ORDER}
    layer_primary["unstratified"] = 0
    layer_matched = {name: 0 for name in LAYER_ORDER}
    overlap = {"high_and_low": 0, "high_and_new": 0, "low_and_new": 0,
               "high_low_new": 0}
    bad_jev: list[dict] = []
    bad_jev_n = 0
    bad_conf_n = 0
    unstratified_n = 0
    candidates: list[dict] = []
    source_candidates: dict[str, int] = {}

    cursor = conn.execute(
        "SELECT n.id, n.source, n.ts, n.title, n.stocks, n.jev, n.judged_at "
        "FROM news n WHERE "
        "((n.jev IS NOT NULL AND trim(n.jev) <> '') OR "
        "(n.judged_at IS NOT NULL AND trim(n.judged_at) <> '')) "
        "AND NOT EXISTS (SELECT 1 FROM jev_review_queue q WHERE q.id = n.id)")
    for nid, source, ts, title, stocks, jev, judged_at in cursor:
        obj, err = parse_jev(jev)
        if obj is None and err and err != "empty":
            bad_jev_n += 1
            if len(bad_jev) < 20:
                bad_jev.append({"id": str(nid), "source": str(source or ""),
                                "error": err})
            continue
        conf, _conf_err = conf_of(obj)
        if _conf_err:
            bad_conf_n += 1
        impact = str((obj or {}).get("impact") or "").strip().lower()
        bias = str((obj or {}).get("bias") or "")
        layers = []
        if impact == "high":
            layers.append("high_impact")
        if conf is not None and conf < float(conf_threshold):
            layers.append("low_conf")
        src = str(source or "")
        if src in new_sources:
            layers.append("new_source")
        if not layers:
            unstratified_n += 1
            layer_primary["unstratified"] += 1
            continue
        primary = layers[0]
        layer_primary[primary] += 1
        for name in layers:
            layer_matched[name] += 1
        has_high = "high_impact" in layers
        has_low = "low_conf" in layers
        has_new = "new_source" in layers
        if has_high and has_low:
            overlap["high_and_low"] += 1
        if has_high and has_new:
            overlap["high_and_new"] += 1
        if has_low and has_new:
            overlap["low_and_new"] += 1
        if has_high and has_low and has_new:
            overlap["high_low_new"] += 1
        source_candidates[src] = source_candidates.get(src, 0) + 1
        candidates.append({
            "id": str(nid),
            "source": src,
            "ts": str(ts or ""),
            "judged_at": str(judged_at or ""),
            "title": str(title or ""),
            "stocks": str(stocks or "[]"),
            "conf": conf,
            "bias": bias,
            "impact": impact,
            "layers": layers,
            "jev_raw": str(jev),
        })

    # 排序：层优先级 → conf 升序 → ts 降序
    def _sort_key(cand: dict):
        rank = LAYER_RANK[cand["layers"][0]]
        ts_dt = parse_ts(cand["ts"])
        neg_ts = -ts_dt.timestamp() if ts_dt else 0.0
        conf = cand["conf"]
        if rank <= 1:
            return (rank, conf if conf is not None else -1.0, neg_ts)
        return (rank, 0.0, neg_ts)

    candidates.sort(key=_sort_key)
    return {
        "queue": {"total": queue_total, "by_status": dict(sorted(by_status.items()))},
        "universe": {
            "judged": int(judged),
            "queued": queue_total,
            "candidates": int(judged) - queue_total,
        },
        "latest_ts": str(latest_ts or ""),
        "cutoff_ts": cutoff.strftime("%Y-%m-%d %H:%M:%S") if cutoff else "",
        "new_sources": new_sources,
        "first_by_source": {src: raw for src, (raw, _dt) in first_by_source.items()},
        "layer_primary": layer_primary,
        "layer_matched": layer_matched,
        "overlap": overlap,
        "bad_jev": {"n": bad_jev_n, "samples": bad_jev},
        "bad_conf_n": bad_conf_n,
        "candidates": candidates,
        "source_candidates": source_candidates,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def build_report(scan_res: dict, db_path: Path, conf_threshold: float,
                 new_source_days: int, limit: int, mode: str) -> dict:
    candidates = scan_res["candidates"]
    planned = candidates[:limit] if limit and limit > 0 else list(candidates)
    planned_by_layer: dict[str, int] = {name: 0 for name in LAYER_ORDER}
    planned_by_source: dict[str, int] = {}
    for cand in planned:
        planned_by_layer[cand["layers"][0]] += 1
        planned_by_source[cand["source"]] = planned_by_source.get(cand["source"], 0) + 1
    samples = [{
        "id": c["id"], "source": c["source"], "ts": c["ts"],
        "conf": c["conf"], "bias": c["bias"], "impact": c["impact"],
        "layers": c["layers"],
    } for c in planned[:10]]
    top_sources = []
    for src, n in sorted(scan_res["source_candidates"].items(),
                         key=lambda kv: (-kv[1], kv[0]))[:15]:
        top_sources.append({
            "source": src,
            "candidates": n,
            "planned": planned_by_source.get(src, 0),
            "is_new": src in scan_res["new_sources"],
            "first_ts": scan_res["first_by_source"].get(src, ""),
        })
    return {
        "tool": "jev_queue_refresh",
        "schema_version": 1,
        "mode": mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "db": str(db_path),
        "db_sha256": sha256_of(Path(db_path)) if Path(db_path).is_file() else "",
        "criteria": {
            "judged_definition": "news.jev 非空 或 news.judged_at 非空",
            "conf_threshold": float(conf_threshold),
            "new_source_days": int(new_source_days),
            "limit": int(limit),
            "layer_order": list(LAYER_ORDER),
            "order": "层优先级 → conf 升序 → ts 降序",
        },
        "queue_status": scan_res["queue"],
        "universe": scan_res["universe"],
        "latest_ts": scan_res["latest_ts"],
        "cutoff_ts": scan_res["cutoff_ts"],
        "new_sources": scan_res["new_sources"],
        "layer_primary": scan_res["layer_primary"],
        "layer_matched": scan_res["layer_matched"],
        "overlap": scan_res["overlap"],
        "bad_rows": {
            "n": scan_res["bad_jev"]["n"],
            "invalid_jev": scan_res["bad_jev"]["samples"],
            "invalid_conf": scan_res["bad_conf_n"],
        },
        "planned": {
            "limit": int(limit),
            "n": len(planned),
            "by_layer": planned_by_layer,
            "by_source": dict(sorted(planned_by_source.items(),
                                     key=lambda kv: (-kv[1], kv[0]))),
            "samples": samples,
        },
        "top_sources": top_sources,
        "read_only_scan": True,
        "warnings": [],
    }


def _fmt_int(n) -> str:
    return "{:,}".format(int(n))


def render_text(rep: dict) -> str:
    mode = "DRY-RUN（只读，未写库）" if rep["mode"] == "dry-run" else "APPLY（已写库）"
    q = rep["queue_status"]["by_status"]
    q_detail = " / ".join("%s %s" % (k, _fmt_int(v)) for k, v in q.items())
    lp = rep["layer_primary"]
    lm = rep["layer_matched"]
    ov = rep["overlap"]
    lines = [
        "JEV 复核队列刷新 · %s" % mode,
        "DB: %s" % rep["db"],
        "判读口径: news.jev 非空 或 news.judged_at 非空",
        "已判读 %s ｜ 在队 %s（%s）｜ 未入队候选 %s"
        % (_fmt_int(rep["universe"]["judged"]),
           _fmt_int(rep["queue_status"]["total"]), q_detail,
           _fmt_int(rep["universe"]["candidates"])),
        "分层（primary/matched）: high_impact %s/%s ｜ low_conf %s/%s ｜ "
        "new_source %s/%s ｜ unstratified %s"
        % (_fmt_int(lp["high_impact"]), _fmt_int(lm["high_impact"]),
           _fmt_int(lp["low_conf"]), _fmt_int(lm["low_conf"]),
           _fmt_int(lp["new_source"]), _fmt_int(lm["new_source"]),
           _fmt_int(lp["unstratified"])),
        "交叉: high∩low=%s high∩new=%s low∩new=%s 三层同中=%s"
        % (_fmt_int(ov["high_and_low"]), _fmt_int(ov["high_and_new"]),
           _fmt_int(ov["low_and_new"]), _fmt_int(ov["high_low_new"])),
        "基准: latest_ts=%s ｜ 新来源窗口自 %s 起 ｜ 新来源=%s"
        % (rep["latest_ts"], rep["cutoff_ts"] or "-",
           ",".join(rep["new_sources"]) or "无"),
        "本轮计划入队（--limit %s）: %s 条 ｜ %s"
        % (_fmt_int(rep["planned"]["limit"]), _fmt_int(rep["planned"]["n"]),
           " / ".join("%s %s" % (k, _fmt_int(v))
                      for k, v in rep["planned"]["by_layer"].items())),
        "坏行（jev 非法 JSON / 非对象）: %s ｜ conf 缺失/非法: %s"
        % (_fmt_int(rep["bad_rows"]["n"]),
           _fmt_int(rep["bad_rows"]["invalid_conf"])),
    ]
    for item in rep["planned"]["samples"][:5]:
        lines.append("  样例 id=%s source=%s ts=%s conf=%s bias=%s layers=%s"
                     % (item["id"], item["source"], item["ts"],
                        item["conf"], item["bias"], ",".join(item["layers"])))
    if rep["top_sources"]:
        lines.append("来源 Top: " + " ｜ ".join(
            "%s %s→计划%s%s" % (s["source"], _fmt_int(s["candidates"]),
                               _fmt_int(s["planned"]), "(新)" if s["is_new"] else "")
            for s in rep["top_sources"][:6]))
    if rep["mode"] == "dry-run":
        lines.append("模式: DRY-RUN —— 未写库；--apply 必须同时 --confirm（生产需 L2 审批）")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 一致性备份 + 受控写入
# --------------------------------------------------------------------------- #
def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / (stem + suffix)
    if not base.exists():
        return base
    for i in range(1, 1000):
        cand = directory / ("%s_%d%s" % (stem, i, suffix))
        if not cand.exists():
            return cand
    raise ApplyError("备份文件名冲突过多：%s" % base)


def consistent_backup(db_path: Path, backup_dir: Path) -> tuple[Path, dict]:
    """sqlite3 一致性快照（backup API，WAL 安全）+ 完整性校验。

    WAL 源库由 backup API 拷出的目标库可能仍带 -wal/-shm；这里显式
    `journal_mode=DELETE` 强制 checkpoint 并落成**单文件备份**，避免
    「只拷 .bak 丢最新页」的恢复陷阱。
    """
    stem = "%s.jev_queue_%s" % (Path(db_path).name, time.strftime("%Y%m%d_%H%M%S"))
    dst = _unique_path(Path(backup_dir), stem, ".bak")
    src = ro_connect(Path(db_path))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src.backup(dst_conn)
            try:
                dst_conn.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.Error:
                pass
        finally:
            dst_conn.close()
    finally:
        src.close()
    verify = ro_connect(dst)
    try:
        integrity = verify.execute("PRAGMA integrity_check").fetchone()[0]
        news_rows = verify.execute("SELECT count(*) FROM news").fetchone()[0]
        queue_rows = verify.execute(
            "SELECT count(*) FROM jev_review_queue").fetchone()[0]
    finally:
        verify.close()
    info = {
        "path": str(dst),
        "size_bytes": dst.stat().st_size,
        "integrity_check": str(integrity),
        "news_rows": int(news_rows),
        "queue_rows": int(queue_rows),
        "self_contained": not Path(str(dst) + "-wal").exists(),
    }
    if str(integrity).lower() != "ok":
        raise ApplyError("备份完整性校验失败：integrity_check=%s" % integrity)
    return dst, info


def write_apply_receipt(backup_path: Path, db_path: Path, planned: list[dict],
                        result: dict) -> Path | None:
    """写 apply 回执（含本批全部 news_id）——回滚/审计用；失败不反噬（返回 None）。"""
    receipt = {
        "tool": "jev_queue_refresh",
        "schema_version": 1,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "db": str(db_path),
        "backup_path": str(backup_path),
        "planned": len(planned),
        "result": result,
        "ids": [c["id"] for c in planned],
    }
    path = Path(str(backup_path) + ".receipt.json")
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def apply_planned(db_path: Path, planned: list[dict]) -> dict:
    """单事务写入；幂等（id 主键 + INSERT OR IGNORE）；失败自动回滚。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = [(c["id"], now, c["conf"], c["bias"], str(c["title"] or "")[:300],
             str(c["stocks"] or "[]"), str(c["jev_raw"])) for c in planned]
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        before = conn.execute("SELECT count(*) FROM jev_review_queue").fetchone()[0]
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT OR IGNORE INTO jev_review_queue "
                "(id, queued_at, conf, bias, title, stocks, original_jev) "
                "VALUES (?,?,?,?,?,?,?)", rows)
            conn.execute("COMMIT")
        except sqlite3.Error:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        after = conn.execute("SELECT count(*) FROM jev_review_queue").fetchone()[0]
        return {"performed": True, "before": int(before), "after": int(after),
                "added": int(after) - int(before), "planned": len(rows)}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 TTY / 旧解释器忽略
        pass
    ap = argparse.ArgumentParser(
        description="JEV 复核队列滚动刷新（只读扫描；dry-run 默认）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="news.db 路径（默认 %s）" % DEFAULT_DB)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF,
                    help="low_conf 阈值（默认 0.7；env JEV_REVIEW_CONF）")
    ap.add_argument("--new-source-days", type=int, default=DEFAULT_NEW_SOURCE_DAYS,
                    help="新来源判定窗口天数（默认 30）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="本轮最多入队条数（默认 500；0=不限）")
    ap.add_argument("--apply", action="store_true", help="写库（必须同时 --confirm）")
    ap.add_argument("--confirm", action="store_true", help="确认写生产库（L2 审批后使用）")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR),
                    help="apply 前一致性备份目录（默认 %s）" % DEFAULT_BACKUP_DIR)
    ap.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    ap.add_argument("--fail-on-bad", action="store_true",
                    help="存在坏行（非法 jev JSON）时 rc=4")
    a = ap.parse_args(argv)

    if a.apply and not a.confirm:
        print("[FAIL] --apply 必须同时 --confirm（这是写生产库操作，且需 L2 审批："
              "docs/ops/2026-11-03_JEV复核与校准L2申请单_v1.md）", file=sys.stderr)
        return 2
    if a.limit < 0:
        print("[FAIL] --limit 不能为负（0=不限）", file=sys.stderr)
        return 2
    db_path = Path(a.db)
    if not db_path.is_file():
        print("[FAIL] DB 不存在：%s" % db_path, file=sys.stderr)
        return 2

    try:
        conn = ro_connect(db_path)
        try:
            require_schema(conn, db_path)
            scan_res = scan(conn, a.conf, a.new_source_days)
        finally:
            conn.close()
    except PreconditionError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print("[FAIL] 只读扫描 SQLite 错误：%s" % exc, file=sys.stderr)
        return 2

    mode = "apply" if a.apply else "dry-run"
    rep = build_report(scan_res, db_path, a.conf, a.new_source_days, a.limit, mode)
    if a.apply:
        planned = scan_res["candidates"][:a.limit] if a.limit and a.limit > 0 \
            else list(scan_res["candidates"])
        if not planned:
            rep["apply"] = {"performed": False, "reason": "无候选，未备份未写库"}
        else:
            try:
                backup_path, backup_info = consistent_backup(
                    db_path, Path(a.backup_dir))
                result = apply_planned(db_path, planned)
                receipt_path = write_apply_receipt(
                    backup_path, db_path, planned, result)
                rep["apply"] = {
                    "performed": True,
                    "backup": backup_info,
                    "backup_path": str(backup_path),
                    "receipt_path": str(receipt_path) if receipt_path else "",
                    "result": result,
                    "idempotency_key": "jev_review_queue.id (news_id)",
                }
                if receipt_path is None:
                    rep["warnings"].append(
                        "apply 回执写入失败（备份仍在：%s）" % backup_path)
            except (ApplyError, sqlite3.Error, OSError) as exc:
                print("[FAIL] --apply 失败（事务已回滚；备份见上）：%s" % exc,
                      file=sys.stderr)
                return 1

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep))
        if rep.get("apply"):
            print("APPLY: %s" % json.dumps(rep["apply"], ensure_ascii=False))
    if a.fail_on_bad and rep["bad_rows"]["n"] > 0:
        print("[FAIL] 存在 %d 条坏行（非法 jev JSON），--fail-on-bad 置 rc=4"
              % rep["bad_rows"]["n"], file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
