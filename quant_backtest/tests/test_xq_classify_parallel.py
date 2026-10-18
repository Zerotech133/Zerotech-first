# -*- coding: utf-8 -*-
"""v1.7 multi-instance parallel knobs contract tests (2026-10-18).

Covers the three new xq_classify.py parameters:
  --worker-id  : per-instance sharded status log path
  --order      : asc (historical, unchanged) / desc / engagement fetch SQL
  --segment    : A/B even-odd status_id sharding, disjoint pending queues

Iron rule under test: with all knobs absent, behavior is bit-for-bit the
historical behavior (same SQL semantics, same log path, same log lines).

Run: python -m pytest tests/test_xq_classify_parallel.py -q
No LLM calls, no production DB / C:/aios/ writes (tmp_path + :memory: only).
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_classify as xc

# The exact historical pending-fetch SQL (pre-v1.7 run() literal, with LIMIT ?).
HISTORICAL_SQL = (
    """SELECT s.status_id, s.title, s.desc, s.category
           FROM xq_status s
           WHERE s.status_id NOT IN
                 (SELECT status_id FROM xq_status_classified)
             AND NOT ((s.title IS NULL OR s.title='')
                      AND (s.desc IS NULL OR s.desc=''))
           ORDER BY s.created_at DESC LIMIT ?"""
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate DB + status log into tmp_path (never touch C:/aios/)."""
    monkeypatch.setattr(xc, "DB_PATH", tmp_path / "xueqiu_info.db")
    monkeypatch.setattr(xc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(xc, "STATUS_LOG", tmp_path / "logs" / "status.log")
    return tmp_path


@pytest.fixture(autouse=True)
def reset_worker_id():
    """Keep the module-global worker id from leaking between tests."""
    yield
    xc.set_worker_id("")


def make_db() -> sqlite3.Connection:
    """In-memory DB with a production-shaped xq_status (subset of columns)."""
    con = sqlite3.connect(":memory:")
    con.execute(
        """CREATE TABLE xq_status (
             status_id TEXT PRIMARY KEY,
             created_at TEXT,
             title TEXT,
             desc TEXT,
             user_id TEXT,
             target TEXT,
             view_count INTEGER,
             category TEXT
           )"""
    )
    xc.ensure_table(con)   # NOT IN subquery needs xq_status_classified
    return con


def seed(con: sqlite3.Connection, rows) -> None:
    """rows: iterable of (status_id, created_at, title, desc, view_count)."""
    con.executemany(
        "INSERT INTO xq_status (status_id, created_at, title, desc, view_count, category)"
        " VALUES (?,?,?,?,?,'stock')", rows)
    con.commit()


SAMPLE = [
    ("144250900", "2026-10-18 09:00:00", "t1", "d1", 100),
    ("144250901", "2026-10-18 08:00:00", "t2", "d2", 500),
    ("144250902", "2026-10-17 09:00:00", "t3", "d3", 0),
    ("144250903", "2026-10-17 08:00:00", "t4", "d4", 999),
    ("144250904", "2026-10-16 09:00:00", "t5", "d5", None),
]


# ── 1. Default (all knobs absent) == historical SQL semantics ──────────

def test_default_query_semantically_identical_to_historical():
    con = make_db()
    seed(con, SAMPLE)
    hist = con.execute(HISTORICAL_SQL, (3,)).fetchall()
    new = con.execute(xc.build_pending_query("asc", ""), (3,)).fetchall()
    assert new == hist
    # sanity: newest-created first
    assert [r[0] for r in new] == ["144250900", "144250901", "144250902"]
    con.close()


def test_argparse_defaults_unchanged():
    ap = xc.build_arg_parser()
    a = ap.parse_args([])
    assert a.limit == 500
    assert a.order == "asc"
    assert a.segment == ""
    assert a.worker_id == ""
    assert a.status is False and a.reticker is False
    assert a.recontent is False and a.retry_degraded is False
    # explicit non-default values still parse
    b = ap.parse_args(["--order", "engagement", "--segment", "A",
                       "--worker-id", "w1", "--limit", "50"])
    assert (b.order, b.segment, b.worker_id, b.limit) == \
        ("engagement", "A", "w1", 50)


def test_run_default_logs_no_round_params_line(env, monkeypatch):
    """Default run(): no extra 'round params' line, historical format only."""
    fcon = sqlite3.connect(str(xc.DB_PATH))
    fcon.execute(
        """CREATE TABLE xq_status (
             status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT,
             desc TEXT, user_id TEXT, target TEXT, view_count INTEGER,
             category TEXT)""")
    seed(fcon, SAMPLE)
    fcon.close()
    # classify loop stubbed: never touch Ollama in tests
    monkeypatch.setattr(xc, "classify_one", lambda con, row: True)
    rc = xc.run(10)
    assert rc == 0
    log_text = xc.STATUS_LOG.read_text(encoding="utf-8")
    assert "round start pending_sampled=5 limit=10" in log_text
    assert "round params" not in log_text


# ── 2. --order desc / engagement ────────────────────────────────────────

def test_order_desc_sql_and_execution():
    sql = xc.build_pending_query("desc", "")
    assert "ORDER BY s.status_id DESC" in sql
    assert "NOT IN" in sql
    con = make_db()
    seed(con, SAMPLE)
    rows = con.execute(sql, (10,)).fetchall()
    ids = [r[0] for r in rows]
    assert ids == sorted(ids, reverse=True)   # status_id descending
    con.close()


def test_order_engagement_sql_and_execution():
    sql = xc.build_pending_query("engagement", "")
    assert "LEFT JOIN xq_status_classified c" in sql
    assert "c.status_id IS NULL" in sql
    assert "ORDER BY s.view_count DESC" in sql
    con = make_db()
    seed(con, SAMPLE)
    xc.ensure_table(con)
    # one row already classified -> must be excluded from pending
    con.execute("INSERT INTO xq_status_classified (status_id, parse_ok) VALUES ('144250903', 1)")
    con.commit()
    rows = con.execute(sql, (10,)).fetchall()
    ids = [r[0] for r in rows]
    assert "144250903" not in ids                       # classified row excluded
    assert ids.index("144250901") < ids.index("144250900")  # 500 > 100 views
    assert ids[-1] == "144250904"                       # NULL view_count sorts last
    con.close()


# ── 3. --segment A/B odd-even sharding ──────────────────────────────────

def test_segment_sql_clauses():
    assert "(CAST(s.status_id AS INTEGER) % 2) = 0" in xc.build_pending_query("asc", "A")
    assert "(CAST(s.status_id AS INTEGER) % 2) = 1" in xc.build_pending_query("asc", "B")
    assert "%" not in xc.build_pending_query("asc", "")   # no segment -> no filter
    # segment composes with engagement order too
    assert "(CAST(s.status_id AS INTEGER) % 2) = 1" in \
        xc.build_pending_query("engagement", "B")


def test_segment_disjoint_and_exhaustive():
    """A + B partitions the pending queue: disjoint, union covers all."""
    con = make_db()
    seed(con, SAMPLE)
    sql_a = xc.build_pending_query("asc", "A")
    sql_b = xc.build_pending_query("asc", "B")
    ids_a = {r[0] for r in con.execute(sql_a, (100,)).fetchall()}
    ids_b = {r[0] for r in con.execute(sql_b, (100,)).fetchall()}
    all_ids = {r[0] for r in SAMPLE}
    assert ids_a & ids_b == set()          # never overlap (two workers)
    assert ids_a | ids_b == all_ids        # no row stranded
    assert all(int(i) % 2 == 0 for i in ids_a)
    assert all(int(i) % 2 == 1 for i in ids_b)
    con.close()


# ── 4. --worker-id sharded status log ───────────────────────────────────

def test_worker_id_sharded_log_path(env):
    xc.set_worker_id("w1")
    assert xc._status_log_path() == xc.LOG_DIR / "xq_classify_status_w1.log"
    xc.set_worker_id("  w2  ")   # surrounding whitespace stripped
    assert xc._status_log_path() == xc.LOG_DIR / "xq_classify_status_w2.log"
    xc.set_worker_id("")         # empty -> historical single-file behavior
    assert xc._status_log_path() == xc.STATUS_LOG


def test_log_writes_to_shard_only(env):
    xc.set_worker_id("w1")
    xc.log("hello shard")
    shard = xc.LOG_DIR / "xq_classify_status_w1.log"
    assert shard.exists()
    assert "hello shard" in shard.read_text(encoding="utf-8")
    assert not xc.STATUS_LOG.exists()   # default file untouched by worker w1


def test_log_default_path_unchanged(env):
    xc.log("hello default")
    assert xc.STATUS_LOG.exists()
    assert "hello default" in xc.STATUS_LOG.read_text(encoding="utf-8")
    assert not (xc.LOG_DIR / "xq_classify_status.log").exists() or \
        xc.STATUS_LOG == xc.LOG_DIR / "xq_classify_status.log"
    assert not any(p.name.startswith("xq_classify_status_")
                   for p in xc.LOG_DIR.glob("xq_classify_status_*"))


# ── 5. run() passes knobs through + parallel-run observability line ────

def test_run_passes_order_and_segment(env, monkeypatch):
    fcon = sqlite3.connect(str(xc.DB_PATH))
    fcon.execute(
        """CREATE TABLE xq_status (
             status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT,
             desc TEXT, user_id TEXT, target TEXT, view_count INTEGER,
             category TEXT)""")
    seed(fcon, SAMPLE)
    fcon.close()
    # classify loop stubbed: never touch Ollama in tests
    monkeypatch.setattr(xc, "classify_one", lambda con, row: True)
    calls = []
    orig = xc.build_pending_query

    def recorder(order="asc", segment=""):
        calls.append((order, segment))
        return orig(order, segment)

    monkeypatch.setattr(xc, "build_pending_query", recorder)
    rc = xc.run(10, order="desc", segment="A")
    assert rc == 0
    assert calls == [("desc", "A")]
    log_text = xc.STATUS_LOG.read_text(encoding="utf-8")
    assert "round params order=desc segment=A" in log_text
