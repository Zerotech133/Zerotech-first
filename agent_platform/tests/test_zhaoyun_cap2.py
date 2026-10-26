"""赵云能力二期测试：审批闭环/干预建议/记忆策展/路由/自检/留证（WO-ZHAOYUN-CAP2）。"""
from __future__ import annotations

import importlib.util
import json
import shutil
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_LC = _WT / "agent_platform" / "legion_command"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ZD = _load("zhaoyun_drill", _LC / "zhaoyun_drill.py")
C2 = _load("zhaoyun_cap2", _LC / "zhaoyun_cap2.py")


def _fixture(tmp_path):
    platform = tmp_path / "agent_platform"
    (platform / "data").mkdir(parents=True)
    (platform / "tools").mkdir(parents=True)
    for f in ("capability_self_audit.py", "model_router.py"):
        shutil.copy(_WT / "agent_platform" / "tools" / f, platform / "tools" / f)
    repo = tmp_path
    (repo / ".worktrees").mkdir(exist_ok=True)
    (repo / ".worktrees" / "tasks.json").write_text(json.dumps({
        "t-stalled": {"status": "review", "owner": "X", "desc": "旧 review",
                      "created": "2026-10-20 00:00:00"},
        "t-unowned": {"status": "doing", "owner": "unknown-agent",
                      "desc": "无主 [UNOWNED]", "created": "2026-10-27 04:00:00"},
    }, ensure_ascii=False), encoding="utf-8")
    prof = tmp_path / "profile"
    (prof / "skills").mkdir(parents=True)
    (prof / "skills" / "CAPABILITIES.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": 1, "ready": 1,
        "capabilities": [{"name": "c1", "how": "人工", "status": "ready"}]},
        ensure_ascii=False), encoding="utf-8")
    (prof / "experience").mkdir()
    (prof / "experience" / "_index.jsonl").write_text("\n".join([
        json.dumps({"entry_id": "e1", "cue": "任务退出码审计结果偏差", "confidence": 0.9}),
        json.dumps({"entry_id": "e2", "cue": "任务退出码审计结果偏差分析", "confidence": 0.8}),
        json.dumps({"entry_id": "e3", "cue": "低置信线索", "confidence": 0.4}),
    ]) + "\n", encoding="utf-8")
    return platform, prof


def test_approve_memory_quarantine_executes(tmp_path, monkeypatch):
    platform, _ = _fixture(tmp_path)
    monkeypatch.setattr(ZD, "_emit", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(C2, "_emit_verdict", lambda *a, **k: {"ok": True})
    req = ZD.write_request(platform, "memory_quarantine", {"entry_ids": ["e1", "e2"]},
                           "过时线索")["request"]
    res = C2.approve(platform, req["id"], "approve", "老田", "同意")
    assert res["ok"] and res["request"]["status"] == "approved"
    assert res["request"]["applied"] is True
    q = platform / "data" / "zhaoyun_memory" / "quarantine.jsonl"
    lines = q.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and "e1" in lines[0]


def test_reject_and_guards(tmp_path, monkeypatch):
    platform, _ = _fixture(tmp_path)
    monkeypatch.setattr(ZD, "_emit", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(C2, "_emit_verdict", lambda *a, **k: {"ok": True})
    req = ZD.write_request(platform, "capability_learn", {"name": "x", "what": "y"},
                           "提案")["request"]
    assert C2.approve(platform, req["id"], "approve", "")["error"] == "signer_required"
    assert C2.approve(platform, req["id"], "maybe", "老田")["error"] == "bad_decision"
    assert C2.approve(platform, "NO-SUCH", "approve", "老田")["error"] == "request_not_found"
    res = C2.approve(platform, req["id"], "reject", "老田", "暂不")
    assert res["ok"] and res["request"]["status"] == "rejected"
    assert res["request"]["applied"] is False
    plan = platform / "data" / "zhaoyun_capabilities" / "pending_learn.jsonl"
    assert not plan.exists()


def test_approve_learn_and_model(tmp_path, monkeypatch):
    platform, _ = _fixture(tmp_path)
    monkeypatch.setattr(ZD, "_emit", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(C2, "_emit_verdict", lambda *a, **k: {"ok": True})
    r1 = ZD.write_request(platform, "capability_learn",
                          {"name": "cap-new", "what": "新能力"}, "学")["request"]
    assert C2.approve(platform, r1["id"], "approve", "老田")["request"]["applied"] is True
    r2 = ZD.write_request(platform, "model_change",
                          {"seat": "R2", "candidate": "deepseek/deepseek-flash"},
                          "换模型")["request"]
    res = C2.approve(platform, r2["id"], "approve", "老田")
    assert res["request"]["applied"] is True
    ov = json.loads((platform / "data" / "zhaoyun_model_overrides.json")
                    .read_text(encoding="utf-8"))
    assert ov["overrides"][-1]["seat"] == "R2"


def test_interventions_and_curation(tmp_path):
    platform, prof = _fixture(tmp_path)
    iv = C2.interventions(platform)
    assert iv["ok"] and iv["count"] >= 1
    kinds = {i["kind"] for i in iv["items"]}
    assert "review_stalled" in kinds or "unowned" in kinds
    cur = C2.memory_curation(prof)
    kinds2 = {s["kind"] for s in cur["suggestions"]}
    assert "duplicate" in kinds2 and "low_confidence" in kinds2


def test_selfaudit_routing_usage(tmp_path):
    platform, prof = _fixture(tmp_path)
    audit = C2.selfaudit(platform, prof)
    assert "drift" in audit
    routing = C2.routing(platform)
    assert routing["ok"] and routing["plans"]["governance"]
    C2.log_usage(platform, "task-exit-audit", "test")
    last = C2.last_used(platform, "task-exit-audit")
    assert last["count"] == 1 and last["last"]["source"] == "test"
