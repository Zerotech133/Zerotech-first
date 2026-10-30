# -*- coding: utf-8 -*-
"""SQLite 数据层 · Phase 1: chanlun_pool 迁移。

设计：
· 单库 data/quant.db，WAL 模式（单写入者串行，并发读不阻塞）
· chanlun_pool 表：扁平结构，主键 (code, type, dt) 即去重键
· 双写期：chanlun_scan.py 同时写 SQLite + JSONL；读取优先 SQLite
· JSONL 保留为 fallback，确认稳定后停写

用法：
  from db import get_conn, init_db, migrate_pool, pool_insert, pool_query_recent
  init_db()                          # 建表 + WAL
  migrate_pool()                     # 从 chanlun_pool.jsonl 导入存量
  pool_insert(row)                   # 插入新信号（INSERT OR IGNORE）
  pool_query_recent(days=90)         # 近 N 天信号（cross_poll 用）
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "quant.db")
POOL_JSONL = os.path.join(HERE, "data", "chanlun_pool.jsonl")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chanlun_pool (
    market       TEXT NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT,
    period       TEXT NOT NULL,
    type         TEXT NOT NULL,
    dt           TEXT NOT NULL,
    price        REAL,
    detect_date  TEXT,
    entry_px     REAL,
    lag          INTEGER,
    first_seen   TEXT,
    source       TEXT,
    bei          INTEGER,
    bei2         INTEGER,
    mbei         INTEGER,
    tbe          INTEGER,
    vv           INTEGER,
    mt           TEXT,
    reg          TEXT,
    PRIMARY KEY (code, type, dt)
);
CREATE INDEX IF NOT EXISTS idx_pool_detect ON chanlun_pool(detect_date);
CREATE INDEX IF NOT EXISTS idx_pool_code   ON chanlun_pool(code);

-- Phase 2: nine_dim_bt_rc_cache 迁移
-- 原 JSON 结构：{_meta:{st}, <code>:{rows:[[...]], fields:[...], err?}}
-- 访问口径恒为 per-code（_rc_at 按 report_date 窗口在 rows 内过滤），
-- 故按 code 存 blob，避免 105MB 全量加载；查询走 SQL，单 code 仅几 KB~百 KB。
CREATE TABLE IF NOT EXISTS nine_dim_rc (
    code        TEXT PRIMARY KEY,
    rows_json   TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    err         INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nine_dim_rc_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Phase 3: nine_dim_bt_fina_cache 迁移
-- 原 JSON 结构：{<code>:{fetched_at, rows:[[...]], fields:[...]}}（无 _meta）
-- per-code blob（同 rc 思路）；fetched_at 单列以支持 30 天 TTL 的 SQL 过滤。
CREATE TABLE IF NOT EXISTS nine_dim_fina (
    code        TEXT PRIMARY KEY,
    fetched_at  TEXT,
    rows_json   TEXT NOT NULL,
    fields_json TEXT NOT NULL
);

-- Phase 4: nine_dim_bt_fc_cache 迁移
-- 原 JSON 结构：{_meta:{st}, <code>:{rows, fields}}（同 rc，但无 err 字段：
-- 拉取异常存空 rows/fields，无 err 标记）。per-code blob + 独立 meta 存 st。
CREATE TABLE IF NOT EXISTS nine_dim_fc (
    code        TEXT PRIMARY KEY,
    rows_json   TEXT NOT NULL,
    fields_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nine_dim_fc_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_conn():
    """获取 SQLite 连接（WAL 模式，外键开启）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表 + 索引（幂等）。"""
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ── 字段映射 ───────────────────────────────────────────────
_POOL_COLS = (
    "market",
    "code",
    "name",
    "period",
    "type",
    "dt",
    "price",
    "detect_date",
    "entry_px",
    "lag",
    "first_seen",
    "source",
    "bei",
    "bei2",
    "mbei",
    "tbe",
    "vv",
    "mt",
    "reg",
)


def _row_to_tuple(r):
    """JSONL 行 → SQLite 插入元组（bool → 0/1，None 保留）。"""

    def conv(v):
        if v is True:
            return 1
        if v is False:
            return 0
        return v

    return tuple(conv(r.get(c) if c != "lag" else r.get(c)) for c in _POOL_COLS)


def _row_from_sqlite(row):
    """SQLite Row → dict（0/1 → bool，与 JSONL 口径一致）。"""

    def conv(v, col):
        if v is None:
            return None
        if col in ("bei", "bei2", "mbei", "tbe", "vv"):
            return bool(v)
        return v

    return {c: conv(row[c], c) for c in _POOL_COLS}


# ── 写入 ───────────────────────────────────────────────────
def pool_insert(conn, row):
    """插入一条信号（INSERT OR IGNORE，主键冲突跳过）。

    双写期：调用方仍同时写 JSONL。返回是否实际插入（1=新, 0=跳过）。
    """
    vals = _row_to_tuple(row)
    placeholders = ",".join("?" * len(_POOL_COLS))
    sql = "INSERT OR IGNORE INTO chanlun_pool (%s) VALUES (%s)" % (
        ",".join(_POOL_COLS),
        placeholders,
    )
    cur = conn.execute(sql, vals)
    return cur.rowcount


def pool_insert_batch(conn, rows):
    """批量插入（事务内，返回新增数）。"""
    n = 0
    for r in rows:
        n += pool_insert(conn, r)
    return n


# ── 读取 ───────────────────────────────────────────────────
def pool_count(conn):
    """总行数。"""
    return conn.execute("SELECT COUNT(*) FROM chanlun_pool").fetchone()[0]


def pool_query_recent(conn, days=90):
    """近 N 天信号，按 code 保留最新（cross_poll._src_chanlun 替代）。

    返回 list[dict]，与 JSONL 行结构一致（bool 字段还原）。
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    # 每个代码取 detect_date 最新的一条
    sql = """
    SELECT p.* FROM chanlun_pool p
    INNER JOIN (
        SELECT code, MAX(detect_date) AS max_dd
        FROM chanlun_pool
        WHERE detect_date >= ?
        GROUP BY code
    ) latest ON p.code = latest.code
             AND p.detect_date = latest.max_dd
    WHERE p.detect_date >= ?
    """
    rows = conn.execute(sql, (cutoff, cutoff)).fetchall()
    return [_row_from_sqlite(r) for r in rows]


