# -*- coding: utf-8 -*-
"""WO-LEGION-S43 档案迁移测试。

承重点：原位置打标记不删（chief 仍被 monthly_report/snapshot 读）、
原本就在名单的席位不误报、失败时 org 与 team 文件**都要**回滚、
缺职责/缺绑定一律拒（工具不造职责、不替席位假定能力）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from engine import org_migrate as M  # noqa: E402


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "registry").mkdir(parents=True)
    org = {
        "chief": {"agent_id": "C0", "role_profile": "chief_of_staff",
                  "llm_binding": "glm/glm-5.3", "name": "参谋长某",
                  "attributes": {"experience": "二十年参谋经验"}},
        "departments": [
            {"dept": "line", "name": "直属线", "lead": "C0", "agents": ["OPT1"],
             "agent_profiles": [{"agent_id": "OPT1", "llm_binding": "none",
                                 "charter_detail": "参谋席职责描述足够长"}]},
            {"dept": "comm", "name": "通信部", "lead": "L1",
             "agents": ["L1", "T1"],        # T1 已在名单，但无档案
             "agent_profiles": [{"agent_id": "L1", "llm_binding": "ollama/qwen3.5:9b",
                                 "charter_detail": "主官职责描述足够长"}]},
        ]}
    (tmp_path / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp_path / "registry" / "team_comm.json").write_text(
        json.dumps({"agents": [{"agent_id": "T1", "name": "某甲",
                                "role_profile": "relay",
                                "duty": "通讯中继职责描述足够长的一句话"}]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def test_migrate_from_chief_adds_profile_and_marks_source(root):
    plan = M.build_migrate_plan(root, agent_id="C0", dept="line", source="chief",
                                charter="参谋长：组织调度与任务仲裁、汇报聚合与简报出件",
                                ticket="WO-LEGION-S43", signed_by="老田")
    assert plan["add_to_agents"] is True
    res = M.apply_migrate_plan(root, plan, signed_by="老田")
    assert res["ok"] is True
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    line = next(d for d in org["departments"] if d["dept"] == "line")
    assert "C0" in line["agents"]
    prof = next(p for p in line["agent_profiles"] if p["agent_id"] == "C0")
    assert prof["llm_binding"] == "glm/glm-5.3", "绑定应沿用原档案"
    assert prof["charter_detail"].startswith("参谋长")
    # chief 块保留可读（monthly_report/snapshot 仍读它），只多一个指向标记
    assert org["chief"]["agent_id"] == "C0"
    assert org["chief"]["llm_binding"] == "glm/glm-5.3"
    assert "departments.line" in org["chief"]["_superseded_by"]


def test_migrate_keeps_agents_list_when_already_listed(root):
    """原本就在名单里的席位（真实中的邓芝）迁移后名单不得被削掉。

    旧自检无条件把该 id 从 after 名单里过滤掉再比对，这类席位每次必误报。
    """
    plan = M.build_migrate_plan(root, agent_id="T1", dept="comm",
                                source="team:registry/team_comm.json",
                                binding="none", ticket="WO-LEGION-S43", signed_by="老田")
    assert plan["add_to_agents"] is False
    assert M.apply_migrate_plan(root, plan, signed_by="老田")["ok"] is True
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    comm = next(d for d in org["departments"] if d["dept"] == "comm")
    assert comm["agents"] == ["L1", "T1"]
    assert any(p["agent_id"] == "T1" for p in comm["agent_profiles"])


def test_migrate_refuses_when_target_has_profile(root):
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    with pytest.raises(M.MigrateError) as ei:
        M.build_migrate_plan(root, agent_id="L1", dept="comm", source="chief",
                             charter="随便一段够长的职责描述文字",
                             ticket="WO-LEGION-S43", signed_by="老田")
    assert "已有档案" in str(ei.value)


def test_migrate_refuses_without_duty_or_binding(root):
    """不造职责、不替席位假定能力：缺 duty 且无 --charter 拒，缺 binding 且无 --binding 拒。"""
    with pytest.raises(M.MigrateError) as ei:
        M.build_migrate_plan(root, agent_id="C0", dept="line", source="chief",
                             charter="", ticket="WO-LEGION-S43", signed_by="老田")
    assert "职责" in str(ei.value)
    with pytest.raises(M.MigrateError) as ei2:
        M.build_migrate_plan(root, agent_id="T1", dept="comm",
                             source="team:registry/team_comm.json",
                             ticket="WO-LEGION-S43", signed_by="老田")
    assert "llm_binding" in str(ei2.value)


def test_migrate_rolls_back_both_org_and_team(root, monkeypatch):
    """自检失败必须把 org.json **和** team 文件都还原。

    旧实现只备份 org.json：team 已被打上 _superseded_by，回滚后留下
    「标记指向一次没发生的迁移」的脏状态（S43 实测踩到）。
    """
    org_before = (root / "registry" / "org.json").read_bytes()
    team_before = (root / "registry" / "team_comm.json").read_bytes()
    plan = M.build_migrate_plan(root, agent_id="T1", dept="comm",
                                source="team:registry/team_comm.json",
                                binding="none", ticket="WO-LEGION-S43", signed_by="老田")

    def boom(*a, **k):
        raise M.MigrateError("自检失败：模拟")

    monkeypatch.setattr(M, "_selfcheck", boom)
    with pytest.raises(M.MigrateError):
        M.apply_migrate_plan(root, plan, signed_by="老田")
    monkeypatch.undo()
    assert (root / "registry" / "org.json").read_bytes() == org_before
    assert (root / "registry" / "team_comm.json").read_bytes() == team_before


def test_migrate_requires_creds(root):
    with pytest.raises(M.MigrateError):
        M.build_migrate_plan(root, agent_id="C0", dept="line", source="chief",
                             charter="参谋长：组织调度与任务仲裁、汇报聚合与简报出件",
                             ticket="", signed_by="老田")
    with pytest.raises(M.MigrateError):
        M.build_migrate_plan(root, agent_id="C0", dept="line", source="chief",
                             charter="参谋长：组织调度与任务仲裁、汇报聚合与简报出件",
                             ticket="WO-LEGION-S43", signed_by="")
