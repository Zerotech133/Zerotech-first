# -*- coding: utf-8 -*-
"""增量落库：SQLite 幂等 upsert + seen 集（逐步增量核心）。

表结构对齐 xueqiu_common.SQLITE_TABLES（xq_status / xq_hot_post / xq_comment），
另增 xq_quote / xq_kline（行情快照 / K线，pysnowball 来源）。
默认库优先复用现有 xueqiu_info.db（若有），否则新建；绝不硬编码 Windows 路径。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

_STATUS_COLS = [
    "status_id", "created_at", "title", "desc", "user_id", "target",
    "view_count", "comment_count", "retweet_count", "like_count",
    "reward_count", "source", "category", "fetched_at",
]
_HOT_COLS = _STATUS_COLS + ["hot_rank", "hot_fallback"]
_COMMENT_COLS = [
    "comment_id", "created_at", "title", "desc", "user_id", "target",
    "view_count", "comment_count", "retweet_count", "like_count",
    "reward_count", "source", "category", "symbol", "fetched_at",
]
_QUOTE_COLS = [
    "symbol", "ts", "open", "high", "low", "close", "volume",
    "amount", "pct_chg", "fetched_at",
]
_KLINE_COLS = [
    "symbol", "date", "open", "high", "low", "close", "volume", "amount",
]

TABLES = {
    "xq_status": (_STATUS_COLS,
                  "(status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, "
                  "desc TEXT, user_id TEXT, target TEXT, view_count INTEGER, "
                  "comment_count INTEGER, retweet_count INTEGER, "
                  "like_count INTEGER, reward_count INTEGER, source TEXT, "
                  "category INTEGER, fetched_at TEXT)"),
    "xq_hot_post": (_HOT_COLS,
                    "(status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, "
                    "desc TEXT, user_id TEXT, target TEXT, view_count INTEGER, "
                    "comment_count INTEGER, retweet_count INTEGER, "
                    "like_count INTEGER, reward_count INTEGER, source TEXT, "
                    "category INTEGER, hot_rank INTEGER, hot_fallback INTEGER, "
                    "fetched_at TEXT)"),
    "xq_comment": (_COMMENT_COLS,
                   "(comment_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, "
                   "desc TEXT, user_id TEXT, target TEXT, view_count INTEGER, "
                   "comment_count INTEGER, retweet_count INTEGER, "
                   "like_count INTEGER, reward_count INTEGER, source TEXT, "
                   "category INTEGER, symbol TEXT, fetched_at TEXT)"),
    "xq_quote": (_QUOTE_COLS,
                 "(symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, "
                  "close REAL, volume REAL, amount REAL, pct_chg REAL, "
                  "fetched_at TEXT, PRIMARY KEY(symbol, ts))"),
    "xq_kline": (_KLINE_COLS,
                 "(symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
                  "close REAL, volume REAL, amount REAL, "
                  "PRIMARY KEY(symbol, date))"),
}


def _default_db_path() -> str:
    """优先复用现有 xueqiu_info.db（quant_backtest/ 下），否则新建到 xq_pump/data。"""
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    legacy = os.path.join(parent, "xueqiu_info.db")
    if os.path.exists(legacy):
        return legacy
    return os.path.join(here, "data", "xq_pump.db")


class Store:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.con = sqlite3.connect(self.db_path)
        self._ensure()

    def _ensure(self) -> None:
        for name, (_cols, ddl) in TABLES.items():
            self.con.execute("CREATE TABLE IF NOT EXISTS %s %s" % (name, ddl))
        self.con.commit()

    # ---- seen 集（增量去重） ----
    def seen_ids(self, table: str, col: str) -> set:
        try:
            return {str(r[0]) for r in self.con.execute(
                "SELECT %s FROM %s" % (col, table))}
        except Exception:
            return set()

    # ---- 通用 upsert ----
    def upsert(self, table: str, rows: Iterable[dict], cols: list) -> int:
        cols = [c for c in cols if c in TABLES.get(table, (cols, ""))[0]]
        if not cols:
            return 0
        ph = ",".join(["?"] * len(cols))
        sql = "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (
            table, ",".join(cols), ph)
        n = 0
        for r in rows:
            vals = [r.get(c) for c in cols]
            self.con.execute(sql, vals)
            n += 1
        self.con.commit()
        return n

    def upsert_posts(self, rows) -> int:
        return self.upsert("xq_status", rows, _STATUS_COLS)

    def upsert_hot(self, rows) -> int:
        return self.upsert("xq_hot_post", rows, _HOT_COLS)

    def upsert_comments(self, rows) -> int:
        return self.upsert("xq_comment", rows, _COMMENT_COLS)

    def upsert_quotes(self, rows) -> int:
        return self.upsert("xq_quote", rows, _QUOTE_COLS)

    def upsert_kline(self, rows) -> int:
        return self.upsert("xq_kline", rows, _KLINE_COLS)

    def count(self, table: str) -> int:
        try:
            return self.con.execute(
                "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        except Exception:
            return 0

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass
