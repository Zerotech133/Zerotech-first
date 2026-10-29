# -*- coding: utf-8 -*-
"""鲁肃军团人员就位/模型/能力配置审计测试（2026-10-30 WO-INV-STAFF-AUDIT）。"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location("invest_staff_audit", TOOLS / "invest_staff_audit.py")
audit_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_mod)


def _checks(res):
    return {(c["seat"], c["check"]): c for c in res["checks"]}


def test_audit_all_green():
    res = audit_mod.audit()
    assert res["ok"] is True
    assert res["summary"]["fail"] == 0
    assert res["summary"]["total"] >= 90


def test_eight_seats_present():
    res = audit_mod.audit()
    c = _checks(res)
    for s in audit_mod.SEATS:
        assert c[(s, "org profile 存在")]["status"] == "PASS"
        assert c[(s, "HR 姓名一致")]["status"] == "PASS"
        assert c[(s, "prompt 文件")]["status"] == "PASS"
        assert c[(s, "模型绑定")]["status"] == "PASS"
        assert c[(s, "工具覆盖")]["status"] == "PASS"
        assert c[(s, "memory_grants")]["status"] == "PASS"


def test_d1_model_and_team_alignment():
    import json
    org = json.loads((REPO / "agent_platform/registry/org.json").read_text(encoding="utf-8"))
    inv = [d for d in org["departments"] if d["dept"] == "invest"][0]
    d1 = [p for p in inv["agent_profiles"] if p["agent_id"] == "D1"][0]
    team = json.loads((REPO / "agent_platform/registry/team_invest.json").read_text(encoding="utf-8"))
    td1 = [a for a in team["agents"] if a["agent_id"] == "D1"][0]
    assert d1["llm_binding"] == "glm/glm-5.3"
    assert td1["llm_binding"] == "glm/glm-5.3"
    assert td1.get("llm_fallback")


def test_qs1_runtime_mode_and_probe():
    res = audit_mod.audit()
    c = _checks(res)
    assert c[("QS1", "QS1 runtime 模式")]["status"] == "PASS"
    assert c[("—", "QS1 专用探针")]["status"] == "PASS"
