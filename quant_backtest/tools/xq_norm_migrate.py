# -*- coding: utf-8 -*-
"""xq_status_classified.norm_tickers 幂等迁移（P0-1 落地脚本）。

做什么（2026-10-19 数据标准线拍板）：
  1. ALTER TABLE xq_status_classified ADD COLUMN norm_tickers TEXT
     —— 先 PRAGMA table_info 探测，列不存在才加（原表只增不改）；
  2. 调 xq_ticker_normalize.backfill 全表回填（当前 ~4800 行），
     已有行 UPDATE 新列、原行数据零修改；重复执行零副作用。

生产纪律（分类双实例在跑同库）：
  - WAL 模式（设置失败不阻断，可能已被并发写占住或本就是 WAL）；
  - busy_timeout 30s + backfill 内部 busy 重试 3 次指数退避；
  - 每 200 条 commit 一次的短事务，不持长写锁。

用法：
  python tools/xq_norm_migrate.py                 # 全量迁移
  python tools/xq_norm_migrate.py --dry-run       # 只算不写（零副作用预演）
  python tools/xq_norm_migrate.py --db <path>     # 指定库（默认 data/xueqiu/xueqiu_info.db）
  python tools/xq_norm_migrate.py --verify        # 迁移后 kind 分布一行 SQL 验证
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import xq_ticker_normalize as xtn  # noqa: E402

DEFAULT_DB = HERE.parent / "data" / "xueqiu" / "xueqiu_info.db"


def _set_wal(conn: sqlite3.Connection) -> str:
    """尝试切 WAL；失败不阻断（瞬时 busy/已是 WAL 时 SQLite 自会回落原状）。"""
    try:
        mode = xtn._with_retry(
            lambda: conn.execute("PRAGMA journal_mode=WAL").fetchone()[0],
            retries=3,
            base_delay=0.5,
        )
    except sqlite3.OperationalError:
        mode = "keep-current(failed)"
    return str(mode)


def migrate(db_path, batch_size: int = 200, dry_run: bool = False) -> dict:
    """执行幂等迁移；预演仅以只读连接查询，不改变数据库持久状态。"""
    if dry_run:
        # as_uri() 对空格、问号、井号等路径字符转义；mode=ro 禁止新建缺失库。
        db_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        if dry_run:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            rows = conn.execute(
                "SELECT status_id, tickers FROM %s" % xtn._TABLE  # nosec B608 - 值参数化/内部标识符（已抽查）
            ).fetchall()
            kinds = {}
            for _sid, tickers_raw in rows:
                for it in xtn.normalize_tickers(tickers_raw):
                    kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
            return {
                "db": str(db_path),
                "journal_mode": str(journal_mode),
                "dry_run": True,
                "total": len(rows),
                "updated": 0,
                "skipped": 0,
                "kinds": kinds,
            }
        wal = _set_wal(conn)
        added = xtn.ensure_norm_column(conn)
        report = xtn.backfill(conn, batch_size=batch_size)
        report.update(
            {
                "db": str(db_path),
                "journal_mode": wal,
                "added_column": added,
                "dry_run": False,
            }
        )
        return report
    finally:
        conn.close()


def verify(db_path) -> list:
    """以只读连接验证 kind 分布；缺失库直接失败而不创建。"""
    db_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA query_only=ON")
        return conn.execute(xtn.KIND_DIST_SQL).fetchall()
    finally:
        conn.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """Argparse builder（对齐 xq_classify.build_arg_parser 可测性判例）。"""
    ap = argparse.ArgumentParser(
        description="xq_status_classified norm_tickers 幂等迁移（P0-1）"
    )
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 库路径")
    ap.add_argument(
        "--batch-size", type=int, default=200, help="每批 commit 行数（默认 200）"
    )
    ap.add_argument("--dry-run", action="store_true", help="只算不写（零副作用预演）")
    ap.add_argument(
        "--verify", action="store_true", help="只跑 kind 分布验证 SQL 后退出"
    )
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.verify:
        for kind, n in verify(args.db):
            print("kind_dist %s=%d" % (kind, n))
        return 0
    report = migrate(args.db, batch_size=args.batch_size, dry_run=args.dry_run)
    print("migrate done " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report.get("dry_run"):
        for kind, n in verify(args.db):
            print("kind_dist %s=%d" % (kind, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
