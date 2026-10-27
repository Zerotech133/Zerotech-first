# -*- coding: utf-8 -*-
"""研究流水线状态机测试（WO-RESEARCH-FSM-20261027）。

覆盖：8 阶段顺序、artifact schema、状态持久化/恢复、审批门、dry-run 零写。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "research_fsm.py"


def _run(root: Path, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def _ok(r):
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _art(stage: str) -> dict:
    return {
        "intake": {"title": "T", "question": "Q", "object": "O"},
        "hypothesis": {"hypothesis": "H", "falsify_conditions": ["F"], "evidence_scope": "S"},
        "evidence": {"evidence": ["E"], "sources": ["N"]},
        "validation": {"method": "M", "metrics": "X", "result_summary": "R"},
        "review": {"reviewer": "QA1", "verdict": "pass", "notes": "ok"},
        "joint_review": {"participants": ["A"], "verdict": "pass", "gaps": []},
        "decision": {"decision": "go", "reason": "R", "signed_by": "老田"},
        "closed": {"closure_summary": "C", "archive_path": "deliverables/x.md"},
    }[stage]


def _advance(root: Path, topic: str, to: str, stage: str):
    return _run(root, "advance", "--topic", topic, "--to", to,
                "--artifact", json.dumps(_art(stage), ensure_ascii=False))


def test_full_flow_with_gates_and_resume(tmp_path):
    topic = "AC-TEST-1"
    out = _ok(_run(tmp_path, "init", "--topic", topic, "--title", "测试课题"))
    assert out["state"]["stage"] == "intake"
    # 非法跳阶段
    r = _run(tmp_path, "advance", "--topic", topic, "--to", "evidence",
             "--artifact", json.dumps(_art("intake")))
    assert r.returncode == 2
    # 缺 schema 字段
    r = _run(tmp_path, "advance", "--topic", topic, "--to", "hypothesis",
             "--artifact", json.dumps({"title": "T"}))
    assert r.returncode == 2
    # 正常推进到 decision
    order = ["hypothesis", "evidence", "validation", "review", "joint_review", "decision"]
    for to in order:
        out = _ok(_advance(tmp_path, topic, to, STAGES_CUR[to]))
        assert out["to"] == to
    # decision 是审批门：未批不能走
    r = _run(tmp_path, "advance", "--topic", topic, "--to", "closed",
             "--artifact", json.dumps(_art("decision")))
    assert r.returncode == 3
    # 拒绝 AI 代签
    r = _run(tmp_path, "approve", "--topic", topic, "--by", "AI")
    assert r.returncode == 2
    # 人类审批后放行
    out = _ok(_run(tmp_path, "approve", "--topic", topic, "--by", "老田", "--note", "同意"))
    assert out["approval"]["by"] == "老田"
    # 用新进程读状态继续（恢复）
    st = _ok(_run(tmp_path, "status", "--topic", topic))
    assert st["stage"] == "decision" and st["gated"] is True
    out = _ok(_advance(tmp_path, topic, "closed", "decision"))
    assert out["stage"] == "closed"
    st = _ok(_run(tmp_path, "status", "--topic", topic))
    assert st["history_n"] == 7 and st["next"] is None


STAGES_CUR = {
    "hypothesis": "intake",
    "evidence": "hypothesis",
    "validation": "evidence",
    "review": "validation",
    "joint_review": "review",
    "decision": "joint_review",
    "closed": "decision",
}


def test_dry_run_zero_write(tmp_path):
    out = _ok(_run(tmp_path, "--dry-run", "init", "--topic", "AC-DRY"))
    assert out["dry_run"] is True
    assert not (tmp_path / "agent_platform" / "data" / "academy" / "research_fsm").exists()
    _ok(_run(tmp_path, "init", "--topic", "AC-DRY"))
    r = _run(tmp_path, "--dry-run", "advance", "--topic", "AC-DRY", "--to", "hypothesis",
             "--artifact", json.dumps(_art("intake")))
    assert r.returncode == 0
    st = _ok(_run(tmp_path, "status", "--topic", "AC-DRY"))
    assert st["stage"] == "intake"


def test_duplicate_init_and_missing_state(tmp_path):
    _ok(_run(tmp_path, "init", "--topic", "AC-DUP"))
    r = _run(tmp_path, "init", "--topic", "AC-DUP")
    assert r.returncode == 2
    r = _run(tmp_path, "status", "--topic", "AC-MISSING")
    assert r.returncode == 2


def test_stage_definitions():
    # 顺序与 schema 由 CLI 行为覆盖；此处校验 8 阶段齐全且 gated 为 decision/closed
    sys.path.insert(0, str(TOOL.parent))
    import research_fsm as F
    assert F.STAGES == ["intake", "hypothesis", "evidence", "validation",
                        "review", "joint_review", "decision", "closed"]
    assert F.GATED == {"decision", "closed"}
    for stage in F.STAGES:
        assert F.REQUIRED[stage], stage
