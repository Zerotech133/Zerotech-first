# -*- coding: utf-8 -*-
"""K-QUANT-004: trade_pool 双向反馈列 (feedback_*) 单元测试.

两部分:
  A. set_feedback mock 游标单测 (对齐 test_trade_pool.py 风格, 不连真 DB)
  B. SQLite 模拟迁移幂等验证 (前后两态)

方言说明 (如实): 迁移脚本与 trade_pool.py 均为 PostgreSQL 方言
  (DO $$ / information_schema / TIMESTAMPTZ / NOW())。
  SQLite 无法执行 DO $$ 块与 information_schema, 故 B 部分以 Python 侧
  等价逻辑模拟迁移脚本的幂等语义: 解析脚本中的 ADD COLUMN 行,
  用 PRAGMA table_info 判列存在则跳过 (与脚本 information_schema 判断等价),
  PG→SQLite 类型映射仅用于建表。set_feedback 的 NOW() 在 SQLite 中
  以 CURRENT_TIMESTAMP 等价替换执行。真 PG 上的最终验证须老田批准后执行。
"""
from __future__ import annotations
import os, re, sqlite3, sys
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from portal.quant_mid import trade_pool as tp

_HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATE_SQL = os.path.join(_HERE, "migrate_trade_pool_feedback.sql")

FEEDBACK_COLS = ["feedback_type", "feedback_note", "feedback_score", "feedback_at"]


# ═══ A. set_feedback mock 单测 ═══════════════════════════

@pytest.fixture
def _mock_conn():
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.cursor.return_value = fake_cur
    with patch("portal.quant_mid.trade_pool.psycopg2") as m:
        m.connect.return_value = fake_conn
        yield fake_conn, fake_cur


@pytest.fixture(autouse=True)
def _clear_secrets_cache():
    tp._SECRETS_CACHE.clear()
    tp._SECRETS_CACHE_TS = 0
    for k in list(os.environ):
        if k.startswith("QUANT_DB_"):
            del os.environ[k]
    yield


