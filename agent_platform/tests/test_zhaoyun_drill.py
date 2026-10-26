"""赵云能力下钻模块测试（WO-ZHAOYUN-DRILL）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "legion_command" / "zhaoyun_drill.py"
_spec = importlib.util.spec_from_file_location("zhaoyun_drill", str(_TOOL))
ZD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ZD)


def _fixture(tmp_path):
    platform = tmp_path / "agent_platform"
    repo = tmp_path
    prof = tmp_path / "profile"
    (prof / "skills").mkdir(parents=True)
    (prof / "skills" / "CAPABILITIES.json").write_text(json.dumps({
        "generated_at": "2026-10-27T00:00:00+08:00", "total": 2, "ready": 1,
        "capabilities": [
            {"name": "cap-a", "what": "A 能力", "how": "python agent_platform/tools/x.py",
             "boundary": "只读", "status": "ready"},
            {"name": "cap-b", "what": "B 能力", "how": "无脚本", "status": "pending_key"},
        ]}, ensure_ascii=False), encoding="utf-8")
    (prof / "experience").mkdir(parents=True)
    (prof / "experience" / "_index.jsonl").write_text(
        json.dumps({"entry_id": "e1", "domain": "supervision", "cue": "线索",
                    "confidence": "high", "verified": True}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (repo / "portal" / "data").mkdir(parents=True)
    (repo / "portal" / "agent_ide_bundle.json").write_text(json.dumps(
        {"agents": {"ZHAOYUN": {"cn_name": "赵云", "llm": "deepseek/deepseek-flash"},
                    "R2": {"cn_name": "赵云", "llm": "ollama/qwen3.5:9b"}}},
        ensure_ascii=False), encoding="utf-8")
    (repo / "portal" / "data" / "dev_events.jsonl").write_text(
        json.dumps({"ts": "2026-10-27 00:00:00", "kind": "patrol_verdict",
                    "agent": "ZHAOYUN", "detail": "x"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (repo / ".worktrees").mkdir()
    (repo / ".worktrees" / "tasks.json").write_text(json.dumps({
        "old-doing": {"status": "doing", "owner": "ZHAOYUN",
                      "desc": "老单", "created": "2026-10-24 00:00:00",
                      "worktree": str(tmp_path / "gone")},
        "fresh-doing": {"status": "doing", "owner": "X", "desc": "新单",
                        "created": "2026-10-27 05:00:00", "worktree": ""},
        "unowned-review": {"status": "review", "owner": "unknown-agent",
                           "desc": "无主 [UNOWNED]", "created": "2026-10-27 04:00:00"},
    }, ensure_ascii=False), encoding="utf-8")
    return platform, prof


def test_capabilities_detail(tmp_path):
    platform, prof = _fixture(tmp_path)
    rep = ZD.capabilities_detail(platform, prof)
    assert rep["ok"] and rep["total"] == 2 and rep["ready"] == 1
    assert rep["gaps"], "缺少能力缺口清单"
    assert rep["items"][0]["name"] == "cap-a"
    assert rep["items"][0]["how_ref_exists"] is False


def test_tasks_view_deviations(tmp_path):
    platform, _ = _fixture(tmp_path)
    rep = ZD.tasks_view(platform)
    assert rep["ok"] and rep["counts"]["doing"] == 2 and rep["counts"]["review"] == 1
    kinds = {d["kind"] for d in rep["deviations"]}
    assert "doing_stalled" in kinds and "unowned" in kinds and "worktree_missing" in kinds


def test_memory_and_model_view(tmp_path):
    platform, prof = _fixture(tmp_path)
    mem = ZD.memory_view(platform, prof)
    assert mem["ok"] and mem["entries_total"] == 1
    assert mem["items"][0]["entry_id"] == "e1"
    mdl = ZD.model_view(platform)
    assert mdl["ok"] and {r["seat"] for r in mdl["seats"]} == {"ZHAOYUN", "R2"}
    assert mdl["candidate_models"]


def test_write_request_validates_and_persists(tmp_path, monkeypatch):
    platform, _ = _fixture(tmp_path)
    calls = []
    monkeypatch.setattr(ZD, "_emit", lambda rid, kind, reason: calls.append((rid, kind)) or {"ok": True})
    bad = ZD.write_request(platform, "drop_all", {}, "x")
    assert bad["ok"] is False and bad["error"] == "bad_kind"
    good = ZD.write_request(platform, "memory_quarantine",
                            {"entry_ids": ["e1"]}, "线索过时")
    assert good["ok"] and good["request"]["status"] == "pending_human"
    files = list((platform / "data" / "zhaoyun_requests").glob("ZYREQ-*.json"))
    assert len(files) == 1
    assert calls and calls[0][1] == "memory_quarantine"
    req = ZD.requests_view(platform)
    assert req["pending"] == 1


def test_memory_quarantine_list(tmp_path):
    platform, prof = _fixture(tmp_path)
    q = platform / "data" / "zhaoyun_memory"
    q.mkdir(parents=True)
    (q / "quarantine.jsonl").write_text(json.dumps({"entry_id": "e2"}) + "\n",
                                        encoding="utf-8")
    rep = ZD.memory_view(platform, prof)
    assert rep["quarantined_total"] == 1
