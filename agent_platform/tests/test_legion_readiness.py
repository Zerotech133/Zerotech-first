# -*- coding: utf-8 -*-
"""legion_readiness 测试（合成最小 org/tasks）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import legion_readiness as LR  # noqa: E402


def _mk(tmp_path):
    org = {"departments": [{"dept": "dev", "lead": "ARCH1", "deputy": "DEV1",
                            "groups": [{"id": "G1", "lead": "DEV1",
                                        "members": ["DEV6"]}],
                            "agent_profiles": [
        {"agent_id": "ARCH1", "cn_name": "曹操", "role_title": "架构",
         "discipline": "architecture", "grade": "E3", "group": "",
         "report_to": "A0", "role_profile": "arch",
         "attributes": {"specialties": ["x"], "tools_allowed": {"write": ["a"]}},
         "matrix_assignments": [1]},
        {"agent_id": "DEV1", "cn_name": "马钧", "role_title": "后端",
         "discipline": "backend", "grade": "E3", "group": "G1",
         "report_to": "ARCH1", "role_profile": "code",
         "attributes": {"specialties": ["y"], "tools_allowed": {"write": ["b"]}},
         "matrix_assignments": [1]},
        {"agent_id": "DEV5", "cn_name": "邓艾", "role_title": "前端",
         "discipline": "frontend", "grade": "E3", "group": "G2",
         "report_to": "ARCH1", "role_profile": "ui",
         "attributes": {"specialties": ["z"], "tools_allowed": {"write": ["c"]}},
         "matrix_assignments": [1]},
        {"agent_id": "DEV2", "cn_name": "x", "role_title": "AI",
         "discipline": "ai", "grade": "E2", "group": "G1",
         "report_to": "DEV1", "role_profile": "ai",
         "attributes": {"specialties": ["a"], "tools_allowed": {"write": ["d"]}},
         "matrix_assignments": [1]},
        {"agent_id": "DEV3", "cn_name": "x", "role_title": "量化",
         "discipline": "quant", "grade": "E2", "group": "G2",
         "report_to": "DEV5", "role_profile": "q",
         "attributes": {"specialties": ["q"], "tools_allowed": {"write": ["e"]}},
         "matrix_assignments": [1]},
        {"agent_id": "OPS1", "cn_name": "x", "role_title": "运维",
         "discipline": "devops", "grade": "E3", "group": "G1",
         "report_to": "DEV1", "role_profile": "ops",
         "attributes": {"specialties": ["o"], "tools_allowed": {"write": ["f"]}},
         "matrix_assignments": [1]},
        {"agent_id": "DOC1", "cn_name": "x", "role_title": "文档",
         "discipline": "docs", "grade": "E2", "group": "G2",
         "report_to": "DEV5", "role_profile": "doc",
         "attributes": {"specialties": ["d"], "tools_allowed": {"write": ["g"]}},
         "matrix_assignments": [1]},
    ]}]}
    (tmp_path / "agent_platform/registry").mkdir(parents=True)
    (tmp_path / "agent_platform/registry/org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (tmp_path / ".worktrees").mkdir()
    (tmp_path / ".worktrees/tasks.json").write_text(json.dumps({
        "T1": {"dept": "dev", "owner": "DEV1", "status": "done",
               "merged": "2026-10-28 10:00", "created": "2026-10-28 09:00"},
        "T2": {"dept": "dev", "owner": "DSH-Lead", "status": "done",
               "merged": "2026-10-28 11:00", "created": "2026-10-28 10:00"},
        "T3": {"dept": "dev", "owner": "DEV1", "status": "doing",
               "created": "2026-10-29 09:00"},
    }), encoding="utf-8")
    for f in ("tools/devtask.py", "tools/tianfeng_dispatch.py",
              "tools/dev_orchestrator.py"):
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    (tmp_path / "deliverables").mkdir()
    return tmp_path


def test_compute_roles_cards_mech(tmp_path):
    root = _mk(tmp_path)
    m = LR.compute(root)
    assert m["seats"] == 7
    assert m["verdict"]["role_ok"] is True
    assert m["verdict"]["chain_ok"] is True
    assert m["verdict"]["cards_ok"] is True
    assert m["verdict"]["mechanism_ok"] is True
    assert m["new_dev_done"] == 2
    assert m["seat_exec_share_new"] == 0.5


def test_render_md(tmp_path):
    m = LR.compute(_mk(tmp_path))
    md = LR.render_md(m)
    for k in ("角色到位", "能力卡", "任务分派机制", "判定"):
        assert k in md
