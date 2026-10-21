# -*- coding: utf-8 -*-
"""晨报离线审核闸：所有测试只用 tmp_path，不访问生产账本或发送端。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zhuge_morning_review as review

DAY = "2026-10-21"
STAMP = "2026-10-21T07:00:00+08:00"


def artifact(tmp_path, **changes):
    data = {"business_day": DAY, "generated_at": STAMP, "engine": "rule-based",
            "model_invoked": False, "dispatched": False,
            "suggestions": [{"id": "one", "priority": 1, "reason": "核查通知", "source": "tasks"}],
            "gaps": []}
    data.update(changes)
    p = tmp_path / "brief.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def approval(prepared, **changes):
    a = {"decision": "approved", "reviewer": "laotian", "recipient": "laotian_self",
         "business_day": DAY, "content_sha256": prepared["content_sha256"],
         "idempotency_key": prepared["idempotency_key"], "approved_at": STAMP}
    a.update(changes)
    return a


def test_prepare_idempotent_and_content_revision_distinct(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    p = artifact(tmp_path)
    a = review.prepare(p, db, now=STAMP)
    b = review.prepare(p, db, now=STAMP)
    assert a["ok"] and not a["duplicate"] and b["duplicate"]
    assert a["idempotency_key"] == b["idempotency_key"]
    assert a["state"] == "pending_review"
    p = artifact(tmp_path, suggestions=[{"id": "two", "priority": 2, "reason": "变化", "source": "os"}])
    c = review.prepare(p, db, now=STAMP)
    assert c["idempotency_key"] != a["idempotency_key"]
    assert len(review.events(db, a["idempotency_key"])) == 2
    assert review.state(db, a["idempotency_key"])["state"] == "superseded_offline"
    assert not review.approve(db, a["idempotency_key"], approval(a), now=STAMP)["ok"]
    assert not review.test_receipt(db, a["idempotency_key"],
                                   {"status": "simulated", "idempotency_key": a["idempotency_key"],
                                    "content_sha256": a["content_sha256"], "recipient": "laotian_self"}, now=STAMP)["ok"]


@pytest.mark.parametrize("changes", [
    {"business_day": "2026-10-20"}, {"dispatched": True},
    {"engine": "model"}, {"model_invoked": True},
    {"suggestions": "not an array"}, {"generated_at": "2026-10-20T07:00:00+08:00"},
])
def test_invalid_artifact_denied_with_failure_trace(tmp_path, changes):
    db = tmp_path / "ledger.sqlite3"
    r = review.prepare(artifact(tmp_path, **changes), db, now=STAMP)
    assert not r["ok"] and r["state"] == "blocked"
    assert review.events(db)[-1]["state"] == "blocked"


def test_missing_or_corrupt_artifact_denied(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    assert not review.prepare(tmp_path / "missing.json", db, now=STAMP)["ok"]
    p = tmp_path / "broken.json"
    p.write_text("{bad", encoding="utf-8")
    assert not review.prepare(p, db, now=STAMP)["ok"]
    assert [x["state"] for x in review.events(db)] == ["blocked", "blocked"]


def test_approval_requires_exact_binding_and_never_proves_identity(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    a = review.prepare(artifact(tmp_path), db, now=STAMP)
    for changes in ({"recipient": "anyone"}, {"content_sha256": "0" * 64},
                    {"idempotency_key": "bad"}, {"reviewer": ""},
                    {"decision": "rejected"}, {"approved_at": "bad"}):
        result = review.approve(db, a["idempotency_key"], approval(a, **changes), now=STAMP)
        assert not result["ok"]
    assert review.state(db, a["idempotency_key"])["state"] == "pending_review"
    ok = review.approve(db, a["idempotency_key"], approval(a), now=STAMP)
    assert ok["ok"] and ok["state"] == "approved_offline"
    assert ok["identity_verified"] is False
    assert review.approve(db, a["idempotency_key"], approval(a), now=STAMP)["duplicate"]
    assert not review.approve(db, a["idempotency_key"], approval(a, reviewer="other"), now=STAMP)["ok"]


def test_approved_old_version_cannot_be_reapproved_after_supersede(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    p = artifact(tmp_path)
    old = review.prepare(p, db, now=STAMP)
    assert review.approve(db, old["idempotency_key"], approval(old), now=STAMP)["ok"]
    artifact(tmp_path, suggestions=[{"id": "new", "priority": 2, "reason": "新版本", "source": "os"}])
    assert review.prepare(p, db, now=STAMP)["ok"]
    assert not review.prepare(artifact(tmp_path, suggestions=[{"id": "one", "priority": 1,
                     "reason": "核查通知", "source": "tasks"}]), db, now=STAMP)["ok"]
    assert not review.approve(db, old["idempotency_key"], approval(old), now=STAMP)["ok"]
    assert review.state(db, old["idempotency_key"])["state"] == "superseded_offline"


def test_receipt_is_simulation_only_and_unknown_or_mismatch_fail_closed(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    a = review.prepare(artifact(tmp_path), db, now=STAMP)
    assert not review.test_receipt(db, a["idempotency_key"], {"status": "api_accepted"}, now=STAMP)["ok"]
    review.approve(db, a["idempotency_key"], approval(a), now=STAMP)
    for receipt in ({"status": "delivered"}, {"status": "api_accepted", "idempotency_key": "wrong"},
                    {"status": "simulated", "idempotency_key": "wrong"}):
        assert not review.test_receipt(db, a["idempotency_key"], receipt, now=STAMP)["ok"]
    result = review.test_receipt(db, a["idempotency_key"],
                                 {"status": "simulated", "idempotency_key": a["idempotency_key"],
                                  "content_sha256": a["content_sha256"], "recipient": "laotian_self"}, now=STAMP)
    assert result["ok"] and result["state"] == "receipt_tested_offline"
    assert result["delivered"] is False
    assert review.state(db, a["idempotency_key"])["delivered"] is False
    assert review.test_receipt(db, a["idempotency_key"],
                               {"status": "simulated", "idempotency_key": a["idempotency_key"],
                                "content_sha256": a["content_sha256"], "recipient": "laotian_self"},
                               now=STAMP)["duplicate"]


def test_missing_ledger_does_not_initialize_on_read(tmp_path):
    db = tmp_path / "absent.sqlite3"
    assert review.state(db, "absent") is None
    assert review.events(db) == []
    assert not db.exists()


def test_unwritable_ledger_fails_closed_without_claiming_audit(tmp_path):
    p = artifact(tmp_path)
    (tmp_path / "ledger.sqlite3").write_text("occupied", encoding="utf-8")
    r = review.prepare(p, tmp_path / "ledger.sqlite3" / "cannot-create", now=STAMP)
    assert not r["ok"] and r["state"] == "blocked"
    assert r["audit_persisted"] is False


def test_read_only_query_never_creates_tables(tmp_path):
    db = tmp_path / "empty.sqlite3"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert review.state(db, "none") is None
    assert review.events(db) == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_write_connections_closed_after_success_and_block(tmp_path, monkeypatch):
    real_connect = review.sqlite3.connect
    opened = []

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            self.was_closed = True
            return super().close()

    def tracked_connect(*args, **kwargs):
        conn = real_connect(*args, factory=TrackedConnection, **kwargs)
        conn.was_closed = False
        opened.append(conn)
        return conn

    monkeypatch.setattr(review.sqlite3, "connect", tracked_connect)
    db = tmp_path / "ledger.sqlite3"
    prepared = review.prepare(artifact(tmp_path), db, now=STAMP)
    assert prepared["ok"]
    assert not review.approve(db, "missing", {}, now=STAMP)["ok"]
    assert review.approve(db, prepared["idempotency_key"], approval(prepared), now=STAMP)["ok"]
    assert opened and all(conn.was_closed for conn in opened)


def test_no_transport_or_scheduler_imports():
    src = Path(review.__file__).read_text(encoding="utf-8")
    for banned in ("urllib", "requests", "socket", "subprocess", "schtasks", "send_msg", "PUSH_DECISION_EXECUTE"):
        assert banned not in src
