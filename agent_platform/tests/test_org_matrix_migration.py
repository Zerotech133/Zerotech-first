"""org 矩阵迁移测试（WO-ORG-MATRIX P2）：双写校验、幂等、冲突拒绝。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "migrate_org_matrix.py"
_spec = importlib.util.spec_from_file_location("migrate_org_matrix", str(_TOOL))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _org():
    return {"departments": [
        {"dept": "dev", "name": "研发部", "agents": ["DEV1"],
         "agent_profiles": [{"agent_id": "DEV1", "role_profile": "code_engineer",
                             "llm_binding": "local/9b", "tools": ["events"]}]},
        {"dept": "proj_backtest", "name": "回测开发部", "type": "project",
         "agents": ["DEV1"],
         "agent_profiles": [{"agent_id": "DEV1", "role_profile": "backtest_dev",
                             "analysis_type": "backtest", "llm_binding": "local/35b",
                             "tools": ["run_backtest"], "grade_note": "项目侧"}]},
        {"dept": "quality", "name": "质量部", "agents": ["QA1"],
         "agent_profiles": [{"agent_id": "QA1", "role_profile": "qa_engineer"}]},
    ]}


def test_plan_counts_and_moves():
    p = M.plan(_org())
    assert p["before_profiles"] == 3 and p["after_profiles"] == 2
    assert len(p["moves"]) == 1 and p["moves"][0]["dept"] == "proj_backtest"
    assert p["moves"][0]["functional_dept"] == "dev"


def test_dual_read_equal_and_migrate_idempotent():
    org = _org()
    rep = M.check(org)
    assert rep["ok"] is True, (rep["missing"], rep["diff"])
    after = M.migrate(json.loads(json.dumps(org)))
    prof = after["departments"][0]["agent_profiles"][0]
    assert len(prof["matrix_assignments"]) == 1
    assert prof["matrix_assignments"][0]["role_profile"] == "backtest_dev"
    # 幂等：再迁移一次不重复追加
    again = M.migrate(json.loads(json.dumps(after)))
    prof2 = again["departments"][0]["agent_profiles"][0]
    assert len(prof2["matrix_assignments"]) == 1


def test_project_profiles_cleared_agents_kept():
    after = M.migrate(_org())
    proj = [d for d in after["departments"] if d["dept"] == "proj_backtest"][0]
    assert proj["agent_profiles"] in ([], {})
    assert proj["agents"] == ["DEV1"]


def test_conflict_rejected():
    org = _org()
    org["departments"][1]["agent_profiles"][0]["agent_id"] = "GHOST1"
    p = M.plan(org)
    assert p["conflicts"] and p["conflicts"][0]["agent"] == "GHOST1"
    rep = M.check(org)
    assert rep["ok"] is False and rep["error"] == "conflicts"
    try:
        M.migrate(org)
        assert False, "应拒绝迁移"
    except ValueError as e:
        assert "无法合并" in str(e)


def test_cli_check_and_apply(tmp_path):
    root = tmp_path
    (root / "agent_platform" / "registry").mkdir(parents=True)
    (root / M.ORG_REL).write_text(json.dumps(_org(), ensure_ascii=False), encoding="utf-8")
    assert M.main(["--root", str(root), "--check", "--json"]) == 0
    assert M.main(["--root", str(root), "--apply"]) == 0
    saved = json.loads((root / M.ORG_REL).read_text(encoding="utf-8"))
    proj = [d for d in saved["departments"] if d["dept"] == "proj_backtest"][0]
    assert proj["agent_profiles"] == []
    backups = list((root / "agent_platform" / "registry").glob("org.json.bak-matrix-*"))
    assert len(backups) == 1
