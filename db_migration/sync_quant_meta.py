# -*- coding: utf-8 -*-
r"""sync_quant_meta.py -- SQLite 活跃三库 -> PostgreSQL quant_meta 增量镜像（双跑期）

P1 消费者切换第 1 阶段（MIGRATION_P1_REPORT.md §8.2）：
  SQLite 为主继续写（现网不动）；本脚本周期性把新增行回放到 PG 侧，
  形成 30 天双跑对照。PG 在转正前只是镜像，不是真源。

策略（按表）：
  incr    -- rowid 水位增量（仅追加型大表：news/quotes/tdx_info.stock_*）
  refresh -- 小表全量重灌（ai_briefs/ai_reviews/runs/quant.* 全部六表：
            chanlun_pool 写入端整池重建、nine_dim_* 原地 UPDATE，INSERT 型
            镜像对二者失配，2026-10-17 改判 refresh 并重镜像）
漂移哨兵：每轮比对双侧行数（双向：pg<sqlite=漏新行，pg>sqlite=僵尸行），
          落 meta.sync_drift 表（每表保留最新一条）+ stdout 打印。

用法：python sync_quant_meta.py [--once]   （默认跑一轮退出，供计划任务每 30 分钟调用）
口令：C:\aios\quant_backtest\.env.local 的 XMAP_PG_PASSWORD（utf-8 读取，禁止打印）
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import time

import psycopg2
import psycopg2.extras

# --------------------------------------------------------------------------
CONFIG = {
    "news": (
        r"C:\aios\news_collector\news.db",
        [("news", "incr"), ("quotes", "incr"), ("ai_briefs", "refresh"),
         ("ai_reviews", "refresh"), ("runs", "refresh")],
    ),
    # 2026-10-17 BUG修复：quant.* 六表全部改 refresh。chanlun_pool 写入端会
    # 整池重建（删旧业务键+插新键）、nine_dim_* 分数为原地 UPDATE，rowid 水位
    # + INSERT 型镜像对这两种语义失配（水位只进不退，重建后旧行变僵尸、新行
    # 永不补齐：实测算 PG 92% 为僵尸快照）。万行级小表全量重灌成本可忽略。
    "quant": (
        r"C:\aios\quant_backtest\data\quant.db",
        [("chanlun_pool", "refresh"), ("nine_dim_fc", "refresh"),
         ("nine_dim_fc_meta", "refresh"), ("nine_dim_fina", "refresh"),
         ("nine_dim_rc", "refresh"), ("nine_dim_rc_meta", "refresh")],
    ),
    "tdx_info": (
        r"C:\aios\quant_backtest\data\tdx\tdx_info.db",
        [("stock_news", "incr"), ("stock_notice", "incr"), ("stock_report", "incr")],
    ),
}
ENV_LOCAL = r"C:\aios\quant_backtest\.env.local"
PG_DSN_BASE = dict(host="127.0.0.1", port=5442, dbname="quant_meta", user="quant_user")
BATCH = 2000


def load_pg_password() -> str:
    with io.open(ENV_LOCAL, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("XMAP_PG_PASSWORD="):
                return ln.split("=", 1)[1].strip()
    raise RuntimeError("XMAP_PG_PASSWORD not found in .env.local")


def connect_pg():
    return psycopg2.connect(**PG_DSN_BASE, password=load_pg_password())


def connect_sqlite_ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    return con


def ensure_state_table(pg):
    with pg.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS meta")
        cur.execute("""CREATE TABLE IF NOT EXISTS meta.sync_state (
            schema_name text, table_name text, last_rowid bigint,
            synced_rows bigint, last_sync timestamptz DEFAULT now(),
            PRIMARY KEY (schema_name, table_name))""")
    pg.commit()


def get_state(pg, schema, table):
    with pg.cursor() as cur:
        cur.execute("SELECT last_rowid, synced_rows FROM meta.sync_state "
                    "WHERE schema_name=%s AND table_name=%s", (schema, table))
        r = cur.fetchone()
    return r if r else (0, 0)


def put_state(pg, schema, table, last_rowid, synced):
    with pg.cursor() as cur:
        cur.execute("""INSERT INTO meta.sync_state (schema_name, table_name, last_rowid, synced_rows, last_sync)
                       VALUES (%s,%s,%s,%s,now())
                       ON CONFLICT (schema_name, table_name)
                       DO UPDATE SET last_rowid=%s, synced_rows=%s, last_sync=now()""",
                    (schema, table, last_rowid, synced, last_rowid, synced))
    pg.commit()


def pg_columns(pg, schema, table):
    with pg.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position""",
                    (schema, table))
        cols = [r[0] for r in cur.fetchall()]
    if not cols:
        raise RuntimeError(f"PG table {schema}.{table} missing")
    return cols


