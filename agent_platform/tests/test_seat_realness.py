"""席位真成员化审计器测试（WO-SEAT-REALSTD-20261026）。

五维：授权/工具/触发/审计/回滚；用 tmp 仓夹具，不依赖真实组织与计划任务。
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "seat_realness.py"
_spec = importlib.util.spec_from_file_location("seat_realness", str(_TOOL))
SR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SR)


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_root(tmp_path, *, tool_file=True, prompt=True, rollback=True, events=True):
    root = tmp_path / "repo"
    _write(root / "agent_platform/registry/org.json", json.dumps({
        "departments": [{
            "dept": "x", "name": "X部", "cn_title": "测试府", "agents": ["T1"],
            "agent_profiles": {"T1": {"agent_id": "T1", "role_profile": "demo_role",
                                      "tools": ["demo_probe"], "grade": "E1"}},
        }]
    }, ensure_ascii=False))
    _write(root / "agent_platform/hr/T1/resume.yaml", "agent_id: T1\ncn_name: 测试\n")
    body = "# T1 · 测试岗（demo_role）\n\n## 回滚\n设置 `DEMO_PROBE=off` 即停。\n" if rollback else "# T1 · 测试岗（demo_role）\n"
    _write(root / "agent_platform/prompts/demo_role.md", body)
    _write(root / "portal/agent_ide_bundle.json", json.dumps({
        "agents": {"T1": {"role_profile": "demo_role", "tools": ["demo_probe"],
                          "prompt_file": "agent_platform/prompts/demo_role.md",
                          "entry_source": "org_profiles"}}
    }, ensure_ascii=False))
    if tool_file:
        _write(root / "agent_platform/tools/demo_probe.py", "# demo tool\n")
    if events:
        rec = {"day": time.strftime("%Y-%m-%d"), "kind": "patrol_verdict",
               "agent": "T1", "detail": "demo"}
        _write(root / "portal/data/dev_events.jsonl", json.dumps(rec, ensure_ascii=False) + "\n")
    return root


def test_full_member_scores_5(monkeypatch, tmp_path):
    root = make_root(tmp_path)
    monkeypatch.setattr(SR, "task_exists", lambda t: t == "Demo-Task")
    rep = SR.audit(root, "T1", task="Demo-Task")
    assert rep["score"] == 5 and rep["missing"] == []


def test_missing_tool_impl_fails_tools(monkeypatch, tmp_path):
    root = make_root(tmp_path, tool_file=False)
    monkeypatch.setattr(SR, "task_exists", lambda t: True)
    rep = SR.audit(root, "T1", task="Demo-Task")
    assert rep["rows"]["tools"]["ok"] is False
    assert rep["score"] == 4


def test_missing_prompt_fails_identity(monkeypatch, tmp_path):
    root = make_root(tmp_path)
    (root / "agent_platform/prompts/demo_role.md").unlink()
    monkeypatch.setattr(SR, "task_exists", lambda t: True)
    rep = SR.audit(root, "T1", task="Demo-Task")
    assert rep["rows"]["authorization"]["ok"] is False
    assert rep["rows"]["rollback"]["ok"] is False


def test_missing_events_fails_audit(monkeypatch, tmp_path):
    root = make_root(tmp_path, events=False)
    monkeypatch.setattr(SR, "task_exists", lambda t: True)
    rep = SR.audit(root, "T1", task="Demo-Task")
    assert rep["rows"]["audit"]["ok"] is False
    assert rep["score"] == 4


def test_no_rollback_marker_fails(monkeypatch, tmp_path):
    root = make_root(tmp_path, rollback=False)
    monkeypatch.setattr(SR, "task_exists", lambda t: True)
    rep = SR.audit(root, "T1", task="Demo-Task")
    assert rep["rows"]["rollback"]["ok"] is False


def test_no_task_means_trigger_unverified(monkeypatch, tmp_path):
    root = make_root(tmp_path)
    monkeypatch.setattr(SR, "task_exists", lambda t: True)
    rep = SR.audit(root, "T1", task="")
    assert rep["rows"]["trigger"]["ok"] is False


def test_main_exit_codes(monkeypatch, tmp_path, capsys):
    root = make_root(tmp_path)
    monkeypatch.setattr(SR, "task_exists", lambda t: True)
    rc = SR.main(["--root", str(root), "--seat", "T1", "--task", "Demo-Task", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["score"] == 5
    rc2 = SR.main(["--root", str(root), "--seat", "T1", "--json"])  # 无任务 → 4/5
    assert rc2 == 1
