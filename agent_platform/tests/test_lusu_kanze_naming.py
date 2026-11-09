# -*- coding: utf-8 -*-
"""鲁肃 / 阚泽 定名防回归守卫（2026-10-27 判例）。

判例真源：docs/audit/2026-10-27_鲁肃与阚泽定名判例.md
钉住三条现行口径，防旧口径（「鲁肃在通信客服部」「阌泽」错字）回潮：
  1. 鲁肃 = D1 · 量化交易部（投研部）最终决策官；
  2. 阚泽 = LUSU1 · 通信与客户服务部（信驿司）主官，LUSU1 为历史 ID；
  3. 旧《鲁肃通信与客户服务部编制方案 v1》已加 STATUS superseded。
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _org() -> dict:
    return json.loads(_read("agent_platform/registry/org.json"))


def _dept(org: dict, dept: str) -> dict:
    return [d for d in org["departments"] if d.get("dept") == dept][0]


def test_hr_name_truth_distinguishes_lusu_and_kanze():
    d1 = _read("agent_platform/hr/D1/resume.yaml")
    lusu1 = _read("agent_platform/hr/LUSU1/resume.yaml")
    assert "cn_name: 鲁肃" in d1
    assert "投研" in d1 and "裁决" in d1
    assert "cn_name: 阚泽" in lusu1
    assert "鲁肃" not in lusu1, "阚泽档案不得再出现鲁肃名字"


def test_org_comm_dept_lead_is_kanze_not_lusu():
    org = _org()
    comm = _dept(org, "comm")
    assert comm["name"] == "通信与客户服务部"
    assert comm["lead"] == "LUSU1"
    assert "阚泽" in comm["charter"]
    assert "阌泽" not in _read("agent_platform/registry/org.json")
    profiles = {p["agent_id"]: p for p in comm["agent_profiles"]}
    assert profiles["LUSU1"]["cn_name"] == "阚泽"
    # 现行口径写进 naming_note；历史编制方案只能以 STATUS 文档被引用
    assert "鲁肃=D1" in comm["naming_note"]
    assert "2026-10-27_鲁肃与阚泽定名判例" in comm["naming_note"]


def test_org_invest_dept_lead_is_lusu_final_decider():
    org = _org()
    invest = _dept(org, "invest")
    assert invest["lead"] == "D1"
    profiles = {p["agent_id"]: p for p in invest["agent_profiles"]}
    assert "最终决策官" in profiles["D1"]["charter_detail"]


def test_no_kanze_wrong_char_in_serving_artifacts():
    # 错字「阌泽」不得出现在真源/生成物（历史 backups/ 豁免）
    for rel in (
        "agent_platform/registry/org.json",
        "agent_platform/registry/team_comm.json",
        "portal/agent_ide_bundle.json",
        "portal/persona_registry.json",
    ):
        assert "阌泽" not in _read(rel), rel


def test_bundle_carries_current_naming():
    bundle = json.loads(_read("portal/agent_ide_bundle.json"))
    agents = bundle["agents"]
    assert agents["D1"]["cn_name"] == "鲁肃"
    assert agents["D1"]["role_cn"] == "最终决策官"
    assert agents["LUSU1"]["cn_name"] == "阚泽"
    assert "通信" in agents["LUSU1"]["dept_name"]


def test_persona_registry_carries_judgment_not_old_line():
    reg = json.loads(_read("portal/persona_registry.json"))
    dep = [x for x in reg["departments"] if x.get("name") == "鲁肃"][0]
    assert dep.get("service_line") == "invest_decision"
    assert "阚泽" in dep["naming_note"], "命名注须说明通信部主官=阚泽"
    assert "鲁肃在通信" not in dep["naming_note"], "旧「鲁肃在通信部」口径须已清除"
    assert "2026-10-27_鲁肃与阚泽定名判例" in dep["naming_note"]
    assert "communication_customer_service" not in _read("portal/persona_registry.json")


def test_old_lusu_comm_plan_marked_superseded():
    # 2026-11-07 归档迁移：旧计划已移到 archive/feishu-20261107/plans/，
    # 内容仍保留 STATUS superseded / 阚泽 / 判例引用，守卫不降级。
    text = _read("archive/feishu-20261107/plans/2026-10-21_鲁肃通信与客户服务部_编制方案_v1.md")
    head = "\n".join(text.splitlines()[:6])
    assert "STATUS: superseded" in head
    assert "阚泽" in head
    assert "2026-10-27_鲁肃与阚泽定名判例" in head


def test_judgment_doc_is_single_source_of_naming():
    text = _read("docs/audit/2026-10-27_鲁肃与阚泽定名判例.md")
    assert "鲁肃 = D1" in text or "鲁肃=D1" in text
    assert "阚泽 = LUSU1" in text or "阚泽=LUSU1" in text
    assert "bfb46df6" in text