def pg_count(pg, schema, table):
    with pg.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')  # 标识符来自内置白名单 CONFIG
        return cur.fetchone()[0]


def insert_rows(pg, schema, table, cols, rows):
    if not rows:
        return
    col_sql = ",".join(f'"{c}"' for c in cols)
    ph = ",".join(["%s"] * len(cols))
    sql = f'INSERT INTO "{schema}"."{table}" ({col_sql}) VALUES ({ph}) ON CONFLICT DO NOTHING'
    psycopg2.extras.execute_batch(pg.cursor(), sql, rows, page_size=BATCH)


def _perm_from(names, cols, schema, table):
    """SQLite 列名 -> PG 列序的置换表（列对齐实装：原先 names 算了没用，
    两侧列序一旦漂移会静默串列，2026-10-17 修复）。"""
    idx = {n: i for i, n in enumerate(names)}
    try:
        return [idx[c] for c in cols]
    except KeyError as e:
        raise RuntimeError(f"{schema}.{table}: SQLite 侧缺列 {e}，无法对齐 PG 列序")


def ensure_drift_table(pg):
    with pg.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS meta.sync_drift (
            schema_name text, table_name text, pg_rows bigint,
            sqlite_rows bigint, detected_at timestamptz DEFAULT now(),
            PRIMARY KEY (schema_name, table_name))""")


def record_drift(pg, schema, table, pgc, sq):
    """docstring 承诺的「记 drift」此前只有 print，静默漏账一天才被发现；
    2026-10-17 起落表（每表保留最新一条）。"""
    with pg.cursor() as cur:
        cur.execute("""INSERT INTO meta.sync_drift (schema_name, table_name, pg_rows, sqlite_rows)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (schema_name, table_name)
                       DO UPDATE SET pg_rows=EXCLUDED.pg_rows,
                                     sqlite_rows=EXCLUDED.sqlite_rows,
                                     detected_at=now()""",
                    (schema, table, pgc, sq))
    pg.commit()


def sync_table(pg, lite, schema, table, mode):
    t0 = time.time()
    cols = pg_columns(pg, schema, table)
    sq_count = lite.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]  # 白名单表名

    if mode == "refresh":
        pg.cursor().execute(f'TRUNCATE "{schema}"."{table}"')  # 白名单表名
        lite_cur = lite.execute(f'SELECT * FROM "{table}"')
        names = [d[0] for d in lite_cur.description]
        perm = _perm_from(names, cols, schema, table)
        rows = [tuple(r[p] for p in perm) for r in lite_cur.fetchall()]
        insert_rows(pg, schema, table, cols, rows)
        if schema == "news" and table == "runs":
            pg.cursor().execute(
                "SELECT setval(pg_get_serial_sequence('news.runs','id'), "
                "COALESCE((SELECT MAX(id) FROM news.runs),0)+1, false)")
        pg.commit()
        put_state(pg, schema, table, sq_count, sq_count)
        pgc = pg_count(pg, schema, table)
        if pgc != sq_count:
            record_drift(pg, schema, table, pgc, sq_count)
        print(f"[refresh] {schema}.{table}: {sq_count} rows ({time.time()-t0:.1f}s)")
        return

    last_rowid, synced_before = get_state(pg, schema, table)
    max_rowid = lite.execute(f"SELECT COALESCE(MAX(rowid),0) FROM \"{table}\"").fetchone()[0]
    if max_rowid < last_rowid:  # 库被重建/VACUUM：水位失效，全量重灌
        print(f"[warn] {schema}.{table}: rowid 回退 ({last_rowid}->{max_rowid})，全量重灌")
        pg.cursor().execute(f'TRUNCATE "{schema}"."{table}"')
        put_state(pg, schema, table, 0, 0)
        last_rowid, synced_before = 0, 0
    if synced_before == 0 and last_rowid == 0 and max_rowid > 0:
        # 首轮水位初始化：P1 快照已在 PG 侧则直接对齐水位；否则全量补灌。
        # 无主键表（tdx_info.*）靠这条路径防重复灌入。
        pgc = pg_count(pg, schema, table)
        if pgc == sq_count:
            put_state(pg, schema, table, max_rowid, sq_count)
            print(f"[init] {schema}.{table}: 快照已同步（pg={pgc}=sqlite），水位对齐 {max_rowid}")
            return
        print(f"[init] {schema}.{table}: pg={pgc} != sqlite={sq_count}，全量重灌")
        pg.cursor().execute(f'TRUNCATE "{schema}"."{table}"')
        cur = lite.execute(f'SELECT * FROM "{table}"')
        total = 0
        while True:
            batch = cur.fetchmany(BATCH)
            if not batch:
                break
            insert_rows(pg, schema, table, cols, batch)
            total += len(batch)
        pg.commit()
        put_state(pg, schema, table, max_rowid, total)
        print(f"[init] {schema}.{table}: 补灌 {total} rows ({time.time()-t0:.1f}s)")
        return
    cur = lite.execute(f'SELECT rowid, * FROM "{table}" WHERE rowid > ? ORDER BY rowid', (last_rowid,))
    names = [d[0] for d in cur.description][1:]  # 去掉 rowid 列
    # 列对齐实装：按 PG 列序置换（原先 names 算了没用，列序漂移会静默串列）
    perm = _perm_from(names, cols, schema, table)
    total = 0
    while True:
        batch = cur.fetchmany(BATCH)
        if not batch:
            break
        rows = [tuple(r[1:][p] for p in perm) for r in batch]
        insert_rows(pg, schema, table, cols, rows)
        total += len(rows)
        last_rowid = batch[-1][0]
    pg.commit()
    put_state(pg, schema, table, last_rowid, synced_before + total)
    pgc = pg_count(pg, schema, table)
    # 双向 drift：pg<sqlite=漏新行；pg>sqlite=僵尸行（chanlun_pool 判例，
    # 此前只认单方向，僵尸 9720 行静默漏报一天）
    if pgc != sq_count:
        record_drift(pg, schema, table, pgc, sq_count)
    drift = "" if pgc == sq_count else f"  DRIFT pg={pgc} vs sqlite={sq_count}"
    print(f"[incr] {schema}.{table}: +{total} rows, pg_total={pgc}, sqlite={sq_count} "
          f"({time.time()-t0:.1f}s){drift}")


def main():
    pg = connect_pg()
    ensure_state_table(pg)
    ensure_drift_table(pg)
    fails = 0
    for schema, (dbpath, tables) in CONFIG.items():
        if not os.path.exists(dbpath):
            print(f"[skip] {dbpath} 不存在")
            fails += 1
            continue
        lite = connect_sqlite_ro(dbpath)
        try:
            for table, mode in tables:
                try:
                    sync_table(pg, lite, schema, table, mode)
                except Exception as e:
                    fails += 1
                    print(f"[FAIL] {schema}.{table}: {type(e).__name__}: {e}")
                    pg.rollback()
        finally:
            lite.close()
    pg.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