def pool_query_all(conn):
    """全量读取（迁移验证用）。"""
    rows = conn.execute("SELECT * FROM chanlun_pool").fetchall()
    return [_row_from_sqlite(r) for r in rows]


def pool_query_by_key(conn, code, sig_type, dt):
    """按主键查单条（验证用）。"""
    row = conn.execute(
        "SELECT * FROM chanlun_pool WHERE code=? AND type=? AND dt=?",
        (code, sig_type, dt),
    ).fetchone()
    return _row_from_sqlite(row) if row else None


# ── 迁移 ───────────────────────────────────────────────────
def migrate_pool(verbose=True):
    """从 chanlun_pool.jsonl 导入存量到 SQLite。

    流程：init_db → 逐行读 JSONL → INSERT OR IGNORE → 验证行数。
    幂等：可重复跑，主键冲突跳过。
    """
    init_db()
    if not os.path.exists(POOL_JSONL):
        if verbose:
            print("migrate: %s not found, skip" % POOL_JSONL)
        return 0
    conn = get_conn()
    total = 0
    inserted = 0
    try:
        conn.execute("BEGIN")
        with open(POOL_JSONL, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                total += 1
                inserted += pool_insert(conn, r)
        conn.commit()
        db_count = pool_count(conn)
        if verbose:
            print(
                "migrate: jsonl=%d, inserted=%d, db=%d (match=%s)"
                % (total, inserted, db_count, "YES" if db_count == total else "NO")
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return inserted


# ── Phase 2: nine_dim_bt_rc_cache 迁移 ──────────────────────
ND_RC_JSON = os.path.join(HERE, "data", "nine_dim_bt_rc_cache.json")


def nd_rc_get_st(conn):
    """读 meta.st（窗口起算键，月末取整防逐日失效）。None=未设置/表空。"""
    row = conn.execute("SELECT value FROM nine_dim_rc_meta WHERE key='st'").fetchone()
    return row[0] if row else None


def nd_rc_set_st(conn, st):
    """upsert meta.st。调用方负责在 st 变化时先 nd_rc_clear。"""
    conn.execute(
        "INSERT INTO nine_dim_rc_meta(key, value) VALUES('st', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (st,),
    )


def nd_rc_clear(conn):
    """清空 nine_dim_rc（st 失效时重建用）。"""
    conn.execute("DELETE FROM nine_dim_rc")


def nd_rc_count(conn):
    return conn.execute("SELECT COUNT(*) FROM nine_dim_rc").fetchone()[0]


def nd_rc_upsert(conn, code, rows, fields, err=0):
    """写入/覆盖单 code 的 rec（INSERT OR REPLACE）。
    rows: list[list]（positional，与 fields 对齐）；fields: list[str]。
    """
    conn.execute(
        "INSERT OR REPLACE INTO nine_dim_rc(code, rows_json, fields_json, err) "
        "VALUES(?, ?, ?, ?)",
        (
            code,
            json.dumps(rows, ensure_ascii=False),
            json.dumps(fields, ensure_ascii=False),
            1 if err else 0,
        ),
    )


def nd_rc_query(conn, code):
    """单 code 懒加载 → {rows, fields, err?} 或 None。
    与原 JSON cache[code] 结构一致，_rc_at 直接消费。
    """
    row = conn.execute(
        "SELECT rows_json, fields_json, err FROM nine_dim_rc WHERE code=?", (code,)
    ).fetchone()
    if row is None:
        return None
    rec = {
        "rows": json.loads(row["rows_json"]),
        "fields": json.loads(row["fields_json"]),
    }
    if row["err"]:
        rec["err"] = 1
    return rec


def nd_rc_missing(conn, codes):
    """返回 codes 中不在表里的子集（增量拉取清单）。"""
    if not codes:
        return []
    # 单 code 直接查
    if len(codes) == 1:
        c = codes[0]
        ex = conn.execute("SELECT 1 FROM nine_dim_rc WHERE code=?", (c,)).fetchone()
        return [] if ex else [c]
    # 多 code 分批 IN（SQLite 默认 999 变量上限，分片 500 保险）
    present = set()
    for i in range(0, len(codes), 500):
        chunk = codes[i : i + 500]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            "SELECT code FROM nine_dim_rc WHERE code IN (%s)" % ph, chunk  # nosec B608 - 值参数化/内部标识符（已抽查）
        ):
            present.add(r["code"])
    return [c for c in codes if c not in present]


def migrate_nd_rc(verbose=True):
    """从 nine_dim_bt_rc_cache.json 导入存量到 SQLite。

    流程：init_db → 读 JSON → 写 meta.st + 逐 code upsert → 验证行数。
    幂等：可重复跑（INSERT OR REPLACE）。105MB JSON 一次性读取后释放。
    """
    init_db()
    if not os.path.exists(ND_RC_JSON):
        if verbose:
            print("migrate_nd_rc: %s not found, skip" % ND_RC_JSON)
        return 0
    if verbose:
        print("migrate_nd_rc: loading %s ..." % ND_RC_JSON, flush=True)
    with open(ND_RC_JSON, encoding="utf-8") as f:
        cache = json.load(f)
    st = (cache.get("_meta") or {}).get("st")
    codes = [k for k in cache if k != "_meta"]
    conn = get_conn()
    n = 0
    try:
        conn.execute("BEGIN")
        if st:
            nd_rc_set_st(conn, st)
        for c in codes:
            rec = cache[c]
            nd_rc_upsert(
                conn,
                c,
                rec.get("rows") or [],
                rec.get("fields") or [],
                rec.get("err", 0),
            )
            n += 1
            if verbose and n % 1000 == 0:
                print("  nd_rc %d/%d" % (n, len(codes)), flush=True)
        conn.commit()
        db_count = nd_rc_count(conn)
        if verbose:
            print(
                "migrate_nd_rc: json_codes=%d, inserted=%d, db=%d (match=%s)"
                % (len(codes), n, db_count, "YES" if db_count == len(codes) else "NO")
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    del cache  # 显式释放 105MB
    return n


# ── Phase 3: nine_dim_bt_fina_cache 迁移 ────────────────────
ND_FINA_JSON = os.path.join(HERE, "data", "nine_dim_bt_fina_cache.json")


def nd_fina_count(conn):
    return conn.execute("SELECT COUNT(*) FROM nine_dim_fina").fetchone()[0]


def nd_fina_upsert(conn, code, rows, fields, fetched_at=None):
    """写入/覆盖单 code 的 fina rec（INSERT OR REPLACE）。
    fetched_at: ISO 字符串（30 天 TTL 依据），None 保留。
    """
    conn.execute(
        "INSERT OR REPLACE INTO nine_dim_fina(code, fetched_at, rows_json, fields_json) "
        "VALUES(?, ?, ?, ?)",
        (
            code,
            fetched_at,
            json.dumps(rows, ensure_ascii=False),
            json.dumps(fields, ensure_ascii=False),
        ),
    )


def nd_fina_query(conn, code):
    """单 code 懒加载 → {fetched_at, rows, fields} 或 None。
    与原 JSON cache[code] 结构一致，_fina_asof 直接消费（仅用 rows/fields）。
    """
    row = conn.execute(
        "SELECT fetched_at, rows_json, fields_json FROM nine_dim_fina WHERE code=?",
        (code,),
    ).fetchone()
    if row is None:
        return None
    return {
        "fetched_at": row["fetched_at"],
        "rows": json.loads(row["rows_json"]),
        "fields": json.loads(row["fields_json"]),
    }


def nd_fina_need(conn, codes, cutoff_iso):
    """返回 codes 中「缺失 或 fetched_at < cutoff_iso」的子集
    （cutoff_iso = now-30天；fresh = present AND fetched_at>=cutoff）。"""
    if not codes:
        return []
    fresh = set()
    for i in range(0, len(codes), 500):
        chunk = codes[i : i + 500]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            "SELECT code FROM nine_dim_fina WHERE code IN (%s) AND fetched_at>=?" % ph,  # nosec B608 - 值参数化/内部标识符（已抽查）
            chunk + [cutoff_iso],
        ):
            fresh.add(r["code"])
    return [c for c in codes if c not in fresh]


def migrate_nd_fina(verbose=True):
    """从 nine_dim_bt_fina_cache.json 导入存量到 SQLite。

    流程：init_db → 读 JSON → 逐 code upsert（含 fetched_at）→ 验证行数。
    幂等：可重复跑（INSERT OR REPLACE）。57.6MB JSON 一次性读取后释放。
    """
    init_db()
    if not os.path.exists(ND_FINA_JSON):
        if verbose:
            print("migrate_nd_fina: %s not found, skip" % ND_FINA_JSON)
        return 0
    if verbose:
        print("migrate_nd_fina: loading %s ..." % ND_FINA_JSON, flush=True)
    with open(ND_FINA_JSON, encoding="utf-8") as f:
        cache = json.load(f)
    codes = list(cache.keys())  # 无 _meta，全部为 code
    conn = get_conn()
    n = 0
    try:
        conn.execute("BEGIN")
        for c in codes:
            rec = cache[c]
            nd_fina_upsert(
                conn,
                c,
                rec.get("rows") or [],
                rec.get("fields") or [],
                rec.get("fetched_at"),
            )
            n += 1
            if verbose and n % 1000 == 0:
                print("  nd_fina %d/%d" % (n, len(codes)), flush=True)
        conn.commit()
        db_count = nd_fina_count(conn)
        if verbose:
            print(
                "migrate_nd_fina: json_codes=%d, inserted=%d, db=%d (match=%s)"
                % (len(codes), n, db_count, "YES" if db_count == len(codes) else "NO")
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    del cache
    return n


# ── Phase 4: nine_dim_bt_fc_cache 迁移 ──────────────────────
ND_FC_JSON = os.path.join(HERE, "data", "nine_dim_bt_fc_cache.json")


def nd_fc_get_st(conn):
    """读 nine_dim_fc_meta.st。None=未设置/表空。"""
    row = conn.execute("SELECT value FROM nine_dim_fc_meta WHERE key='st'").fetchone()
    return row[0] if row else None


def nd_fc_set_st(conn, st):
    conn.execute(
        "INSERT INTO nine_dim_fc_meta(key, value) VALUES('st', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (st,),
    )


def nd_fc_clear(conn):
    conn.execute("DELETE FROM nine_dim_fc")


def nd_fc_count(conn):
    return conn.execute("SELECT COUNT(*) FROM nine_dim_fc").fetchone()[0]


def nd_fc_upsert(conn, code, rows, fields):
    """写入/覆盖单 code 的 fc rec（INSERT OR REPLACE）。无 err 字段。"""
    conn.execute(
        "INSERT OR REPLACE INTO nine_dim_fc(code, rows_json, fields_json) "
        "VALUES(?, ?, ?)",
        (
            code,
            json.dumps(rows, ensure_ascii=False),
            json.dumps(fields, ensure_ascii=False),
        ),
    )


def nd_fc_query(conn, code):
    """单 code 懒加载 → {rows, fields} 或 None（与原 JSON cache[code] 一致）。"""
    row = conn.execute(
        "SELECT rows_json, fields_json FROM nine_dim_fc WHERE code=?", (code,)
    ).fetchone()
    if row is None:
        return None
    return {
        "rows": json.loads(row["rows_json"]),
        "fields": json.loads(row["fields_json"]),
    }


def nd_fc_missing(conn, codes):
    """返回 codes 中不在表里的子集。"""
    if not codes:
        return []
    if len(codes) == 1:
        c = codes[0]
        ex = conn.execute("SELECT 1 FROM nine_dim_fc WHERE code=?", (c,)).fetchone()
        return [] if ex else [c]
    present = set()
    for i in range(0, len(codes), 500):
        chunk = codes[i : i + 500]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            "SELECT code FROM nine_dim_fc WHERE code IN (%s)" % ph, chunk  # nosec B608 - 值参数化/内部标识符（已抽查）
        ):
            present.add(r["code"])
    return [c for c in codes if c not in present]


def migrate_nd_fc(verbose=True):
    """从 nine_dim_bt_fc_cache.json 导入存量到 SQLite。

    流程：init_db → 读 JSON → 写 meta.st + 逐 code upsert → 验证行数。
    幂等：可重复跑（INSERT OR REPLACE）。
    """
    init_db()
    if not os.path.exists(ND_FC_JSON):
        if verbose:
            print("migrate_nd_fc: %s not found, skip" % ND_FC_JSON)
        return 0
    if verbose:
        print("migrate_nd_fc: loading %s ..." % ND_FC_JSON, flush=True)
    with open(ND_FC_JSON, encoding="utf-8") as f:
        cache = json.load(f)
    st = (cache.get("_meta") or {}).get("st")
    codes = [k for k in cache if k != "_meta"]
    conn = get_conn()
    n = 0
    try:
        conn.execute("BEGIN")
        if st:
            nd_fc_set_st(conn, st)
        for c in codes:
            rec = cache[c]
            nd_fc_upsert(conn, c, rec.get("rows") or [], rec.get("fields") or [])
            n += 1
            if verbose and n % 1000 == 0:
                print("  nd_fc %d/%d" % (n, len(codes)), flush=True)
        conn.commit()
        db_count = nd_fc_count(conn)
        if verbose:
            print(
                "migrate_nd_fc: json_codes=%d, inserted=%d, db=%d (match=%s)"
                % (len(codes), n, db_count, "YES" if db_count == len(codes) else "NO")
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    del cache
    return n


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "nd_rc":
        print("=== SQLite Phase 2: nine_dim_bt_rc_cache migration ===")
        n = migrate_nd_rc()
        print("done: %d codes inserted" % n)
    elif len(sys.argv) > 1 and sys.argv[1] == "nd_fina":
        print("=== SQLite Phase 3: nine_dim_bt_fina_cache migration ===")
        n = migrate_nd_fina()
        print("done: %d codes inserted" % n)
    elif len(sys.argv) > 1 and sys.argv[1] == "nd_fc":
        print("=== SQLite Phase 4: nine_dim_bt_fc_cache migration ===")
        n = migrate_nd_fc()
        print("done: %d codes inserted" % n)
    else:
        print("=== SQLite Phase 1: chanlun_pool migration ===")
        n = migrate_pool()
        print("done: %d rows inserted" % n)
