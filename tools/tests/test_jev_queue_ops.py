# -*- coding: utf-8 -*-
"""jev_queue_ops 单测（task-8 / WO-JEV-OPS-20261103）。

覆盖：空表、队列分层/aging/stuck、hit 空 backlog、坏行 fail-visible、
缺表缺列 fail-visible、audit 缺失降级、--json/--md/--out、只读 hash 断言。
全部用 tmp_path 临时 sqlite，不碰生产 news.db / jev_audit.db。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_queue_ops as ops  # noqa: E402

NOW = datetime(2026, 11, 4, 2, 0, 0)
NOW_ARG = "2026-11-04 02:00:00"

NEWS_DDL = """
CREATE TABLE news (
    id TEXT PRIMARY KEY,
    jev TEXT,
    judged_at TEXT
)
"""

QUEUE_DDL = """
CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY,
    queued_at TEXT NOT NULL,
    conf REAL,
    bias TEXT,
    title TEXT,
    stocks TEXT,
    original_jev TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TEXT,
    decision TEXT,
    note TEXT,
    review_type TEXT NOT NULL DEFAULT ''
)
"""

AUDIT_DDL = """
CREATE TABLE dec_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT,
    hit INTEGER,
    model TEXT,
    trade_day TEXT,
    posterior_at TEXT
)
"""

JEV = '{"bias": "bull", "conf": 0.8, "impact": "low"}'


def _ts(delta: timedelta) -> str:
    return (NOW - delta).strftime("%Y-%m-%d %H:%M:%S")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_db(tmp_path: Path, *, news: bool = True, queue: bool = True) -> Path:
    db = tmp_path / "news.db"
    conn = sqlite3.connect(db)
    try:
        if news:
            conn.execute(NEWS_DDL)
        if queue:
            conn.execute(QUEUE_DDL)
        conn.commit()
    finally:
        conn.close()
    return db


def _make_full(tmp_path: Path) -> tuple[Path, Path]:
    """队列 10 行（含 4 类坏行）+ news 11 行已判读 + audit 7 行（含坏行）。"""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        # news: 11 行已判读（n1..n6, n7, n8, badts, badstatus, badconf）
        for nid in ("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8",
                    "badts", "badstatus", "badconf"):
            conn.execute(
                "INSERT INTO news (id, jev, judged_at) VALUES (?,?,?)",
                (nid, JEV, _ts(timedelta(days=1))))
        queue_rows = [
            # id, queued_at, conf, bias, title, status, review_type,
            # reviewer, reviewed_at, decision
            ("n1", _ts(timedelta(hours=2)), 0.5, "bull", "p-one",
             "pending", "", None, None, None),
            ("n2", _ts(timedelta(days=5)), 0.4, "bear", "p-two-stuck",
             "pending", "", None, None, None),
            ("n3", _ts(timedelta(days=2)), 0.6, "neutral", "p-three-claimed",
             "pending", "human", "R1", None, None),
            ("n4", _ts(timedelta(days=4)), 0.8, "bull", "resolved-human",
             "resolved", "human", "老田", _ts(timedelta(days=1)), "correct"),
            ("n5", _ts(timedelta(days=4)), 0.7, "bear", "resolved-ai",
             "resolved", "ai_first_pass", "N1-AI", _ts(timedelta(days=1)),
             "wrong"),
            ("n6", _ts(timedelta(days=10)), 0.9, "bull", "legacy",
             "unreviewed_legacy", "", None, None, None),
            ("ghost", _ts(timedelta(hours=1)), 0.5, "bull", "orphan",
             "pending", "", None, None, None),
            ("badts", "not-a-date", 0.5, "bull", "bad-ts",
             "pending", "", None, None, None),
            ("badstatus", _ts(timedelta(hours=1)), 0.5, "bull", "bad-status",
             "weird", "", None, None, None),
            ("badconf", _ts(timedelta(hours=1)), "abc", "bull", "bad-conf",
             "pending", "", None, None, None),
        ]
        conn.executemany(
            "INSERT INTO jev_review_queue (id, queued_at, conf, bias, title, "
            "status, review_type, reviewer, reviewed_at, decision) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", queue_rows)
        conn.commit()
    finally:
        conn.close()

    audit_db = tmp_path / "jev_audit.db"
    acon = sqlite3.connect(audit_db)
    try:
        acon.execute(AUDIT_DDL)
        rows = [
            (1, "qwen3.5:9b", "20261031", "2026-11-01T00:00:00Z"),
            (0, "qwen3.5:9b", "20261101", "2026-11-02T00:00:00Z"),
            (None, "qwen3.5:9b", "20261020", None),
            (None, "unknown", "20261103", None),
            (None, "jev-latest", "20261104", None),
            (2, "qwen3.5:9b", "20261104", None),
            (1, "unknown", "bad-day", "2026-11-01T00:00:00Z"),
        ]
        acon.executemany(
            "INSERT INTO dec_audit (hit, model, trade_day, posterior_at) "
            "VALUES (?,?,?,?)", rows)
        acon.commit()
    finally:
        acon.close()
    return db, audit_db


def _run_json(capsys, argv):
    rc = ops.main(argv)
    out = capsys.readouterr()
    assert rc == 0, "rc=%s stderr=%s" % (rc, out.err)
    return json.loads(out.out)


def _base_argv(db: Path, audit_db: Path, *extra) -> list[str]:
    return ["--db", str(db), "--audit-db", str(audit_db),
            "--now", NOW_ARG] + list(extra)


# --------------------------------------------------------------------------- #
# 1) 空表
# --------------------------------------------------------------------------- #
def test_empty_tables(tmp_path, capsys):
    db = _make_db(tmp_path)
    audit = tmp_path / "jev_audit.db"
    acon = sqlite3.connect(audit)
    acon.execute(AUDIT_DDL)
    acon.commit()
    acon.close()
    rep = _run_json(capsys, _base_argv(db, audit, "--json"))
    assert rep["queue"]["total"] == 0
    assert rep["queue"]["pending"]["n"] == 0
    assert rep["queue"]["coverage"]["judged"] == 0
    assert rep["queue"]["coverage"]["coverage"] is None
    assert sum(rep["queue"]["layers"][name]["total"]
               for name in ops.LAYER_ORDER) == 0
    assert rep["hit_empty"]["available"] is True
    assert rep["hit_empty"]["total"] == 0
    assert rep["hit_empty"]["backlog"]["n"] == 0
    assert rep["bad_rows_total"] == 0
    # 文本模式也不崩
    rc = ops.main(_base_argv(db, audit))
    out = capsys.readouterr().out
    assert rc == 0
    assert "队列: 总 0" in out


# --------------------------------------------------------------------------- #
# 2) 队列分层 / aging / stuck / 覆盖率
# --------------------------------------------------------------------------- #
def test_queue_layers_aging_stuck(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    rep = _run_json(capsys, _base_argv(db, audit, "--json"))
    q = rep["queue"]
    assert q["total"] == 10
    assert q["by_status"]["pending"] == 6
    assert q["by_status"]["resolved"] == 2
    assert q["by_status"]["unreviewed_legacy"] == 1
    assert q["by_status"]["weird"] == 1
    assert q["by_review_type"]["human"] == 2
    assert q["by_review_type"]["ai_first_pass"] == 1
    # 分层：pending 6 / human 1（resolved human）/ ai 1 / legacy 1 / other 1
    layers = {k: v["total"] for k, v in q["layers"].items()}
    assert layers == {"pending": 6, "human": 1, "ai": 1,
                      "legacy_unreviewed": 1, "other": 1}
    assert q["layers"]["pending"]["by_status"]["pending"] == 6
    # aging（未解决 = pending 6 + legacy 1 = 7；其中 1 条 queued_at 非法）
    assert q["unresolved"]["n"] == 7
    assert q["unresolved"]["aged_rows"] == 6
    assert q["unresolved"]["unparsable_queued_at"] == 1
    assert q["unresolved"]["aging"] == {
        "<1d": 3, "1-3d": 1, "3-7d": 1, "7-14d": 1, ">=14d": 0}
    assert q["pending"]["aging"] == {
        "<1d": 3, "1-3d": 1, "3-7d": 1, "7-14d": 0, ">=14d": 0}
    # stuck：只算 pending 且 age > 3d → n2(5d)
    assert q["stuck"]["n"] == 1
    assert q["stuck"]["samples"][0]["id"] == "n2"
    assert q["pending"]["oldest_age_days"] == 5.0
    assert q["pending"]["claimed_no_decision"] == 1
    # 覆盖率：judged 11，队列 id 在 news 9（ghost 是孤儿），judged_queued 9
    c = q["coverage"]
    assert c["judged"] == 11
    assert c["queued_in_news"] == 9
    assert c["judged_queued"] == 9
    assert c["coverage"] == round(9 / 11, 4)
    assert c["candidate_backlog"] == 2
    # 最近刷新 = ghost 的 queued_at（1h 前）
    assert q["last_refresh"]["queued_at"] == _ts(timedelta(hours=1))
    assert q["last_refresh"]["age_days"] == round(1 / 24, 2)


# --------------------------------------------------------------------------- #
# 3) hit 空 backlog
# --------------------------------------------------------------------------- #
def test_hit_empty_backlog(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    rep = _run_json(capsys, _base_argv(db, audit, "--json"))
    a = rep["hit_empty"]
    assert a["available"] is True
    assert a["total"] == 7
    assert a["labeled"] == 3
    assert a["hit_1"] == 2
    assert a["hit_0"] == 1
    assert a["hit_missing"] == 3
    assert a["hit_invalid"] == 1
    assert a["hit_coverage"] == round(3 / 7, 4)
    assert a["direction_hit_rate"] == round(2 / 3, 4)
    assert a["hit_missing_pct"] == round(3 / 7, 4)
    assert a["posterior_filled"] == 3
    b = a["backlog"]
    assert b["n"] == 3
    assert b["max_trade_day"] == "20261104"
    assert b["oldest_trade_day"] == "20261020"
    assert b["oldest_age_days"] == 15.0
    assert b["aging"] == {"<1d": 1, "1-3d": 1, "3-7d": 0,
                          "7-14d": 0, ">=14d": 1}
    by_model = {m["model"]: m["hit_missing"] for m in b["by_model_top"]}
    assert by_model == {"jev-latest": 1, "qwen3.5:9b": 1, "unknown": 1}


# --------------------------------------------------------------------------- #
# 4) 坏行 fail-visible
# --------------------------------------------------------------------------- #
def test_bad_rows_fail_visible(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    rep = _run_json(capsys, _base_argv(db, audit, "--json"))
    bad = rep["queue"]["bad_rows"]
    assert bad["n"] == 4
    assert bad["invalid_queued_at"] == 1
    assert bad["unknown_status"] == 1
    assert bad["invalid_conf"] == 1
    assert bad["queue_id_not_in_news"] == 1
    assert bad["samples"]["invalid_queued_at"][0]["id"] == "badts"
    assert bad["samples"]["unknown_status"][0]["id"] == "badstatus"
    assert bad["samples"]["invalid_conf"][0]["id"] == "badconf"
    assert bad["samples"]["queue_id_not_in_news"][0]["id"] == "ghost"
    abad = rep["hit_empty"]["bad_rows"]
    assert abad["n"] == 2
    assert abad["invalid_hit"] == 1
    assert abad["invalid_trade_day"] == 1
    assert rep["bad_rows_total"] == 6

    rc = ops.main(_base_argv(db, audit, "--fail-on-bad"))
    out = capsys.readouterr()
    assert rc == 4
    assert "坏行" in out.err


# --------------------------------------------------------------------------- #
# 5) 只读断言（db 字节不变）
# --------------------------------------------------------------------------- #
def test_read_only_hash_unchanged(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    db_hash = _sha256(db)
    audit_hash = _sha256(audit)
    out_file = tmp_path / "ops.md"
    rc = ops.main(_base_argv(db, audit, "--md", "--out", str(out_file)))
    capsys.readouterr()
    assert rc == 0
    assert out_file.is_file()
    assert not list(tmp_path.glob("*.tmp"))
    assert _sha256(db) == db_hash
    assert _sha256(audit) == audit_hash


# --------------------------------------------------------------------------- #
# 6) 缺表/缺列 fail-visible
# --------------------------------------------------------------------------- #
def test_missing_queue_table_fail_visible(tmp_path, capsys):
    db = _make_db(tmp_path, queue=False)
    rc = ops.main(["--db", str(db), "--no-audit"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺表 jev_review_queue" in err


def test_missing_news_table_fail_visible(tmp_path, capsys):
    db = _make_db(tmp_path, news=False)
    rc = ops.main(["--db", str(db), "--no-audit"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺表 news" in err


def test_missing_column_fail_visible(tmp_path, capsys):
    db = tmp_path / "news.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE news (id TEXT PRIMARY KEY, jev TEXT, "
                     "judged_at TEXT)")
        # 队列缺 status 列
        conn.execute("CREATE TABLE jev_review_queue (id TEXT PRIMARY KEY, "
                     "queued_at TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()
    rc = ops.main(["--db", str(db), "--no-audit"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺列" in err and "status" in err


# --------------------------------------------------------------------------- #
# 7) audit 缺失降级 / --require-audit
# --------------------------------------------------------------------------- #
def test_audit_missing_graceful(tmp_path, capsys):
    db, _audit = _make_full(tmp_path)
    missing = tmp_path / "no_such_audit.db"
    rc = ops.main(["--db", str(db), "--audit-db", str(missing), "--json"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    rep = json.loads(out.out)
    assert rep["hit_empty"] is None
    assert any("audit db 不存在" in w for w in rep["warnings"])
    # --require-audit 则 fail-visible
    rc2 = ops.main(["--db", str(db), "--audit-db", str(missing),
                    "--require-audit"])
    err = capsys.readouterr().err
    assert rc2 == 2
    assert "audit db 不存在" in err


def test_audit_missing_table_graceful(tmp_path, capsys):
    db, _audit = _make_full(tmp_path)
    bad_audit = tmp_path / "bad_audit.db"
    conn = sqlite3.connect(bad_audit)
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()
    rc = ops.main(["--db", str(db), "--audit-db", str(bad_audit), "--json"])
    out = capsys.readouterr()
    assert rc == 0
    assert json.loads(out.out)["hit_empty"] is None


# --------------------------------------------------------------------------- #
# 8) 输出模式
# --------------------------------------------------------------------------- #
def test_json_md_conflict(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    rc = ops.main(_base_argv(db, audit, "--json", "--md"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "二选一" in err


def test_md_output_content(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    out_file = tmp_path / "ops.md"
    rc = ops.main(_base_argv(db, audit, "--md", "--out", str(out_file)))
    out = capsys.readouterr().out
    assert rc == 0
    text = out_file.read_text(encoding="utf-8")
    assert text == out
    assert "# JEV 复核队列运营视图" in text
    assert "## 三、Aging / Stuck" in text
    assert "## 四、hit 空后验 backlog" in text
    assert "| pending 数 | 6 |" in text
    assert "| hit 空 backlog | 3（42.86%） |" in text


# --------------------------------------------------------------------------- #
# 9) 其它边界
# --------------------------------------------------------------------------- #
def test_future_queued_at_clamped(tmp_path, capsys):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO news (id, jev, judged_at) VALUES ('f1',?,?)",
                     (JEV, _ts(timedelta(days=1))))
        conn.execute(
            "INSERT INTO jev_review_queue (id, queued_at, status) VALUES (?,?,?)",
            ("f1", (NOW + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
             "pending"))
        conn.commit()
    finally:
        conn.close()
    rep = _run_json(capsys, ["--db", str(db), "--no-audit",
                             "--now", NOW_ARG, "--json"])
    # 未来时间戳按 0 天处理（clamp），不产生负 aging / 不崩
    assert rep["queue"]["pending"]["aging"]["<1d"] == 1
    assert rep["queue"]["pending"]["oldest_age_days"] == 0.0
    assert rep["queue"]["bad_rows"]["n"] == 0


def test_invalid_now_and_params(tmp_path, capsys):
    db, audit = _make_full(tmp_path)
    rc = ops.main(["--db", str(db), "--audit-db", str(audit),
                   "--now", "not-a-time"])
    err = capsys.readouterr().err
    assert rc == 2 and "--now" in err
    rc2 = ops.main(_base_argv(db, audit, "--samples", "99"))
    err2 = capsys.readouterr().err
    assert rc2 == 2 and "--samples" in err2
    rc3 = ops.main(["--db", str(tmp_path / "missing.db"), "--no-audit"])
    err3 = capsys.readouterr().err
    assert rc3 == 2 and "DB 不存在" in err3


def test_layering_helper_and_bucket():
    assert ops.layer_of("pending", "human") == "pending"
    assert ops.layer_of("resolved", "human") == "human"
    assert ops.layer_of("resolved", "ai_first_pass") == "ai"
    assert ops.layer_of("unreviewed_legacy", "") == "legacy_unreviewed"
    assert ops.layer_of("resolved", "") == "other"
    assert ops.bucket_of(0.5) == "<1d"
    assert ops.bucket_of(1.0) == "1-3d"
    assert ops.bucket_of(3.0) == "3-7d"
    assert ops.bucket_of(7.0) == "7-14d"
    assert ops.bucket_of(14.0) == ">=14d"
    assert ops.bucket_of(None) is None