def test_set_feedback_updates_all_four_columns(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 1
    tp.set_feedback(7, feedback_type="approve",
                    feedback_note="信号有效", feedback_score=0.85)
    sql, args = fake_cur.execute.call_args[0]
    assert "UPDATE trade_pool" in sql
    for col in FEEDBACK_COLS:
        assert col in sql
    # 参数序: type, note, score, id
    assert args == ("approve", "信号有效", 0.85, 7)
    fake_conn.commit.assert_called_once()


def test_set_feedback_none_handling(_mock_conn):
    """空字符串/None → NULL; feedback_at 由 SQL NOW() 写, 不走参数."""
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 1
    tp.set_feedback(1, feedback_type="", feedback_note="", feedback_score=None)
    sql, args = fake_cur.execute.call_args[0]
    assert args == (None, None, None, 1)
    assert "NOW()" in sql


def test_set_feedback_row_not_found_raises(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 0
    with pytest.raises(ValueError, match="not found"):
        tp.set_feedback(999, feedback_type="approve")
    fake_conn.rollback.assert_called()


# ═══ B. SQLite 模拟迁移幂等 ══════════════════════════════

# PG 类型 → SQLite 类型 (仅模拟用)
_PG_TO_SQLITE = {
    "VARCHAR(20)": "TEXT", "VARCHAR(10)": "TEXT",
    "TEXT": "TEXT", "NUMERIC(4,3)": "REAL", "TIMESTAMPTZ": "TEXT",
}


def _parse_add_columns() -> list:
    """解析迁移脚本中的 ADD COLUMN 行 → [(col, sqlite_type)]."""
    with open(MIGRATE_SQL, encoding="utf-8") as f:
        text = f.read()
    out = []
    for m in re.finditer(
            r"ADD COLUMN (\w+)\s+([A-Z]+(?:\(\d+(?:,\d+)?\))?)", text):
        col, pg_type = m.group(1), m.group(2)
        assert pg_type in _PG_TO_SQLITE, f"未覆盖的 PG 类型: {pg_type}"
        out.append((col, _PG_TO_SQLITE[pg_type]))
    assert [c for c, _ in out] == FEEDBACK_COLS, "脚本列集与语义确认不一致"
    return out


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    """等价模拟迁移脚本幂等逻辑: 列已存在(PRAGMA table_info)则跳过."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_pool)")}
    for col, sqlite_type in _parse_add_columns():
        if col not in cols:
            conn.execute(f"ALTER TABLE trade_pool ADD COLUMN {col} {sqlite_type}")


def _create_pre_state(conn: sqlite3.Connection) -> None:
    """旧态 trade_pool (无 feedback_* 列, 取建表脚本核心列)."""
    conn.execute("""
        CREATE TABLE trade_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL DEFAULT '',
            signal TEXT NOT NULL,
            confidence REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_research',
            source_agent TEXT NOT NULL,
            rationale TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT INTO trade_pool (stock_code, signal, confidence, source_agent)
        VALUES ('600000.SH', 'BUY', 0.9, 'guojia_chan_monitor')
    """)
    conn.commit()


def test_migration_adds_exactly_four_columns():
    """前态(无列) → 迁移后四列齐备, 旧行值 NULL."""
    with sqlite3.connect(":memory:") as conn:
        _create_pre_state(conn)
        before = {r[1] for r in conn.execute("PRAGMA table_info(trade_pool)")}
        assert not (set(FEEDBACK_COLS) & before)

        _migrate_sqlite(conn)
        after = {r[1] for r in conn.execute("PRAGMA table_info(trade_pool)")}
        assert set(FEEDBACK_COLS) <= after

        row = conn.execute(
            "SELECT feedback_type, feedback_note, feedback_score, feedback_at "
            "FROM trade_pool WHERE id=1").fetchone()
        assert row == (None, None, None, None)


def test_migration_is_idempotent():
    """重复执行迁移: 列集不变、不报错 (幂等)."""
    with sqlite3.connect(":memory:") as conn:
        _create_pre_state(conn)
        _migrate_sqlite(conn)
        cols_1 = [r[1] for r in conn.execute("PRAGMA table_info(trade_pool)")]
        _migrate_sqlite(conn)  # 第二遍
        cols_2 = [r[1] for r in conn.execute("PRAGMA table_info(trade_pool)")]
        assert cols_1 == cols_2
        assert cols_2.count("feedback_type") == 1  # 无重复列


def test_set_feedback_sql_works_on_migrated_table():
    """迁移后表上 set_feedback 的 UPDATE 路径可用.

    方言替换: NOW() → CURRENT_TIMESTAMP (SQLite 无 NOW()),
    真实 NOW() 语义已在 A 部分 test_set_feedback_none_handling 对 SQL 文本断言。
    """
    with sqlite3.connect(":memory:") as conn:
        _create_pre_state(conn)
        _migrate_sqlite(conn)
        # 与 trade_pool.set_feedback 相同列集的 UPDATE (方言替换 NOW())
        sql = """
            UPDATE trade_pool
            SET feedback_type = ?, feedback_note = ?,
                feedback_score = ?, feedback_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """
        conn.execute(sql, ("calibrate", "对照缠论卖点", 0.7, 1))
        conn.commit()
        row = conn.execute(
            "SELECT feedback_type, feedback_note, feedback_score, "
            "feedback_at IS NOT NULL FROM trade_pool WHERE id=1").fetchone()
        assert row == ("calibrate", "对照缠论卖点", 0.7, 1)


def test_migrate_sql_file_has_approval_header():
    """脚本头必须含「执行须老田批准」纪律标注."""
    with open(MIGRATE_SQL, encoding="utf-8") as f:
        head = f.read(400)
    assert "执行须老田批准" in head
