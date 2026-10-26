# -*- coding: utf-8 -*-
"""记忆 v2 测试（WO-MEMORY-V2）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "memory_store", str(_WT / "agent_platform" / "memory" / "store.py"))
MS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MS)


def _db(tmp_path, monkeypatch):
    db = tmp_path / "memory.sqlite"
    monkeypatch.setattr(MS, "DB", db)
    monkeypatch.setattr(MS, "EVAL_OUT", tmp_path / "eval_latest.json")
    return db


def test_add_search_layers(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    MS.add("semantic", "假成功：脚本报成功但目标未修改", "检查退出码之外还要校验目标对象",
           mid="semantic:e1", confidence=0.9)
    MS.add("procedural", "技能 task-exit-audit", "python tools/task_exit_audit.py",
           mid="procedural:s1")
    rep = MS.search("假成功 目标未修改")
    assert rep["ok"] and rep["items"]
    assert rep["items"][0]["id"] == "semantic:e1"
    st = MS.stats()
    assert st["total"] == 2 and st["layers"]["semantic"] == 1


def test_ingest_experience_requests_skills(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    prof = tmp_path / "profile"
    (prof / "experience").mkdir(parents=True)
    (prof / "experience" / "_index.jsonl").write_text(json.dumps({
        "entry_id": "EXP-1", "cue": "快照假绿", "lesson": "校验要两层",
        "confidence": 0.9, "domain": "supervision"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    plat = tmp_path / "plat"
    req = plat / "data" / "zhaoyun_requests"
    req.mkdir(parents=True)
    (req / "ZYREQ-1.json").write_text(json.dumps({
        "id": "ZYREQ-1", "kind": "memory_quarantine", "status": "pending_human",
        "reason": "旧线索"}, ensure_ascii=False), encoding="utf-8")
    skills = plat / "skills"
    skills.mkdir()
    (skills / "registry.json").write_text(json.dumps({"total": 1, "skills": [
        {"name": "health-card", "status": "ready", "verify_cmd": "python v.py"}]}),
        encoding="utf-8")
    a = MS.ingest_experience(prof)
    b = MS.ingest_requests(plat)
    c = MS.ingest_skills(plat)
    assert a["added"] == 1 and b["added"] == 1 and c["added"] == 1
    st = MS.stats()
    assert st["layers"]["semantic"] == 1 and st["layers"]["episodic"] == 1
    assert st["layers"]["procedural"] == 1


def test_prune_dry_run_and_apply(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    MS.add("semantic", "old", "x", mid="semantic:old", confidence=0.1, ttl_days=0)
    rep = MS.prune(dry_run=True)
    assert rep["dry_run"] and rep["count"] >= 1
    assert MS.stats()["total"] == 1
    applied = MS.prune(dry_run=False)
    assert applied["count"] >= 1 and MS.stats()["total"] == 0


def test_run_eval(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    MS.add("semantic", "假成功 检测目标对象", "修复后要校验目标", mid="semantic:e1")
    MS.add("semantic", "快照假绿 LastTaskResult", "快照不能当成功", mid="semantic:e2")
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"queries": [
        {"query": "假成功 目标", "expected_any": ["假成功"]},
        {"query": "快照假绿", "expected_any": ["快照假绿"]},
        {"query": "完全无关查询xyz", "expected_any": ["不存在"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    rep = MS.run_eval(q)
    assert rep["ok"] and rep["total"] == 3
    assert rep["hit@1"] >= 2 and rep["hit3_rate"] >= 0.6 and rep["mrr"] > 0.6
