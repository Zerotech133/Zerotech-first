# -*- coding: utf-8 -*-
"""news 表 JEV 溯源/失败列迁移（idempotent）。

新增列：
  model      TEXT   -- 判读模型（云端 jev-latest / 本地 qwen3.5:9b）
  judged_at  TEXT   -- 最近一次判读/失败时间
  attempt    INTEGER NOT NULL DEFAULT 0 -- 判读尝试次数（成功+失败）
  last_error TEXT   -- 最近一次失败原因（成功后清空）

设计：迁移只加列，不改旧列/旧行；旧行 model/judged_at 为 NULL 属正常。
生产库执行前先 backup_to() 做一个 sqlite 一致性快照。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

META_COLUMNS = {
    "model": "TEXT",
    "judged_at": "TEXT",
    "attempt": "INTEGER NOT NULL DEFAULT 0",
    "last_error": "TEXT",
}


def columns(conn: sqlite3.Connection) -> set[str]:
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(news)")}


def ensure_news_meta(conn: sqlite3.Connection) -> list[str]:
    """缺列则 ALTER；返回本次新增列名（空=已是最新）。"""
    have = columns(conn)
    added = []
    for name, ddl in META_COLUMNS.items():
        if name in have:
            continue
        conn.execute("ALTER TABLE news ADD COLUMN %s %s" % (name, ddl))
        added.append(name)
    if added:
        conn.commit()
    return added


def backup_to(src: Path, dst_dir: Path) -> Path:
    """sqlite 一致性备份（WAL 下也安全），返回备份路径。"""
    src = Path(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / ("news.db.%s.bak" % time.strftime("%Y%m%d_%H%M%S"))
    s = sqlite3.connect(str(src), timeout=30)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return dst


def ensure_path(path: Path, do_backup: bool = True) -> list[str]:
    path = Path(path)
    if not path.exists():
        return []
    if do_backup:
        backup_to(path, path.parent / "backups")
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        return ensure_news_meta(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parent / "news.db"
    )
    added = ensure_path(p)
    print("news schema: added=%s" % (added or "none"))
