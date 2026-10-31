# -*- coding: utf-8 -*-
"""intel_pre_review 机器预审回归：打分/排序稳定、上限、只读、fail-visible。

WO-INTEL-MACHINE-20261031（执行拍板 2/4 机器侧）。临时 DB，不碰生产。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import intel_pre_review as A  # noqa: E402

NOW = "2026-10-31T12:00:00+08:00"

Q_SCHEMA = """CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT, title TEXT,
    stocks TEXT, original_jev TEXT, status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)"""
N_SCHEMA = ("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
            "title TEXT, jev TEXT)")


def _mkdb(tmp_path) -> Path:
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute(Q_SCHEMA)
    con.execute(N_SCHEMA)
    con.commit()
    con.close()
    return p


def _add(con, rid, *, queued_at="2026-10-30 09:00", conf=0.6, impact=None,
         orig_raw=None, source="sina", ts="2026-09-01 00:00",
         news_impact=None, status="pending"):
    orig = orig_raw if orig_raw is not None else json.dumps(
        {"impact": impact} if impact else {})
    con.execute(
        "INSERT OR REPLACE INTO jev_review_queue "
        "(id,queued_at,conf,bias,title,stocks,original_jev,status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rid, queued_at, conf, "bull", "T-" + rid, "[]", orig, status))
    if source is not None:
        nj = json.dumps({"impact": news_impact}) if news_impact else "{}"
        con.execute("INSERT OR REPLACE INTO news (id,source,ts,title,jev) "
                    "VALUES (?,?,?,?,?)", (rid, source, ts, rid, nj))


def _seed(tmp_path) -> Path:
    """9 条：覆盖三决策 + 同分排序 + 坏 JSON/缺来源。"""
    db = _mkdb(tmp_path)
    con = sqlite3.connect(str(db))
    _add(con, "hi", impact="high", conf=0.6)                        # 40+20+5 =65 needs_evidence
    _add(con, "rej", impact="medium", conf=0.3)                     # 20+15+20+5=60 reject
    _add(con, "mid", impact="medium", conf=0.5)                     # 20+8+20+5 =53 needs_evidence
    _add(con, "acc0", impact="medium", conf=0.6,
         queued_at="2026-10-30 09:00")                              # 20+20+5=45 accept
    _add(con, "acc1", impact="medium", conf=0.6,
         queued_at="2026-10-30 09:01")                              # 45（同分但更新→前）
    _add(con, "nosrc", impact="medium", conf=0.6, source=None)      # 20+20+0=40 needs_evidence
    _add(con, "low", impact="low", conf=0.6)                        # 5+20+5=30 accept(low)
    _add(con, "bad", orig_raw="not-json", conf=0.6)                 # 0+20+5=25 needs_evidence(bad)
    _add(con, "stale", impact="medium", conf=0.6,
         queued_at="2026-09-01 09:00")                              # 20+0+5=25 accept
    con.commit()
    con.close()
    return db


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _counts(db):
    con = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
    finally:
        con.close()


def test_scores_decisions_and_stable_order(tmp_path):
    db = _seed(tmp_path)
    d = A.build_pre_review(db_path=db, now=NOW, limit=200, samples=20)
    assert d["ok"] and d["db_written"] is False
    assert d["pending"] == 9 and d["processed"] == 9
    # 全量顺序（score desc → queued_at desc → id asc）
    assert [x["id"] for x in d["items"]] == [
        "hi", "rej", "mid", "acc1", "acc0", "nosrc", "low", "bad", "stale"]
    by_id = {x["id"]: x for x in d["items"]}
    assert by_id["hi"]["score"] == 65 and by_id["hi"]["machine_decision"] == "needs_evidence"
    assert by_id["hi"]["reason_code"] == "high_impact_needs_evidence"
    assert by_id["rej"]["score"] == 60 and by_id["rej"]["machine_decision"] == "reject"
    assert by_id["rej"]["reason_code"] == "low_conf_reject"
    assert by_id["mid"]["score"] == 53 and by_id["mid"]["machine_decision"] == "needs_evidence"
    assert by_id["mid"]["reason_code"] == "mid_conf_needs_evidence"
    assert by_id["acc0"]["score"] == 45 and by_id["acc0"]["machine_decision"] == "accept"
    assert by_id["acc1"]["score"] == 45
    assert by_id["nosrc"]["machine_decision"] == "needs_evidence"
    assert by_id["nosrc"]["reason_code"] == "source_unknown"
    assert by_id["nosrc"]["source_known"] is False
    assert by_id["low"]["machine_decision"] == "accept"
    assert by_id["low"]["reason_code"] == "low_impact_accept"
    assert by_id["bad"]["machine_decision"] == "needs_evidence"
    assert by_id["bad"]["reason_code"] == "bad_original_jev"
    assert by_id["stale"]["machine_decision"] == "accept"
    # 决策只允许三种；计数自洽
    assert set(d["decisions"]) == set(A.DECISIONS)
    assert sum(d["decisions"].values()) == 9
    # 稳定性：同 now 重复两次 → 完全一致
    d2 = A.build_pre_review(db_path=db, now=NOW, limit=200)
    assert [(x["id"], x["score"], x["machine_decision"]) for x in d["items"]] == \
        [(x["id"], x["score"], x["machine_decision"]) for x in d2["items"]]


def test_limit_cap_200_and_input_250_output_200(tmp_path):
    db = _mkdb(tmp_path)
    con = sqlite3.connect(str(db))
    for i in range(250):
        _add(con, "p%03d" % i, impact="medium", conf=0.6,
             queued_at="2026-10-30 09:%02d" % (i % 60))
    con.commit()
    con.close()
    d = A.build_pre_review(db_path=db, now=NOW, limit=1000)
    assert d["pending"] == 250
    assert d["limit_requested"] == 1000 and d["limit"] == 200
    assert d["limit_capped"] is True
    assert d["processed"] == 200 and len(d["items"]) == 200
    d2 = A.build_pre_review(db_path=db, now=NOW, limit=200)
    assert d2["limit"] == 200 and d2["processed"] == 200
    d3 = A.build_pre_review(db_path=db, now=NOW, limit=37)
    assert d3["processed"] == 37 and d3["limit_capped"] is False


def test_dry_run_is_read_only_and_writes_no_file(tmp_path, capsys):
    db = _seed(tmp_path)
    before = _sha(db)
    before_counts = _counts(db)
    rc = A.main(["--db", str(db), "--dry-run", "--now", NOW, "--json",
                 "--samples", "3"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run"
    assert payload["db_written"] is False and len(payload["items"]) == 9
    assert _sha(db) == before and _counts(db) == before_counts
    # 默认不写文件；即使给了 --out 但没 --write 也不落盘
    out = tmp_path / "should_not_exist.jsonl"
    rc = A.main(["--db", str(db), "--dry-run", "--out", str(out), "--now", NOW])
    assert rc == 0
    assert not out.exists()


def test_write_mode_emits_jsonl_without_db_write(tmp_path):
    db = _seed(tmp_path)
    before = _sha(db)
    out = tmp_path / "pre" / "decisions.jsonl"
    rc = A.main(["--db", str(db), "--write", "--out", str(out), "--now", NOW,
                 "--limit", "5", "--json"])
    assert rc == 0
    assert out.is_file()
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 5
    for row in lines:
        assert row["kind"] == "machine_pre_review"
        assert row["rule_version"] == "intel-pre-review/v1"
        assert row["machine_decision"] in A.DECISIONS
        assert isinstance(row["score"], int) and row["id"]
        assert row["reason"] and row["reason_code"]
    assert [r["id"] for r in lines] == ["hi", "rej", "mid", "acc1", "acc0"]
    # 零写库：DB hash 与 status 完全未变
    assert _sha(db) == before
    assert _counts(db) == {"pending": 9}


def test_fail_visible_missing_db_bad_now_bad_out_and_limit(tmp_path, capsys):
    missing = tmp_path / "missing.db"
    rc = A.main(["--db", str(missing), "--json"])
    assert rc == 1
    with pytest.raises(RuntimeError):
        A.build_pre_review(db_path=missing, now=NOW)
    db = _seed(tmp_path)
    assert A.main(["--db", str(db), "--now", "not-a-time"]) == 1
    assert A.main(["--db", str(db), "--limit", "0"]) == 1
    # 输出路径=news.db → 拒绝（零写库硬保护），退出码 2
    assert A.main(["--db", str(db), "--write", "--out", str(db),
                   "--now", NOW]) == 2
    # 默认输出目录下没有意外文件
    assert not any(Path(tmp_path).glob("*.jsonl"))
    # 坏行不崩：bad JSON 在 build 里正常给出 needs_evidence
    d = A.build_pre_review(db_path=db, now=NOW, limit=200)
    bad = [x for x in d["items"] if x["id"] == "bad"][0]
    assert bad["machine_decision"] == "needs_evidence"
    assert bad["reason_code"] == "bad_original_jev"


def test_only_pending_rows_are_considered(tmp_path):
    db = _seed(tmp_path)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE jev_review_queue SET status='unreviewed_legacy' "
                "WHERE id='stale'")
    con.commit()
    con.close()
    d = A.build_pre_review(db_path=db, now=NOW, limit=200)
    assert d["pending"] == 8
    assert "stale" not in {x["id"] for x in d["items"]}
    assert d["status_counts"].get("unreviewed_legacy") == 1
