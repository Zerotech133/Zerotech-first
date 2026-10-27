# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_evolver as E  # noqa: E402


def _setup(tmp_path, monkeypatch):
    missions = tmp_path / "missions"
    exp = tmp_path / "experience"
    monkeypatch.setattr(E, "MISSIONS", missions)
    monkeypatch.setattr(E, "EXPERIENCE", exp)
    monkeypatch.setattr(E, "CANDIDATES", exp / "candidates")
    monkeypatch.setattr(E, "ACCEPTED", exp / "accepted.jsonl")
    m = missions / "M-1"
    m.mkdir(parents=True)
    (m / "plan.json").write_text(json.dumps({
        "schema": "mission_plan/v1", "mission_id": "M-1", "name": "演示", "source": "llm",
        "tasks": [{"id": "T1"}, {"id": "T2"}]}), encoding="utf-8")
    (m / "dry_run.json").write_text(json.dumps({"conflicts": [{"a": "T1", "b": "T2"}]}), encoding="utf-8")
    (m / "state.json").write_text(json.dumps({
        "mission_id": "M-1", "parallel_limit": 3,
        "tasks": {
            "T1": {"owner": "ARCH1", "status": "done", "attempts": 1, "model": "strong",
                   "ticket_id": "K-T1", "started_at": "2026-10-27T10:00:00+0800",
                   "done_at": "2026-10-27T10:05:00+0800", "gate_detail": "ok"},
            "T2": {"owner": "DEV1", "status": "gate_failed", "attempts": 1, "model": "auto",
                   "ticket_id": "K-T2", "gate_detail": "FAIL secret scan"},
        }}), encoding="utf-8")


def test_collect_summary_and_candidate(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    recs = E.collect()
    assert len(recs) == 1 and recs[0]["tasks_done"] == 1 and recs[0]["tasks_gate_failed"] == 1
    m = E.summarize(recs)
    assert m["missions"] == 1 and m["done"] == 1 and m["gate_failed"] == 1
    assert m["gate_pass_rate"] == 0.5
    d = E.evolve()
    assert d["status"] == "candidate" and d["requires_signature"] is True
    assert (E.CANDIDATES / (d["candidate_id"] + ".json")).exists()
    assert E.list_candidates()[0]["candidate_id"] == d["candidate_id"]
    assert (E.EXPERIENCE / "latest.json").exists()


def test_promote_requires_signature(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    d = E.evolve()
    bad = E.promote(d["candidate_id"], "")
    assert bad["ok"] is False and bad["error"] == "signed_by_required"
    good = E.promote(d["candidate_id"], "老田", "批准")
    assert good["ok"] is True
    assert E.ACCEPTED.exists() and "老田" in E.ACCEPTED.read_text(encoding="utf-8")


def test_no_auto_apply():
    assert E.load_latest() == {} or E.load_latest().get("status") == "candidate"
