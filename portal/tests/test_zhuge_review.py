# -*- coding: utf-8 -*-
"""诸葛亮 IDE 复核 P0 单测：默认关/校验/解析/异步/缓存。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL))
import zhuge_review as z  # noqa: E402


PAYLOAD = {
    "task_id": "WO-TEST-1",
    "diff": "--- a/x.py\n+++ b/x.py\n+print('x')\n",
    "changed_files": ["x.py"],
    "test_result": {"ok": True, "passed": 1, "failed": 0, "output_tail": "ok"},
    "context": {"goal": "review test", "acceptance": ["no break"]},
}


def test_default_off(monkeypatch):
    monkeypatch.delenv(z.FEATURE_ENV, raising=False)
    assert z.enabled() is False
    status, out = z.start_review(PAYLOAD)
    assert status == 404 and out["error"] == "zhuge_review_disabled"


def test_validate_limits(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    bad = dict(PAYLOAD, diff="")
    status, out = z.start_review(bad)
    assert status == 400
    status, out = z.start_review(dict(PAYLOAD, diff="x" * (z.MAX_DIFF + 1)))
    assert status == 400


def test_parse_review():
    raw = '前言\n```json\n' + json.dumps({
        "verdict": "concerns",
        "findings": [{"severity": "P1", "file": "x.py", "line": 3,
                      "claim": "c", "evidence": "e", "fix": "f"}],
        "questions": ["q"],
    }, ensure_ascii=False) + '\n```'
    out = z.parse_review(raw)
    assert out["verdict"] == "concerns"
    assert out["findings"][0]["file"] == "x.py"
    assert out["questions"] == ["q"]


def test_parse_bad_degrades():
    out = z.parse_review("not json")
    assert out["verdict"] == "degraded"
    assert out["findings"] == []


def test_async_done_and_cache(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    text = '{"verdict":"pass","findings":[],"questions":[]}'

    def fake_consult(prompt, timeout=90):
        assert "diff" in prompt
        return {"llm_ok": True, "text": text, "mode": "openclaw_cloud",
                "carrier": "deepseek/deepseek-chat", "sources": []}

    status, out = z.start_review(PAYLOAD, consult_fn=fake_consult)
    assert status == 202 and out["review_id"]
    rid = out["review_id"]
    for _ in range(50):
        got = z.get_review(rid)
        if got.get("status") != "pending":
            break
        time.sleep(0.02)
    assert got["status"] == "done"
    assert got["read_only"] is True
    assert got["verdict"] == "pass"

    status2, out2 = z.start_review(PAYLOAD, consult_fn=fake_consult)
    assert status2 == 200
    assert out2["cached"] is True


# ── 运营化（WO-LEGION-REVIEWOPS-20261031）─────────────────────────────
class _FakeExec:
    """不真正跑复核线程（只验证 202/429 与任务登记）。"""

    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


def _seed_done_job(review_id, findings, task_id="WO-TEST"):
    with z._LOCK:
        z._JOBS[review_id] = {
            "ts": time.time(), "key": "k-" + review_id, "task_id": task_id,
            "payload": {"ok": True, "review_id": review_id, "task_id": task_id,
                        "status": "done", "verdict": "concerns",
                        "findings": findings, "read_only": True},
        }


def test_finding_ids_stable_and_unique():
    raw = json.dumps({
        "verdict": "concerns",
        "findings": [
            {"severity": "P1", "file": "a.py", "line": 3,
             "claim": "c", "evidence": "e", "fix": "f"},
            {"severity": "P1", "file": "a.py", "line": 3,
             "claim": "c", "evidence": "e", "fix": "f"},
            {"severity": "P2", "file": "b.py", "line": 9,
             "claim": "d", "evidence": "x"},
        ],
        "questions": [],
    })
    first = z.parse_review(raw)
    second = z.parse_review(raw)
    ids = [f["id"] for f in first["findings"]]
    assert all(i.startswith("zf-") for i in ids)
    assert len(set(ids)) == 3, "同内容重复 finding 必须有唯一 id"
    assert ids == [f["id"] for f in second["findings"]], "同内容重算 id 幂等"
    assert ids[0] == z.finding_id("P1", "a.py", 3, "c", "e")
    assert ids[1] == ids[0] + "-2"


def test_rate_limit_per_session_injectable_clock(monkeypatch):
    monkeypatch.setattr(z, "RATE_PER_SESSION", 2)
    monkeypatch.setattr(z, "RATE_SESSION_WINDOW_S", 3600)
    monkeypatch.setattr(z, "CLOCK", lambda: 1000.0)
    z._RATE_BY_SESSION.clear()
    assert z._rate_check("sess-a") == (True, 0)
    assert z._rate_check("sess-a") == (True, 0)
    ok, retry = z._rate_check("sess-a")
    assert ok is False and retry == 3601, "超限须给 retry_after_s"
    assert z.rate_state("sess-a")["used"] == 2
    assert z._rate_check("sess-b")[0] is True, "不同 session 独立限额"
    monkeypatch.setattr(z, "CLOCK", lambda: 1000.0 + 3601)
    assert z._rate_check("sess-a") == (True, 0), "窗口滑过后恢复"


def test_start_review_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    monkeypatch.setattr(z, "RATE_PER_SESSION", 1)
    monkeypatch.setattr(z, "CLOCK", lambda: 5000.0)
    z._RATE_BY_SESSION.clear()
    z._CACHE.clear()
    fake_exec = _FakeExec()
    monkeypatch.setattr(z, "_EXECUTOR", fake_exec)
    status, out = z.start_review(dict(PAYLOAD, diff="diff-one"), session_key="sess-x")
    assert status == 202 and out["status"] == "pending"
    assert len(fake_exec.calls) == 1
    status2, out2 = z.start_review(dict(PAYLOAD, diff="diff-two"),
                                   session_key="sess-x")
    assert status2 == 429
    assert out2["error"] == "rate_limited"
    assert out2["retry_after_s"] == 3601
    assert out2["scope"] == "session"
    assert len(fake_exec.calls) == 1, "被限流不得登记新任务"


def test_status_reports_rate_worker_and_adoption_schema():
    st = z.status()
    assert st["rate_limit"]["per_session_per_window"] == z.RATE_PER_SESSION
    assert st["rate_limit"]["window_s"] == z.RATE_SESSION_WINDOW_S
    assert st["worker"]["max_workers"] == 1, "全局单 worker"
    assert st["adoption"]["schema"] == z.ADOPTION_SCHEMA


def test_adopt_findings_persists_and_get_returns(monkeypatch, tmp_path):
    ledger = tmp_path / "zhuge_review_adoption.jsonl"
    monkeypatch.setattr(z, "ADOPTION_PATH", ledger)
    rid = "zr-adopt-1"
    _seed_done_job(rid, [{"id": "zf-aaa"}, {"id": "zf-bbb"}])
    out = z.adopt_findings(rid, adopted=["zf-aaa"], ignored=["zf-bbb"],
                           note="确认采纳/忽略", by="qa")
    assert out["ok"] is True
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == z.ADOPTION_SCHEMA
    assert row["review_id"] == rid and row["task_id"] == "WO-TEST"
    assert row["adopted"] == ["zf-aaa"] and row["ignored"] == ["zf-bbb"]
    assert row["note"] == "确认采纳/忽略" and row["by"] == "qa"
    assert row["ts"], "ts 必填"
    got = z.get_review(rid)
    assert got["adoption"]["adopted"] == ["zf-aaa"], "GET review 须返回 adoption"


def test_adopt_findings_rejects_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(z, "ADOPTION_PATH", tmp_path / "a.jsonl")
    rid = "zr-adopt-2"
    _seed_done_job(rid, [{"id": "zf-aaa"}])
    assert z.adopt_findings("zr-missing", adopted=["zf-aaa"])["error"] == "review_not_found"
    assert z.adopt_findings(rid, adopted=["zf-nope"])["error"] == "bad_finding_id"
    assert z.adopt_findings(rid, adopted=["zf-aaa"],
                            ignored=["zf-aaa"])["error"] == "conflict_finding_id"
    assert z.adopt_findings(rid)["error"] == "nothing_to_record"
    with z._LOCK:
        z._JOBS["zr-adopt-pending"] = {"ts": time.time(), "payload": None, "key": "k2"}
    assert z.adopt_findings("zr-adopt-pending",
                            adopted=["zf-aaa"])["error"] == "review_not_ready"


def test_adoption_only_after_job_expired(monkeypatch, tmp_path):
    ledger = tmp_path / "a.jsonl"
    monkeypatch.setattr(z, "ADOPTION_PATH", ledger)
    rid = "zr-adopt-expired"
    _seed_done_job(rid, [{"id": "zf-aaa"}])
    assert z.adopt_findings(rid, adopted=["zf-aaa"], by="qa")["ok"] is True
    with z._LOCK:
        z._JOBS.pop(rid, None)
    got = z.get_review(rid)
    assert got["status"] == "adoption_only"
    assert got["adoption"]["review_id"] == rid
    assert z.get_review("zr-not-exist")["error"] == "review_not_found"
