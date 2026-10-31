# -*- coding: utf-8 -*-
"""intel_ai_first_pass 回归：映射/迁移/备份/事务/不覆盖非 pending（临时 DB）。

WO-INTEL-AI1ST-20261031：不碰生产 news.db、不联网、不跑 A/B。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import intel_ai_first_pass as A  # noqa: E402

OLD_SCHEMA = """CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT, title TEXT,
    stocks TEXT, original_jev TEXT, status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)"""


def _mkdb(tmp_path) -> Path:
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute(OLD_SCHEMA)
    rows = [
        ("a", "2026-10-30 10:00", "pending"),
        ("b", "2026-10-30 10:01", "pending"),
        ("c", "2026-10-30 10:02", "pending"),
        ("d", "2026-10-30 10:03", "unreviewed_legacy"),
        ("e", "2026-10-30 10:04", "resolved"),
    ]
    con.executemany(
        "INSERT INTO jev_review_queue (id,queued_at,status) VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return p


def _batch(tmp_path, records, name="batch.jsonl") -> Path:
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _machine(tmp_path, records, name="machine.jsonl") -> Path:
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _base_batch():
    return [
        {"id": "a", "machine_decision": "accept", "batch_id": "IRB-X"},
        {"id": "b", "machine_decision": "reject", "batch_id": "IRB-X"},
        {"id": "c", "machine_decision": "needs_evidence", "batch_id": "IRB-X"},
        {"id": "d", "machine_decision": "accept", "batch_id": "IRB-X"},
        {"id": "e", "machine_decision": "accept", "batch_id": "IRB-X"},
    ]


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _statuses(db):
    con = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
    finally:
        con.close()


def _rt_cols(db):
    con = sqlite3.connect(str(db))
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(jev_review_queue)")]
    finally:
        con.close()


def test_plan_mapping_and_limit(tmp_path):
    db = _mkdb(tmp_path)
    batch = _batch(tmp_path, _base_batch())
    machine = _machine(tmp_path, [{"id": "z", "machine_decision": "accept"}])
    p = A.build_plan(db_path=db, batch_path=batch, machine_path=machine)
    assert p["ok"] and p["db_written"] is False
    assert p["counts"]["batch_records"] == 5
    assert p["counts"]["writable"] == 3           # a/b/c pending；d legacy、e resolved 跳过
    assert p["counts"]["skipped_non_pending"] == 2
    assert p["counts"]["decisions_writable"] == {
        "correct": 1, "wrong": 1, "uncertain": 1}
    by_id = {x["id"]: x for x in p["items"]}
    assert by_id["a"]["decision"] == "correct"
    assert by_id["b"]["decision"] == "wrong"
    assert by_id["c"]["decision"] == "uncertain"
    assert by_id["d"]["writable"] is False
    assert by_id["e"]["writable"] is False
    # limit 上限 50：请求 999 封顶
    p2 = A.build_plan(db_path=db, batch_path=batch, machine_path=machine,
                      limit=999)
    assert p2["limit"] == 50 and p2["limit_capped"] is True
    p3 = A.build_plan(db_path=db, batch_path=batch, machine_path=machine,
                      limit=2)
    assert p3["counts"]["selected"] == 2


def test_machine_missing_maps_to_uncertain(tmp_path):
    db = _mkdb(tmp_path)
    batch = _batch(tmp_path, [
        {"id": "a", "machine_decision": "accept"},
        {"id": "b"},                       # 无决策字段
    ])
    missing = tmp_path / "no_machine.jsonl"
    p = A.build_plan(db_path=db, batch_path=batch, machine_path=missing)
    assert p["machine"]["exists"] is False
    by_id = {x["id"]: x for x in p["items"]}
    assert by_id["a"]["decision"] == "correct"    # 批次内 accept 仍可用
    assert by_id["b"]["decision"] == "uncertain"  # 缺机器→uncertain
    assert by_id["b"]["machine_decision"] == "unavailable"


def test_dry_run_zero_write(tmp_path):
    db = _mkdb(tmp_path)
    before = _sha(db)
    batch = _batch(tmp_path, _base_batch())
    machine = _machine(tmp_path, [])
    rc = A.main(["--db", str(db), "--batch", str(batch), "--machine",
                 str(machine), "--dry-run", "--json"])
    assert rc == 0
    assert _sha(db) == before
    assert not any(Path(tmp_path).glob("news.db.ai1st.bak-*"))


def test_apply_writes_refresh_type_and_manifest(tmp_path):
    db = _mkdb(tmp_path)
    before = _sha(db)
    batch = _batch(tmp_path, _base_batch())
    machine = _machine(tmp_path, [])
    bak = tmp_path / "bak"
    manifest = tmp_path / "manifest.json"
    rc = A.main(["--db", str(db), "--batch", str(batch), "--machine",
                 str(machine), "--apply", "--backup-dir", str(bak),
                 "--manifest", str(manifest), "--json"])
    assert rc == 0
    # 迁移幂等：列存在；status 分账正确
    cols = _rt_cols(db)
    assert "review_type" in cols
    assert _statuses(db) == {"unreviewed_legacy": 1, "resolved": 4}
    con = sqlite3.connect(str(db))
    try:
        rows = {r[0]: r for r in con.execute(
            "SELECT id, status, decision, reviewer, review_type, note "
            "FROM jev_review_queue")}
    finally:
        con.close()
    for rid, dec in (("a", "correct"), ("b", "wrong"), ("c", "uncertain")):
        assert rows[rid][3] == "N1-AI"
        assert rows[rid][2] == dec
        assert rows[rid][4] == "ai_first_pass"
        assert rows[rid][5].startswith("ai_first_pass:N1-AI")
    # 非 pending 不覆盖：legacy/resolved 原样
    assert rows["d"][1] == "unreviewed_legacy"
    assert rows["e"][1] == "resolved"
    assert rows["e"][4] == ""            # 未被 AI 初筛改写
    # 备份=apply 前快照（应用前 schema 无 review_type 列、全原始状态）
    backups = [p for p in bak.glob("news.db.ai1st.bak-*")]
    assert len(backups) == 1
    bc = sqlite3.connect(str(backups[0]))
    try:
        assert "review_type" not in [r[1] for r in bc.execute(
            "PRAGMA table_info(jev_review_queue)")]
        assert bc.execute("SELECT COUNT(*) FROM jev_review_queue "
                          "WHERE status='pending'").fetchone()[0] == 3
    finally:
        bc.close()
    assert _sha(db) != before
    mf = json.loads(manifest.read_text(encoding="utf-8"))
    assert mf["counts"]["written"] == 3
    assert mf["post_state"]["resolved_ai_first_pass"] == 3
    assert mf["review_type_column_added"] is True
    assert mf["applied_ids_count"] == 3 and mf["applied_ids_sha256"]


def test_apply_idempotent_second_run_noop(tmp_path):
    db = _mkdb(tmp_path)
    batch = _batch(tmp_path, _base_batch())
    machine = _machine(tmp_path, [])
    bak = tmp_path / "bak"
    args = ["--db", str(db), "--batch", str(batch), "--machine", str(machine),
            "--apply", "--backup-dir", str(bak)]
    assert A.main(args) == 0
    backups_after_first = sorted(bak.glob("news.db.ai1st.bak-*"))
    p2 = A.build_plan(db_path=db, batch_path=batch, machine_path=machine)
    assert p2["counts"]["writable"] == 0
    assert A.main(args) == 0                      # no-op 不报错、不再备份
    assert sorted(bak.glob("news.db.ai1st.bak-*")) == backups_after_first


def test_fail_visible_missing_batch_and_bad_lines(tmp_path, capsys):
    db = _mkdb(tmp_path)
    rc = A.main(["--db", str(db), "--batch", str(tmp_path / "nope.jsonl"),
                 "--json"])
    assert rc == 1
    # 批次坏行跳过；全坏 → fail-visible 退出 1
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not-json\n{\"id\":\"a\"}\n", encoding="utf-8")
    rc = A.main(["--db", str(db), "--batch", str(bad), "--json"])
    assert rc == 0
    p = A.build_plan(db_path=db, batch_path=bad, machine_path=tmp_path / "m.jsonl")
    assert p["batch"]["bad_lines"] == 1 and p["counts"]["unique_ids"] == 1
    allbad = tmp_path / "allbad.jsonl"
    allbad.write_text("nope\n", encoding="utf-8")
    assert A.main(["--db", str(db), "--batch", str(allbad)]) == 1
