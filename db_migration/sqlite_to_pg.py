# -*- coding: utf-8 -*-
"""sqlite_to_pg.py -- SQLite -> PostgreSQL (quant_meta) snapshot migration tool.

P1 scope:
  1. Snapshot each active SQLite source via the sqlite3 backup API (safe for
     live databases, never a raw file copy).
  2. Introspect the snapshot schema (tables, columns, PKs, indexes) and
     generate PostgreSQL DDL into a dedicated schema per source database.
  3. Bulk-load data with psycopg2 COPY (copy_expert) in row-chunks.
  4. Validate: row counts on every table + deep checks (PK min/max/distinct,
     sampled field comparison, full ordered streaming compare for PK-less
     tables).

Idempotent: every run DROPs and recreates the target schema (CASCADE).

Usage:
  python -X utf8 sqlite_to_pg.py migrate   [--only news,quant,tdx_info]
  python -X utf8 sqlite_to_pg.py validate  [--only news,quant,tdx_info]

Type mapping (SQLite decl type -> PostgreSQL):
  INTEGER (single-col PK, rowid alias) -> bigserial (PK kept)
  INTEGER                              -> bigint
  TEXT / CHAR / CLOB                   -> text
  REAL / FLOA / DOUB                   -> double precision
  BLOB / empty decl                    -> bytea / text fallback
  other (NUMERIC etc.)                 -> double precision
Composite / non-integer PKs -> text/bigint PRIMARY KEY (...).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, Generator, List, Optional, Sequence

import psycopg2
import psycopg2.extensions

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
HOME = r"C:\aios"
WORK_DIR = os.path.join(HOME, "db_migration")
SNAPSHOT_DIR = os.path.join(WORK_DIR, "snapshots")
ENV_FILE = os.path.join(HOME, r"quant_backtest\.env.local")
MANIFEST_PATH = os.path.join(WORK_DIR, "manifest_p1.json")

PG_HOST = "127.0.0.1"
PG_PORT = 5442
PG_USER = "quant_user"
PG_DB = "quant_meta"
PG_ADMIN_DB = "postgres"

COPY_CHUNK_ROWS = 50_000     # rows per copy_expert call (single tx per table)
SAMPLE_ROWS = 1000           # rows for sampled field comparison
BUSY_TIMEOUT_MS = 60_000     # sqlite busy timeout for snapshotting
SNAPSHOT_RETRIES = 8
SNAPSHOT_RETRY_WAIT = 5.0    # seconds

# source key -> (sqlite file, target pg schema, deep-check tables)
SOURCES: Dict[str, Dict[str, Any]] = {
    "news": {
        "db": os.path.join(HOME, r"news_collector\news.db"),
        "schema": "news",
        "deep_tables": ["news", "quotes"],
    },
    "quant": {
        "db": os.path.join(HOME, r"quant_backtest\data\quant.db"),
        "schema": "quant",
        "deep_tables": ["chanlun_pool", "nine_dim_fina"],
    },
    "tdx_info": {
        "db": os.path.join(HOME, r"quant_backtest\data\tdx\tdx_info.db"),
        "schema": "tdx_info",
        "deep_tables": ["stock_news", "stock_report"],
    },
}


# --------------------------------------------------------------------------
# PG connection helpers
# --------------------------------------------------------------------------
def read_pg_password() -> str:
    """Read XMAP_PG_PASSWORD from .env.local. Never print the value."""
    with open(ENV_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("XMAP_PG_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("XMAP_PG_PASSWORD not found in %s" % ENV_FILE)


def pg_connect(dbname: str = PG_DB) -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER,
        password=read_pg_password(), dbname=dbname, sslmode="disable",
        connect_timeout=10,
    )


def ensure_database() -> None:
    """Create quant_meta with C collation (byte order == SQLite BINARY),
    which makes ordered-stream deep validation deterministic."""
    conn = pg_connect(PG_ADMIN_DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (PG_DB,))
    if cur.fetchone():
        print(f"[pg] database {PG_DB} already exists")
    else:
        cur.execute(
            'CREATE DATABASE "%s" OWNER "%s" TEMPLATE template0 '
            "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'" % (PG_DB, PG_USER)
        )
        print(f"[pg] created database {PG_DB} (LC_COLLATE=C)")
    cur.close()
    conn.close()


# --------------------------------------------------------------------------
# Snapshot (sqlite3 backup API)
# --------------------------------------------------------------------------
def make_snapshot(source_key: str, db_path: str) -> str:
    """Snapshot a (possibly live) SQLite db using the online backup API.

    Retries on 'database is locked' -- scheduled jobs may hold the db.
    """
    snap_path = os.path.join(SNAPSHOT_DIR, "%s_snap.db" % source_key)
    for attempt in range(1, SNAPSHOT_RETRIES + 1):
        try:
            src = sqlite3.connect(
                "file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True,
                timeout=BUSY_TIMEOUT_MS / 1000.0,
            )
            src.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
            dst = sqlite3.connect(snap_path)
            src.backup(dst)          # online backup API, lock-aware
            dst.close()
            src.close()
            size_mb = os.path.getsize(snap_path) / 1024 / 1024
            print(f"[snap] {source_key}: {snap_path} ({size_mb:.1f} MB)")
            return snap_path
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < SNAPSHOT_RETRIES:
                print(f"[snap] {source_key}: locked (attempt {attempt}), "
                      f"retrying in {SNAPSHOT_RETRY_WAIT}s ...")
                time.sleep(SNAPSHOT_RETRY_WAIT)
                continue
            raise
    raise RuntimeError("snapshot failed for %s" % source_key)


# --------------------------------------------------------------------------
# Schema introspection + DDL generation
# --------------------------------------------------------------------------
def _map_type(decl: str) -> str:
    """SQLite declared type -> PostgreSQL type (per SQLite affinity rules)."""
    d = (decl or "").upper()
    if "INT" in d:
        return "bigint"
    if "CHAR" in d or "CLOB" in d or "TEXT" in d:
        return "text"
    if "BLOB" in d:
        return "bytea"
    if "REAL" in d or "FLOA" in d or "DOUB" in d:
        return "double precision"
    if d == "":
        return "text"          # no affinity declared
    return "double precision"  # NUMERIC etc.


def introspect(snapshot: str) -> List[Dict[str, Any]]:
    """Return table metadata dicts from the snapshot."""
    con = sqlite3.connect("file:%s?mode=ro" % snapshot.replace("\\", "/"),
                          uri=True)
    con.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    tables: List[Dict[str, Any]] = []
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for tname in names:
        cols = con.execute('PRAGMA table_info("%s")' % tname).fetchall()
        # cols: cid, name, type, notnull, dflt_value, pk
        pk_cols = [c[1] for c in sorted(
            (c for c in cols if c[5] > 0), key=lambda c: c[5])]
        is_rowid_alias = (
            len(pk_cols) == 1
            and next(c for c in cols if c[1] == pk_cols[0])[2].upper().strip()
            == "INTEGER"
        )
        indexes = []
        for iname, isql in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
                (tname,)):
            m = re.search(r"\((.*)\)", isql, re.S)
            idx_cols = m.group(1) if m else ""
            indexes.append({"name": iname, "cols": idx_cols})
        tables.append({
            "name": tname,
            "columns": [
                {"name": c[1], "decl": c[2], "notnull": bool(c[3]),
                 "dflt": c[4]} for c in cols],
            "pk_cols": pk_cols,
            "rowid_alias": is_rowid_alias,
            "indexes": indexes,
        })
    con.close()
    return tables


def qident(name: str) -> str:
    return '"%s"' % name.replace('"', '""')


def build_ddl(schema: str, table: Dict[str, Any]) -> List[str]:
    """Generate PG DDL statements (CREATE TABLE + indexes) for one table."""
    stmts: List[str] = []
    fq = f"{qident(schema)}.{qident(table['name'])}"
    pk = table["pk_cols"]
    col_defs: List[str] = []
    insert_cols: List[str] = []
    for c in table["columns"]:
        name = c["name"]
        if table["rowid_alias"] and name == pk[0]:
            # INTEGER PRIMARY KEY [AUTOINCREMENT] == rowid alias -> bigserial
            col_defs.append(f"{qident(name)} bigserial NOT NULL")
            continue
        col_defs.append(f"{qident(name)} {_map_type(c['decl'])}"
                        + (" NOT NULL" if c["notnull"] else ""))
        if c["dflt"] is not None:
            col_defs[-1] += f" DEFAULT {c['dflt']}"
        insert_cols.append(name)
    if pk:
        col_defs.append("PRIMARY KEY (%s)" %
                        ", ".join(qident(p) for p in pk))
    stmts.append(f"CREATE TABLE {fq} (\n    "
                 + ",\n    ".join(col_defs) + "\n)")
    for idx in table["indexes"]:
        # PG indexes live in the table's schema; name must be unqualified
        stmts.append(
            f"CREATE INDEX {qident(idx['name'])} ON {fq} ({idx['cols']})")
    table["_insert_cols"] = insert_cols
    return stmts


def recreate_schema(pg: psycopg2.extensions.connection, schema: str) -> None:
    cur = pg.cursor()
    cur.execute(f"DROP SCHEMA IF EXISTS {qident(schema)} CASCADE")
    cur.execute(f"CREATE SCHEMA {qident(schema)}")
    pg.commit()
    cur.close()
    print(f"[pg] schema {schema} recreated")


# --------------------------------------------------------------------------
# Data load (COPY)
# --------------------------------------------------------------------------
def _esc(v: Any) -> str:
    """Encode one value for COPY ... FORMAT text."""
    if v is None:
        return "\\N"
    if isinstance(v, bytes):
        return "\\x" + v.hex()
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v == float("inf"):
            return "Infinity"
        if v == float("-inf"):
            return "-Infinity"
    s = str(v)
    if "\\" in s:
        s = s.replace("\\", "\\\\")
    if "\t" in s:
        s = s.replace("\t", "\\t")
    if "\n" in s:
        s = s.replace("\n", "\\n")
    if "\r" in s:
        s = s.replace("\r", "\\r")
    return s


class _GenFile(io.RawIOBase):
    """File-like adapter: streams a generator of bytes into copy_expert."""

    def __init__(self, gen: Generator[bytes, None, None]) -> None:
        self._gen = gen
        self._buf = b""

    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def read(self, n: int = -1) -> bytes:
        while n is None or n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._gen)
            except StopIteration:
                break
        if n is None or n < 0:
            out, self._buf = self._buf, b""
            return out
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


def load_table(pg: psycopg2.extensions.connection, schema: str,
               table: Dict[str, Any], snapshot: str) -> int:
    """COPY all rows from the snapshot table into PG. Returns row count."""
    tname = table["name"]
    cols = table["_insert_cols"]
    col_list = ", ".join(qident(c) for c in cols)
    fq = f"{qident(schema)}.{qident(tname)}"
    sel = ", ".join(qident(c) for c in cols)
    src = sqlite3.connect("file:%s?mode=ro" % snapshot.replace("\\", "/"),
                          uri=True)
    src.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)

    def rows() -> Generator[bytes, None, None]:
        cur = src.execute(f"SELECT {sel} FROM {qident(tname)}")
        while True:
            batch = cur.fetchmany(COPY_CHUNK_ROWS)
            if not batch:
                break
            yield ("\n".join(
                "\t".join(_esc(v) for v in row) for row in batch)
                + "\n").encode("utf-8")
        cur.close()

    stream = _GenFile(rows())
    cur = pg.cursor()
    copy_sql = f"COPY {fq} ({col_list}) FROM STDIN"
    try:
        cur.copy_expert(copy_sql, stream)
        pg.commit()
    except Exception:
        pg.rollback()
        raise
    finally:
        cur.close()
        src.close()

    # row count + keep serial sequences in sync for bigserial tables
    src2 = sqlite3.connect("file:%s?mode=ro" % snapshot.replace("\\", "/"),
                           uri=True)
    n = src2.execute(f"SELECT COUNT(*) FROM {qident(tname)}").fetchone()[0]
    src2.close()
    if table["rowid_alias"]:
        cur = pg.cursor()
        pk = table["pk_cols"][0]
        # identifiers here are validated lowercase names, safe to interpolate
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{schema}.{tname}', "
            f"'{pk}'), GREATEST((SELECT COALESCE(MAX(\"{pk}\"), 0) "
            f"FROM \"{schema}\".\"{tname}\") + 1, 1), false)")
        cur.close()
        pg.commit()
    return n


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _norm(v: Any) -> Any:
    """Normalize a value for cross-engine comparison."""
    if isinstance(v, bytes):
        return v.hex()
    return v


def validate_counts(pg: psycopg2.extensions.connection, schema: str,
                    tables: List[Dict[str, Any]], snapshot: str
                    ) -> List[Dict[str, Any]]:
    src = sqlite3.connect("file:%s?mode=ro" % snapshot.replace("\\", "/"),
                          uri=True)
    cur = pg.cursor()
    results = []
    for t in tables:
        tname = t["name"]
        fq = f"{qident(schema)}.{qident(tname)}"
        n_src = src.execute(
            f"SELECT COUNT(*) FROM {qident(tname)}").fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {fq}")
        n_pg = cur.fetchone()[0]
        results.append({"table": tname, "sqlite": n_src, "pg": n_pg,
                        "match": n_src == n_pg})
    cur.close()
    src.close()
    return results


def _pk_stats(con_sql: sqlite3.Connection, pg, schema: str,
              t: Dict[str, Any]) -> Dict[str, Any]:
    """MIN/MAX/DISTINCT stats over the PK columns, both sides."""
    tname = t["name"]
    pk = t["pk_cols"]
    pkq = ", ".join(qident(p) for p in pk)
    # multi-col COUNT(DISTINCT) is not valid SQLite -> scalar subquery form
    distinct = (f"(SELECT COUNT(*) FROM (SELECT DISTINCT {pkq} "
                f"FROM {qident(tname)}) d)")
    fq = f"{qident(schema)}.{qident(tname)}"
    fq_d = (f"(SELECT COUNT(*) FROM (SELECT DISTINCT {pkq} FROM {fq}) d)")
    sql_s = (f"SELECT COUNT(*), {distinct}, "
             + ", ".join(f"MIN({qident(p)}), MAX({qident(p)})" for p in pk)
             + f" FROM {qident(tname)}")
    sql_p = (f"SELECT COUNT(*), {fq_d}, "
             + ", ".join(f"MIN({qident(p)}), MAX({qident(p)})" for p in pk)
             + f" FROM {fq}")
    row_s = con_sql.execute(sql_s).fetchone()
    cur = pg.cursor()
    cur.execute(sql_p)
    row_p = cur.fetchone()
    cur.close()
    ok = _norm(row_s) == _norm(row_p)
    return {"table": tname, "match": ok,
            "sqlite": _norm(row_s), "pg": _norm(row_p)}


def _sample_compare(con_sql: sqlite3.Connection, pg, schema: str,
                    t: Dict[str, Any], n: int = SAMPLE_ROWS
                    ) -> Dict[str, Any]:
    """Sampled field-by-field comparison for PK tables (stride over rowid)."""
    tname = t["name"]
    cols = [c["name"] for c in t["columns"]]
    pk = t["pk_cols"]
    fq = f"{qident(schema)}.{qident(tname)}"
    total = con_sql.execute(
        f"SELECT COUNT(*) FROM {qident(tname)}").fetchone()[0]
    stride = max(1, total // max(1, n))
    sel = ", ".join(qident(c) for c in cols)
    sampled = con_sql.execute(
        f"SELECT {sel} FROM {qident(tname)} "
        f"WHERE rowid % {stride} = 0 LIMIT {n}").fetchall()
    if not pk:
        return {"table": tname, "compared": 0, "mismatch": 0, "match": True,
                "note": "no pk, covered by ordered stream compare"}
    cur = pg.cursor()
    where = " AND ".join(f"{qident(p)} = %s" for p in pk)
    pq = f"SELECT {sel} FROM {fq} WHERE {where}"
    mismatch = 0
    compared = 0
    for row in sampled:
        key = tuple(_norm(v) for v in
                    (row[cols.index(p)] for p in pk))
        cur.execute(pq, key)
        got = cur.fetchone()
        exp = tuple(_norm(v) for v in row)
        compared += 1
        if got is None or tuple(_norm(v) for v in got) != exp:
            mismatch += 1
            if mismatch <= 3:
                print(f"    !! mismatch {tname} key={key}: "
                      f"pg={got!r}")
    cur.close()
    return {"table": tname, "compared": compared, "mismatch": mismatch,
            "match": mismatch == 0}


def _ordered_stream_compare(con_sql: sqlite3.Connection, pg, schema: str,
                            t: Dict[str, Any]) -> Dict[str, Any]:
    """Full streaming comparison ordered by all columns (for PK-less tables).

    PG database uses LC_COLLATE=C which matches SQLite BINARY collation, and
    NULLS FIRST matches SQLite's ASC null ordering -- deterministic.
    """
    tname = t["name"]
    cols = [c["name"] for c in t["columns"]]
    fq = f"{qident(schema)}.{qident(tname)}"
    sel = ", ".join(qident(c) for c in cols)
    order_s = ", ".join(qident(c) for c in cols)
    order_p = ", ".join(f"{qident(c)} ASC NULLS FIRST" for c in cols)
    s_cur = con_sql.execute(
        f"SELECT {sel} FROM {qident(tname)} ORDER BY {order_s}")
    p_cur = pg.cursor(name="vs_%s" % re.sub(r"\W", "_", f"{schema}_{tname}"))
    p_cur.itersize = 5000
    p_cur.execute(f"SELECT {sel} FROM {fq} ORDER BY {order_p}")
    compared = 0
    mismatch = 0
    while True:
        rs = s_cur.fetchmany(5000)
        rp = p_cur.fetchmany(5000)
        if not rs and not rp:
            break
        if len(rs) != len(rp):
            mismatch += abs(len(rs) - len(rp))
            break
        for a, b in zip(rs, rp):
            compared += 1
            if tuple(_norm(v) for v in a) != tuple(_norm(v) for v in b):
                mismatch += 1
                if mismatch <= 3:
                    print(f"    !! stream mismatch {tname} row {compared}")
    s_cur.close()
    p_cur.close()
    return {"table": tname, "compared": compared, "mismatch": mismatch,
            "match": mismatch == 0}


def validate_deep(pg: psycopg2.extensions.connection, schema: str,
                  tables: List[Dict[str, Any]], snapshot: str,
                  deep_tables: Sequence[str]) -> List[Dict[str, Any]]:
    con_sql = sqlite3.connect("file:%s?mode=ro" % snapshot.replace("\\", "/"),
                              uri=True)
    out: List[Dict[str, Any]] = []
    by_name = {t["name"]: t for t in tables}
    for tname in deep_tables:
        t = by_name.get(tname)
        if t is None:
            continue
        if t["pk_cols"]:
            stats = _pk_stats(con_sql, pg, schema, t)
            samp = _sample_compare(con_sql, pg, schema, t)
            out.append(stats)
            out.append(samp)
        else:
            out.append(_ordered_stream_compare(con_sql, pg, schema, t))
    con_sql.close()
    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def run_migrate(only: Optional[Sequence[str]] = None) -> None:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ensure_database()
    pg = pg_connect(PG_DB)
    manifest: Dict[str, Any] = {"generated_at": time.strftime(
        "%Y-%m-%d %H:%M:%S"), "sources": {}}

    for key, cfg in SOURCES.items():
        if only and key not in only:
            continue
        t0 = time.time()
        print(f"\n=== [{key}] {cfg['db']}")
        snapshot = make_snapshot(key, cfg["db"])
        tables = introspect(snapshot)
        recreate_schema(pg, cfg["schema"])
        table_stats = []
        for t in tables:
            tt0 = time.time()
            for stmt in build_ddl(cfg["schema"], t):
                cur = pg.cursor()
                cur.execute(stmt)
                pg.commit()
                cur.close()
            n = load_table(pg, cfg["schema"], t, snapshot)
            dt = time.time() - tt0
            print(f"  [load] {t['name']}: {n} rows ({dt:.1f}s)")
            table_stats.append({"table": t["name"], "rows": n,
                                "load_seconds": round(dt, 1)})
        elapsed = time.time() - t0
        print(f"=== [{key}] done in {elapsed:.1f}s, "
              f"{len(tables)} tables, "
              f"{sum(s['rows'] for s in table_stats)} rows")
        manifest["sources"][key] = {
            "db": cfg["db"], "schema": cfg["schema"], "snapshot": snapshot,
            "tables": table_stats, "seconds": round(elapsed, 1)}
    pg.close()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(f"\n[manifest] {MANIFEST_PATH}")


def run_validate(only: Optional[Sequence[str]] = None) -> None:
    pg = pg_connect(PG_DB)
    report: Dict[str, Any] = {"validated_at": time.strftime(
        "%Y-%m-%d %H:%M:%S"), "sources": {}}
    all_ok = True
    for key, cfg in SOURCES.items():
        if only and key not in only:
            continue
        snapshot = os.path.join(SNAPSHOT_DIR, "%s_snap.db" % key)
        tables = introspect(snapshot)
        counts = validate_counts(pg, cfg["schema"], tables, snapshot)
        deep = validate_deep(pg, cfg["schema"], tables, snapshot,
                             cfg["deep_tables"])
        ok = all(r["match"] for r in counts) and \
            all(r["match"] for r in deep)
        all_ok = all_ok and ok
        n_rows = sum(r["sqlite"] for r in counts)
        print(f"[validate] {key}: tables={len(counts)} rows={n_rows} "
              f"deep={len(deep)} checks -> {'PASS' if ok else 'FAIL'}")
        for r in counts:
            if not r["match"]:
                print(f"    !! count mismatch {r['table']}: "
                      f"sqlite={r['sqlite']} pg={r['pg']}")
        for r in deep:
            print(f"    deep {r['table']}: {r}")
        report["sources"][key] = {"counts": counts, "deep": deep, "ok": ok}
    pg.close()
    out_path = os.path.join(WORK_DIR, "validation_p1.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"[validate] report -> {out_path}")
    print(f"[validate] OVERALL: {'PASS' if all_ok else 'FAIL'}")
    if not all_ok:
        sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser(description="SQLite -> PG (quant_meta)")
    ap.add_argument("action", choices=["migrate", "validate"])
    ap.add_argument("--only", default="", help="comma list: news,quant,tdx")
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",") if s.strip()] or None
    if args.action == "migrate":
        run_migrate(only)
    else:
        run_validate(only)


if __name__ == "__main__":
    main()
