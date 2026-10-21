# -*- coding: utf-8 -*-
"""WO-LEGION-S43 中文名迁移测试。

承重点：三处同步（hr / org.persona / team）、重名冲突阻断、去名合法、
写后自检「别的席位一个名字都没动」、失败回滚。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from engine import org_rename as R  # noqa: E402


@pytest.fixture()
def root(tmp_path):
    """最小环境：两个部门、两个席位（A1 名字在 hr+org+team 三处，B1 只在 hr）。"""
    (tmp_path / "registry").mkdir(parents=True)
    (tmp_path / "hr" / "A1").mkdir(parents=True)
    (tmp_path / "hr" / "B1").mkdir(parents=True)
    (tmp_path / "hr" / "A1" / "resume.yaml").write_text(
        "agent_id: A1\nname: 某岗位\ncn_name: 甲某\n", encoding="utf-8")
    (tmp_path / "hr" / "B1" / "resume.yaml").write_text(
        "agent_id: B1\ncn_name: 乙某\n", encoding="utf-8")
    org = {"departments": [{
        "dept": "d1", "name": "测试营", "lead": "A1",
        "agents": ["A1", "B1"],
        "agent_profiles": [
            {"agent_id": "A1", "persona": {"name": "甲某"}, "llm_binding": "none",
             "charter_detail": "测试职责不少于十个字"},
            {"agent_id": "B1", "llm_binding": "none", "charter_detail": "另一段职责描述文字"},
        ]}]}
    (tmp_path / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    team = {"agents": [{"agent_id": "A1", "name": "甲某", "llm_binding": "none"}]}
    (tmp_path / "registry" / "team_d1.json").write_text(
        json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def _roster(root):
    return R._roster(root)


def test_rename_syncs_three_places(root):
    plan = R.build_rename_plan(root, agent_id="A1", new_cn="丙某",
                               ticket="WO-LEGION-S43", signed_by="老田")
    assert {t["where"] for t in plan["targets"]} == {
        "hr.resume.cn_name", "dept:d1.persona.name", "agents[].name"}
    res = R.apply_rename_plan(root, plan, signed_by="老田")
    assert res["ok"] is True and len(res["backups"]) == 3
    assert _roster(root)["丙某"] == ["A1"]
    assert "甲某" not in _roster(root)
    assert "乙某" in _roster(root), "非目标席位名字被动了"
    # org.json 其他字段未变
    prof = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    a1 = prof["departments"][0]["agent_profiles"][0]
    assert a1["llm_binding"] == "none" and a1["charter_detail"] == "测试职责不少于十个字"


def test_rename_rejects_name_taken_by_other(root):
    with pytest.raises(R.RenameError) as ei:
        R.build_rename_plan(root, agent_id="A1", new_cn="乙某",
                            ticket="WO-LEGION-S43", signed_by="老田")
    assert "已被" in str(ei.value)


def test_rename_rejects_same_name_and_missing_creds(root):
    with pytest.raises(R.RenameError):
        R.build_rename_plan(root, agent_id="A1", new_cn="甲某",
                            ticket="WO-LEGION-S43", signed_by="老田")
    with pytest.raises(R.RenameError):
        R.build_rename_plan(root, agent_id="A1", new_cn="丙某", ticket="", signed_by="老田")
    with pytest.raises(R.RenameError):
        R.build_rename_plan(root, agent_id="A1", new_cn="丙某",
                            ticket="WO-LEGION-S43", signed_by="")
    with pytest.raises(R.RenameError):
        R.build_rename_plan(root, agent_id="NOPE", new_cn="丙某",
                            ticket="WO-LEGION-S43", signed_by="老田")


def test_clear_name_is_legal(root):
    """纯工程组件不占人名：去名后名册里不再有它，且不是"改成空字符串的人名"。"""
    plan = R.build_rename_plan(root, agent_id="A1", new_cn="",
                               ticket="WO-LEGION-S43", signed_by="老田")
    assert plan["action"] == "clear"
    R.apply_rename_plan(root, plan, signed_by="老田")
    roster = _roster(root)
    assert not any("A1" in ids for ids in roster.values()), "去名后不应再出现在名册"
    assert roster["乙某"] == ["B1"]


def test_rename_no_write_in_dry_run(root):
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    R.build_rename_plan(root, agent_id="A1", new_cn="丙某",
                        ticket="WO-LEGION-S43", signed_by="老田")
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_rename_rolls_back_when_selfcheck_fails(root, monkeypatch):
    plan = R.build_rename_plan(root, agent_id="A1", new_cn="丙某",
                               ticket="WO-LEGION-S43", signed_by="老田")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and p.is_file()}
    src_before = {k: v for k, v in before.items() if "backups" not in str(k)}

    def boom(*a, **k):
        raise R.RenameError("自检失败：模拟")

    monkeypatch.setattr(R, "_selfcheck", boom)
    with pytest.raises(R.RenameError):
        R.apply_rename_plan(root, plan, signed_by="老田")
    monkeypatch.undo()
    src_after = {p: p.read_bytes() for p in root.rglob("*")
                 if p.is_file() and "backups" not in str(p)}
    assert src_after == src_before, "自检失败必须整体回滚（hr/org/team 三处都要还原）"


def test_roster_detects_duplicates_by_construction(tmp_path):
    """重名检出必须用构造样本证明，不能断言「真实盘面正好 4 对」——
    消歧一完成该断言就红，而它红的原因是"问题被修好了"（2026-10-21 反复踩到）。"""
    (tmp_path / "hr" / "X1").mkdir(parents=True)
    (tmp_path / "hr" / "X2").mkdir(parents=True)
    (tmp_path / "hr" / "X1" / "resume.yaml").write_text("cn_name: 同名\n", encoding="utf-8")
    (tmp_path / "hr" / "X2" / "resume.yaml").write_text("cn_name: 同名\n", encoding="utf-8")
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "org.json").write_text(
        json.dumps({"departments": []}, ensure_ascii=False), encoding="utf-8")
    dups = {k: v for k, v in R._roster(tmp_path).items() if len(v) > 1}
    assert dups == {"同名": ["X1", "X2"]}


def test_name_seat_that_has_no_name_anywhere(root):
    """授名：三处都没有人名时，必须能在 org 档案里新增 cn_name（而不是报「无从改名」）。

    真实场景：LUSU1 只有 team_comm.json 里一个疑似笔误的名字，org/hr 全空，
    指挥台因此显示成 id。工具要支持从「无」到「有」。
    """
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    org["departments"][0]["agents"].append("X9")
    org["departments"][0]["agent_profiles"].append(
        {"agent_id": "X9", "llm_binding": "none", "charter_detail": "无名席位职责描述文字"})
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")

    plan = R.build_rename_plan(root, agent_id="X9", new_cn="某名",
                               ticket="WO-LEGION-S45", signed_by="老田")
    assert plan["action"] == "name" and plan["from_cn"] == ""
    assert any("新增" in t["where"] for t in plan["targets"])
    R.apply_rename_plan(root, plan, signed_by="老田")
    assert _roster(root).get("某名") == ["X9"]


def test_also_team_required_to_touch_team_name_when_naming(root):
    """授名时默认不碰 team.name（那多半是岗位名）；确是人名要用 --also-team 显式要求。"""
    org = json.loads((root / "registry" / "org.json").read_text(encoding="utf-8"))
    org["departments"][0]["agents"].append("X9")
    org["departments"][0]["agent_profiles"].append(
        {"agent_id": "X9", "llm_binding": "none", "charter_detail": "无名席位职责描述文字"})
    (root / "registry" / "org.json").write_text(
        json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    team = json.loads((root / "registry" / "team_d1.json").read_text(encoding="utf-8"))
    team["agents"].append({"agent_id": "X9", "name": "疑似旧名", "llm_binding": "none"})
    (root / "registry" / "team_d1.json").write_text(
        json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")

    p1 = R.build_rename_plan(root, agent_id="X9", new_cn="某名",
                             ticket="WO-LEGION-S45", signed_by="老田")
    assert not any("team_d1" in t["file"] for t in p1["targets"]), "默认不该动 team.name"
    p2 = R.build_rename_plan(root, agent_id="X9", new_cn="某名", also_team=True,
                             ticket="WO-LEGION-S45", signed_by="老田")
    assert any("team_d1" in t["file"] for t in p2["targets"])
