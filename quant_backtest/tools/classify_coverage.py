# -*- coding: utf-8 -*-
"""雪球分类管道覆盖进度快照（coverage-dev 2026-10-18）。

只读查库 data/xueqiu/xueqiu_info.db（file:...?mode=ro uri）+ 读进度日志尾行，
落快照 C:/aios/automation_runs/xq_classify/coverage_snapshot.json 供门户
/api/xq/intel/coverage 纯文件消费（门户不进库契约：门户只读 JSON 不碰 SQLite）。

快照字段：
    generated_at    ISO 时间
    total           xq_status 总行数（存量 2012-2023）
    classified      xq_status_classified 总行数
    coverage_pct    classified/total*100，1 位小数
    v13_count / v13_pct   prompt_version LIKE 'v1.3%' 行数及占 classified 比
    with_tickers    tickers != '[]' 行数
    parse_ok_count  parse_ok=1 行数
    sprint_last     backfill_sprint.log 尾 1 行（文件不存在则 null）
    status_last     xq_classify_status.log 尾 1 行（同样容错）

日志可能是 GBK/UTF-8 混合编码：按 bytes 读尾段后 replace 解码容错，
保证任何脏字节都不致快照失败。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "xueqiu" / "xueqiu_info.db"
SNAPSHOT_PATH = Path(r"C:/aios/automation_runs/xq_classify/coverage_snapshot.json")
LOG_DIR = Path(r"C:/aios/automation_runs/xq_classify")
SPRINT_LOG = LOG_DIR / "backfill_sprint.log"
STATUS_LOG = LOG_DIR / "xq_classify_status.log"


def _last_log_line(path: Path, tail_bytes: int = 4096):
    """读日志文件尾 1 行（bytes 级，GBK/UTF-8 混合容错）。

    文件不存在/读取异常返回 None（进度行是辅助信息，缺了不挡快照）。
    decode 用 errors='replace'：任何脏字节降级为 U+FFFD 而非抛异常。
    """
    try:
        size = path.stat().st_size
        if size <= 0:
            return None
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            return None
        return lines[-1].decode("utf-8", errors="replace").strip()
    except OSError:
        return None


def build_snapshot() -> dict:
    """构建覆盖进度快照（只读，任何单点失败都以 0/null 降级不抛出）。"""
    snap = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": 0,
        "classified": 0,
        "coverage_pct": 0.0,
        "v13_count": 0,
        "v13_pct": 0.0,
        "with_tickers": 0,
        "parse_ok_count": 0,
        "sprint_last": _last_log_line(SPRINT_LOG),
        "status_last": _last_log_line(STATUS_LOG),
    }
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.as_posix(), uri=True)
        try:
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM xq_status")
            snap["total"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM xq_status_classified")
            snap["classified"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM xq_status_classified WHERE prompt_version LIKE 'v1.3%'"
            )
            snap["v13_count"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM xq_status_classified WHERE tickers != '[]'"
            )
            snap["with_tickers"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM xq_status_classified WHERE parse_ok = 1")
            snap["parse_ok_count"] = cur.fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:  # noqa: BLE001 — 库级失败 fail-visible 记入快照
        snap["error"] = "db read failed: %r" % e
    if snap["total"] > 0:
        snap["coverage_pct"] = round(100.0 * snap["classified"] / snap["total"], 1)
    if snap["classified"] > 0:
        snap["v13_pct"] = round(100.0 * snap["v13_count"] / snap["classified"], 1)
    return snap


def main() -> int:
    snap = build_snapshot()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    print("snapshot -> %s" % SNAPSHOT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
