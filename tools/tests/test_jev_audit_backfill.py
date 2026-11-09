# -*- coding: utf-8 -*-
"""R0 / HIT-DEV-20261107：jev_audit.py 零写入预演与跳过原因分类单测。

覆盖：
  · resolve_audit_db 优先级（CLI > env > 默认）
  · open_db(read_only=True) 由 SQLite 自身阻断写
  · fill_posterior(dry_run=True) 不写任何行 + 四类跳过计数守恒
  · fill_posterior 默认签名（不传新参数）行为不变
  · upsert_judged(dry_run=True) 不写任何行
  · main --dry-run --audit-db 副本：不写 DB、不写产物、打印分类
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_audit as ja  # noqa: E402

BENCH = {"20261027": 0.5, "20261106": 0.2}
STOCK = {("600000.SH", "20261027"): 1.5}
SKIP_KEYS = ("no_codes", "bad_json", "future_day", "no_bench", "no_stock", "priced")


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(ja.SCHEMA)
    return conn


def _row(conn, news_id, companies, trade_day, bias="bull", posterior_at=None):
    conn.execute(
        "INSERT INTO dec_audit(news_id, ts, created_at, trade_day, source, bias, conf,"
        " event_type, companies, model, prompt_version, posterior_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (news_id, "2026-10-26 20:00", "2026-10-26 20:01", trade_day, "sina",
         bias, 0.9, "other", companies, "m", "v", posterior_at))
    conn.commit()


def _fixture(monkeypatch):
    monkeypatch.setattr(ja, "load_returns", lambda dates, codes: (STOCK, BENCH))
    conn = _conn()
    _row(conn, "a_no_codes", "[]", "20261027")
    _row(conn, "b_bad_json", "{bad", "20261027")
    _row(conn, "c_future", '["600000.SH"]', "20261109")     # > data_through 20261106
    _row(conn, "d_no_bench", '["600000.SH"]', "20261020")
    _row(conn, "e_no_stock", '["601059.SH"]', "20261027")   # 有 bench 无个股行情
    _row(conn, "f_priced", '["600000.SH"]', "20261027")
    _row(conn, "g_done", '["600000.SH"]', "20261027",
         posterior_at="2026-10-27T07:10:00")                # 不在候选集
    return conn


def test_resolve_audit_db_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_AUDIT_DB", raising=False)
    assert ja.resolve_audit_db() == ja.AUDIT_DB
    env = tmp_path / "env.db"
    monkeypatch.setenv("JEV_AUDIT_DB", str(env))
    assert ja.resolve_audit_db() == env
    cli = tmp_path / "cli.db"
    assert ja.resolve_audit_db(str(cli)) == cli


def test_open_db_read_only_rejects_writes(tmp_path):
    p = tmp_path / "audit.db"
    ja.open_db(p).close()
    ro = ja.open_db(p, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("CREATE TABLE t(x)")
    ro.close()


def test_fill_posterior_dry_run_writes_nothing(monkeypatch):
    conn = _fixture(monkeypatch)
    before = conn.execute(
        "SELECT seq, hit, posterior_at, ret, bench, excess FROM dec_audit ORDER BY seq").fetchall()
    stats = {}
    n = ja.fill_posterior(conn, 2.5, dry_run=True, stats=stats)
    after = conn.execute(
        "SELECT seq, hit, posterior_at, ret, bench, excess FROM dec_audit ORDER BY seq").fetchall()

    assert n == 0
    assert before == after, "dry-run 不得改动任何行"
    assert stats["candidates"] == 6
    assert stats["no_codes"] == 1
    assert stats["bad_json"] == 1
    assert stats["future_day"] == 1
    assert stats["no_bench"] == 1
    assert stats["no_stock"] == 1
    assert stats["priced"] == 1
    assert stats["updated"] == 0
    assert stats["dry_run"] is True
    assert stats["data_through"] == "20261106"
    assert stats["candidates"] == sum(stats[k] for k in SKIP_KEYS), "分类必须互斥且守恒"
    conn.close()


def test_fill_posterior_apply_writes_only_priced(monkeypatch):
    conn = _fixture(monkeypatch)
    stats = {}
    n = ja.fill_posterior(conn, 2.5, dry_run=False, stats=stats)
    assert n == 1 and stats["updated"] == 1 and stats["priced"] == 1
    got = conn.execute(
        "SELECT hit, posterior_at, ret, bench, excess FROM dec_audit"
        " WHERE news_id='f_priced'").fetchone()
    assert got[0] == ja.hit_of("bull", 1.0, 2.5) == 1
    assert got[1] is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM dec_audit WHERE hit IS NOT NULL").fetchone()[0] == 1
    conn.close()


def test_default_signature_unchanged(monkeypatch):
    """不传 dry_run/stats → 与 R0 之前完全一致（返回 int、写 1 行）。"""
    conn = _fixture(monkeypatch)
    n = ja.fill_posterior(conn, 2.5)
    assert n == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM dec_audit WHERE posterior_at IS NOT NULL").fetchone()[0] == 2
    conn.close()


def test_upsert_judged_dry_run_no_writes():
    conn = _conn()
    rows = [{"news_id": "n1", "ts": "2026-10-26 20:00", "created_at": "",
             "source": "sina", "bias": "bull", "conf": 0.9, "codes": ["600000.SH"],
             "event_type": "other", "model": "m", "prompt_version": "v"}]
    n = ja.upsert_judged(conn, rows, ["20261027"], dry_run=True)
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM dec_audit").fetchone()[0] == 0
    conn.close()


def test_main_dry_run_readonly_and_no_artifacts(tmp_path, monkeypatch, capsys):
    """--dry-run + --audit-db 副本：不写 DB、不写产物、打印跳过分类。"""
    db = tmp_path / "copy.db"
    c = ja.open_db(db)
    c.execute("INSERT INTO dec_audit(news_id, trade_day, companies, bias)"
              " VALUES('x','20261027','[]','bull')")
    c.commit()
    c.close()

    monkeypatch.setattr(ja, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(ja, "load_open_days", lambda: ["20261027", "20261106"])
    monkeypatch.setattr(ja, "load_judged", lambda days: [])
    monkeypatch.setattr(ja, "load_returns", lambda dates, codes: (STOCK, BENCH))
    monkeypatch.setattr(ja, "build_report", lambda conn, band: {
        "directional": {"n": 0, "rate": None, "baseline_all_bull": {"rate": None}},
        "judged_total": 0, "linked_total": 0, "priced_total": 0,
        "data_through": None, "n_trade_days": 0})

    before = db.read_bytes()
    rc = ja.main(["--dry-run", "--audit-db", str(db)])
    assert rc == 0
    assert db.read_bytes() == before, "dry-run 不得改动副本 DB 一个字节"
    assert not list(tmp_path.glob("posterior_*.jsonl"))
    assert not (tmp_path / "latest.json").exists()
    assert not list(tmp_path.glob("baseline_*.json"))
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "no_codes" in out
