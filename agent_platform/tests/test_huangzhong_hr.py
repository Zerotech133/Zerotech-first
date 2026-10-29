# -*- coding: utf-8 -*-
"""HUANGZHONG HR 档案一致性测试（WO-HZ-HR-20261030）。

背景：研究院角色属性复盘残留——org.json / bundle 已有 HUANGZHONG（指挥部老将·验收守门官，
研究院 E3 liaison），但 agent_platform/hr/HUANGZHONG/resume.yaml 独缺；seat_realness
「授权」维度因此判 org=True hr=False。

承重点：
1) resume 必须存在且可被 PyYAML 解析（HR 是名字真源，坏 YAML 会让读取工具静默跳过）；
2) 关键字段必须与 org agent_profiles.HUANGZHONG / bundle 卡一致（口径漂移=取档方继续走默认档）；
3) HR 名册内 cn_name 唯一（一名一席；复用 tools/check_name_uniqueness.py 的读取口径）；
4) liaison 口径不回归：team_academy.json 仍不含 HUANGZHONG（不占正式编制）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

_PLATFORM = Path(__file__).resolve().parents[1]  # agent_platform/
_ROOT = _PLATFORM.parent  # 仓库/worktree 根
_HR = _PLATFORM / "hr" / "HUANGZHONG" / "resume.yaml"
_ORG = _PLATFORM / "registry" / "org.json"
_TEAM_ACADEMY = _PLATFORM / "registry" / "team_academy.json"
_BUNDLE = _ROOT / "portal" / "agent_ide_bundle.json"

# 与 GUANYU resume 同构的字段集（缺字段=档案不完整，读取工具会退化）
_GUANYU_KEYS = (
    "agent_id",
    "name",
    "kind",
    "identity",
    "experience",
    "skills",
    "self_statement",
    "cn_name",
    "cn_origin",
    "role_profile",
    "charter_detail",
    "grade",
    "grade_note",
)


def _cn_uniq():
    """加载仓库既有名称唯一性检查器（只读），复用其 HR 读取口径。"""
    spec = importlib.util.spec_from_file_location(
        "check_name_uniqueness", str(_ROOT / "tools" / "check_name_uniqueness.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resume():
    assert _HR.is_file(), "缺 HR 档案：%s" % _HR
    data = yaml.safe_load(_HR.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "resume.yaml 解析结果不是映射"
    return data


def _org_profile():
    org = json.loads(_ORG.read_text(encoding="utf-8"))
    dep = next(d for d in org["departments"] if d.get("dept") == "academy")
    profs = {p["agent_id"]: p for p in dep["agent_profiles"]}
    assert "HUANGZHONG" in profs, "org.json 研究院档案表缺 HUANGZHONG"
    return profs["HUANGZHONG"]


def _bundle_card():
    bundle = json.loads(_BUNDLE.read_text(encoding="utf-8"))
    card = (bundle.get("agents") or {}).get("HUANGZHONG")
    assert card, "portal/agent_ide_bundle.json 缺 HUANGZHONG 卡"
    return card


def test_resume_exists_and_yaml_parses():
    data = _resume()
    for key in _GUANYU_KEYS:
        assert key in data, "resume 缺字段 %s（应与 GUANYU 同构）" % key
    assert data["agent_id"] == "HUANGZHONG"
    assert data["kind"] == "staff"
    ident = data["identity"]
    assert ident.get("origin"), "identity.origin 不得为空"
    assert ident.get("model"), "identity.model 不得为空"
    assert str(ident.get("model_note") or "").strip(), "model_note 须注明绑定来源"
    assert ident.get("born"), "identity.born 不得为空"
    assert isinstance(data["skills"], list) and data["skills"], "skills 不得为空"
    assert isinstance(data["experience"], list) and data["experience"]
    assert data["experience"][0].get("org") and data["experience"][0].get("kpi")


def test_fields_match_org_profile():
    data, prof = _resume(), _org_profile()
    assert data["agent_id"] == prof["agent_id"] == "HUANGZHONG"
    assert data["cn_name"] == prof["cn_name"] == "黄忠"
    assert data["role_profile"] == prof["role_profile"] == "delivery_acceptance_chief"
    assert data["grade"] == prof["grade"] == "E3"
    assert prof.get("analysis_type") == "delivery_acceptance"
    assert prof.get("reports_to") == "A0"
    assert prof.get("academy_status") == "liaison"
    # model 取 org llm_binding（不得凭空换模型）；当前事实源锁定 glm/glm-5.3
    assert data["identity"]["model"] == prof["llm_binding"] == "glm/glm-5.3"
    note = data["identity"]["model_note"]
    assert "org.json" in note and "llm_binding" in note, "model_note 须注明绑定来源"
    # 一句话口径：只验不改、只退不批
    st = data["self_statement"]
    assert "只验不改" in st and "只退不批" in st, "self_statement 须用「只验不改、只退不批」口径"


def test_bundle_card_consistent():
    """HR 与 bundle 卡不得漂移（bundle 由 HR 名真源构建，漂移=指挥台显示与实际不符）。"""
    data, card = _resume(), _bundle_card()
    assert card.get("cn_name") == data["cn_name"]
    assert card.get("role_profile") == data["role_profile"]
    assert card.get("grade") == data["grade"]
    assert card.get("llm") == data["identity"]["model"]


def test_cn_name_unique():
    CN = _cn_uniq()
    hr_names = CN._load_hr_names(_ROOT)
    assert hr_names.get("HUANGZHONG") == "黄忠"
    dups = [
        aid for aid, cn in hr_names.items() if cn == "黄忠" and aid != "HUANGZHONG"
    ]
    assert not dups, "HR 名册重名「黄忠」：%r" % dups
    rep = CN.check(_ROOT)
    assert rep["ok"] is True, "名称唯一性检查不过：%r" % (rep["violations"],)
    assert not any(v["code"] == "DUP_HR_NAME" for v in rep["violations"])


def test_liaison_not_in_team_academy():
    """HUANGZHONG 是研究院 liaison 双身份，不得混进 team_academy 正式编制（不回归）。"""
    team = json.loads(_TEAM_ACADEMY.read_text(encoding="utf-8"))
    ids = [a.get("agent_id") for a in (team.get("agents") or [])]
    assert "HUANGZHONG" not in ids, "HUANGZHONG 不占 team_academy 正式编制"
    assert "GUANYU" not in ids, "GUANYU 同为 liaison，不占 team_academy 正式编制"
    # org 侧口径必须仍是 liaison（与 team 文件互为印证）
    org = json.loads(_ORG.read_text(encoding="utf-8"))
    dep = next(d for d in org["departments"] if d.get("dept") == "academy")
    liaison = {x.get("agent_id") for x in (dep.get("liaison_agents") or [])}
    assert "HUANGZHONG" in liaison, "org.json 研究院 liaison_agents 缺 HUANGZHONG"
    assert "HUANGZHONG" not in set(dep.get("formal_agents") or [])
