# -*- coding: utf-8 -*-
"""intel_review_batch 人工复核批次回归：选批/降级/只读/产物字段。

WO-INTEL-REVIEWBATCH-20261031：临时 DB + 临时机器 JSONL，不碰生产。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import intel_review_batch as B  # noqa: E402

NOW = "2026-10-31T12:00:00+08:00"


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    """固定时钟：本文件所有 build/main 都按 2026-10-31 12:00 判定近 7 天。"""
    monkeypatch.setattr(B.PRE, "_now_naive",
                        lambda now=None: dt.datetime(2026, 10, 31, 12, 0, 0))

Q_SCHEMA = """CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT, title TEXT,
    stocks TEXT, original_jev TEXT, status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)"""
N_SCHEMA = ("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
            "title TEXT, content TEXT, jev TEXT)")


def _mkdb(tmp_path) -> Path:
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute(Q_SCHEMA)
    con.execute(N_SCHEMA)
    con.commit()
    con.close()
    return p


def _add(con, rid, *, queued_at="2026-10-30 09:00", conf=0.6, impact=None,
         source="sina", ts="2026-10-23 10:00", status="pending",
         event_type="other", reason="r-" + "x", title=None):
    orig = json.dumps({"bias": "bull", "conf": conf, "impact": impact,
                       "event_type": event_type, "reason": reason}) \
        if impact else json.dumps({"bias": "bull", "conf": conf})
    con.execute(
        "INSERT OR REPLACE INTO jev_review_queue "
        "(id,queued_at,conf,bias,title,stocks,original_jev,status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rid, queued_at, conf, "bull", title or ("T-" + rid), "[]", orig, status))
    if source is not None:
        con.execute("INSERT OR REPLACE INTO news "
                    "(id,source,ts,title,content,jev) VALUES (?,?,?,?,?,?)",
                    (rid, source, ts, title or rid, "", orig))


def _machine_file(tmp_path, rows) -> Path:
    p = tmp_path / "intel_pre_review_test.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _mrec(rid, decision, score, code=""):
    return {"kind": "machine_pre_review", "id": rid,
            "machine_decision": decision, "score": score,
            "reason_code": code or ("%s_test" % decision),
            "reason": "machine says %s" % decision}


def _seed(tmp_path):
    db = _mkdb(tmp_path)
    con = sqlite3.connect(str(db))
    _add(con, "hi", impact="high", conf=0.6)                      # machine 65+30=95
    _add(con, "rej", impact="medium", conf=0.3)                   # machine 60+20=80
    _add(con, "mid", impact="medium", conf=0.5)                   # machine 53+30=83
    _add(con, "acc0", impact="medium", conf=0.6,
         queued_at="2026-10-30 09:00")                            # 45+0=45
    _add(con, "acc1", impact="medium", conf=0.6,
         queued_at="2026-10-30 09:01")                            # 45+0=45（更新→前）
    _add(con, "nomach", impact="medium", conf=0.6)                # 不在机器文件→fallback 45
    _add(con, "low", impact="low", conf=0.6)                      # machine 30
    _add(con, "legacy1", impact="medium", conf=0.3,
         status="unreviewed_legacy")                              # 非 pending，必须排除
    _add(con, "resolved1", impact="high", conf=0.2, status="resolved")
    con.commit()
    con.close()
    machine = _machine_file(tmp_path, [
        _mrec("hi", "needs_evidence", 65, "high_impact_needs_evidence"),
        _mrec("rej", "reject", 60, "low_conf_reject"),
        _mrec("mid", "needs_evidence", 53, "mid_conf_needs_evidence"),
        _mrec("acc0", "accept", 45, "stable_judgement_accept"),
        _mrec("acc1", "accept", 45, "stable_judgement_accept"),
        _mrec("low", "accept", 30, "low_impact_accept"),
    ])
    return db, machine


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _statuses(db):
    con = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
    finally:
        con.close()


def test_batch_selection_priority_and_fields(tmp_path):
    db, machine = _seed(tmp_path)
    payload = B.build_batch(db_path=db, machine_path=machine, limit=5)
    assert payload["ok"] and payload["db_written"] is False
    # 选批优先：needs_evidence/reject 叠加机器 score；tie 按 queued desc
    assert [r["id"] for r in payload["records"]] == [
        "hi", "mid", "rej", "acc1", "acc0"]
    assert payload["selected"] == 5 and payload["pending"] == 7
    assert payload["machine"]["available"] is True
    assert payload["machine"]["degraded"] is False
    by_id = {r["id"]: r for r in payload["records"]}
    assert by_id["hi"]["machine_decision"] == "needs_evidence"
    assert by_id["hi"]["batch_priority"] == 95
    assert by_id["mid"]["batch_priority"] == 83
    assert by_id["rej"]["batch_priority"] == 80
    assert by_id["acc1"]["batch_priority"] == 45
    # 字段齐全（人工复核所需）
    for r in payload["records"]:
        for key in ("id", "source", "news_ts", "queued_at", "title", "bias",
                    "conf", "impact", "jev", "jev_raw", "machine_decision",
                    "machine_reason_code", "machine_reason", "machine_score",
                    "machine_available", "batch_priority", "batch_rank",
                    "batch_id", "kind", "rule_version", "generated_at",
                    "decision_options", "pending_fields"):
            assert key in r, (r["id"], key)
        assert r["decision_options"] == ["correct", "wrong", "uncertain"]
        assert r["pending_fields"] == {"decision": "", "reviewer": "",
                                       "note": ""}
        assert set(r["jev"]) >= {"bias", "conf", "impact", "event_type",
                                "reason", "companies", "bad_json"}
        assert len(r["title"]) <= 60
        assert r["machine_available"] is True
    # 非 pending 行绝不入批
    ids = {r["id"] for r in payload["records"]}
    assert "legacy1" not in ids and "resolved1" not in ids
    # 分布自洽
    assert sum(payload["counts"]["by_machine_decision"].values()) == 5
    assert payload["counts"]["by_impact"].get("high") == 1


def test_limit_cap_50_and_missing_machine_degrade(tmp_path):
    db, machine = _seed(tmp_path)
    p = B.build_batch(db_path=db, machine_path=machine, limit=100)
    assert p["limit_requested"] == 100 and p["limit"] == 50
    assert p["limit_capped"] is True and p["selected"] == 7  # 7 条 pending 全部可选
    p10 = B.build_batch(db_path=db, machine_path=machine, limit=2)
    assert p10["selected"] == 2 and p10["limit_capped"] is False
    # 缺机器 JSONL → 明确降级（不是静默 0 建议）
    missing = tmp_path / "no_such.jsonl"
    d = B.build_batch(db_path=db, machine_path=missing, limit=5)
    assert d["machine"]["available"] is False
    assert d["machine"]["degraded"] is True
    assert "不存在" in d["machine"]["degrade_reason"]
    for r in d["records"]:
        assert r["machine_decision"] == "unavailable"
        assert r["machine_available"] is False
        assert r["machine_reason_code"] == "machine_jsonl_missing"
    # 降级后按 fallback（impact/conf/时效）排序：hi > rej > mid
    assert [r["id"] for r in d["records"][:3]] == ["hi", "rej", "mid"]
    # 机器文件有、但该 id 不在 200 档（nomach）→ 单条降级为 not_in_machine_top
    e = B.build_batch(db_path=db, machine_path=machine, limit=7)
    nomach = [r for r in e["records"] if r["id"] == "nomach"][0]
    assert nomach["machine_decision"] == "unavailable"
    assert nomach["machine_reason_code"] == "not_in_machine_top"


def test_machine_bad_lines_are_visible(tmp_path):
    db, _ = _seed(tmp_path)
    badfile = tmp_path / "machine_bad.jsonl"
    badfile.write_text(
        json.dumps(_mrec("hi", "needs_evidence", 65)) + "\n"
        + "this-is-not-json\n"
        + json.dumps({"id": "rej", "score": 1}) + "\n",  # 缺 machine_decision
        encoding="utf-8")
    d = B.build_batch(db_path=db, machine_path=badfile, limit=5)
    assert d["machine"]["available"] is True
    assert d["machine"]["bad_lines"] == 2
    assert "不可解析" in d["machine"]["degrade_reason"]
    assert [r["id"] for r in d["records"]][0] == "hi"


def test_dry_run_zero_write_and_write_artifacts(tmp_path):
    db, machine = _seed(tmp_path)
    before = _sha(db)
    before_status = _statuses(db)
    # 默认 dry-run：不写文件、不改库
    outdir = tmp_path / "out"
    rc = B.main(["--db", str(db), "--machine", str(machine),
                 "--limit", "5", "--dry-run", "--out-dir", str(outdir),
                 "--json"])
    assert rc == 0
    assert not outdir.exists()
    assert _sha(db) == before and _statuses(db) == before_status
    # --write：md+jsonl 落盘，库仍不变
    rc = B.main(["--db", str(db), "--machine", str(machine), "--limit", "5",
                 "--write", "--out-dir", str(outdir), "--json"])
    assert rc == 0
    md = [p for p in outdir.glob("intel_review_batch_*.md")]
    jsonl = [p for p in outdir.glob("intel_review_batch_*.jsonl")]
    assert len(md) == 1 and len(jsonl) == 1
    rows = [json.loads(x) for x in
            jsonl[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5
    assert [r["id"] for r in rows] == ["hi", "mid", "rej", "acc1", "acc0"]
    text = md[0].read_text(encoding="utf-8")
    for needle in ("待裁决", "correct", "wrong", "uncertain",
                   "POST /api/jev/review/resolve", "hi"):
        assert needle in text, needle
    assert _sha(db) == before and _statuses(db) == before_status


def test_missing_db_and_bad_now_fail_visible(tmp_path, capsys):
    missing = tmp_path / "missing.db"
    rc = B.main(["--db", str(missing), "--json"])
    assert rc == 1
    db, machine = _seed(tmp_path)
    # 坏机器文件路径不应导致崩溃（明确降级可继续）
    rc = B.main(["--db", str(db), "--machine",
                 str(tmp_path / "nope.jsonl"), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["machine"]["degraded"] is True
    # 缺库时 build 直调也抛 RuntimeError
    try:
        B.build_batch(db_path=missing, machine_path=machine, limit=5)
    except RuntimeError:
        pass
    else:
        raise AssertionError("缺库应 fail-visible")


def test_selected_never_exceeds_50_with_250_pending(tmp_path):
    db = _mkdb(tmp_path)
    con = sqlite3.connect(str(db))
    for i in range(250):
        _add(con, "p%03d" % i, impact="medium", conf=0.6,
             queued_at="2026-10-30 09:%02d" % (i % 60))
    con.commit()
    con.close()
    machine = _machine_file(tmp_path, [])
    p = B.build_batch(db_path=db, machine_path=machine, limit=200)
    assert p["pending"] == 250
    assert p["selected"] == 50 and len(p["records"]) == 50
    assert p["limit_capped"] is True
    assert p["machine"]["degraded"] is True  # 空机器文件=降级
