# -*- coding: utf-8 -*-
"""G2a：一句话 → 商业计划（BP v1/v2）同交互测试。"""
import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_goal_store as S  # noqa: E402
import os_quickstart as Q  # noqa: E402
import os_software_flow as F  # noqa: E402

ACTIONS = Q.DEFAULT_ACTIONS


@pytest.fixture()
def store(tmp_path):
    Q._DRAFTS.clear()
    Q._SEQ[0] = 0
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def _bp_card(text="帮我写一份商业计划：一个人如何用 AI 组队做四类成果"):
    return Q.create_draft(text)


def test_kind_detection_bp_vs_software(store):
    assert _bp_card()["kind"] == "business_plan"
    assert Q.create_draft("做个页面看简报")["kind"] == "software"
    card = _bp_card()
    assert "商业计划" in card["recommended"]["option"]
    assert any("假设" in sc["statement"] for sc in card["success_criteria"])


def test_bp_confirm_generates_document(store):
    card = _bp_card()
    out = Q.confirm(card["draft_id"], store)
    assert out["chain"]["verdict"] == "BLOCKED"  # 等待本人第 3 句
    path = out["artifact"]["path"]
    html = open(path, encoding="utf-8").read()
    for kw in ("假设台账", "依据与引用", "风险", "里程碑", "修订理由",
               "data-artifact=", "假设", "hypothesis"):
        assert kw in html, kw
    assert "暂无返工反馈" in html
    # 本人接受 → 隔离链闭环
    st, _ = A.handle_post(store, "/api/os/item/%s/verdict" % out["item_id"],
                          {"decision": "accepted", "feedback": "接受"})
    assert st == 201
    assert F.chain_report(store, out["item_id"])["verdict"] == "ISOLATED_ONLY"


def test_bp_rework_v2_has_revision_reason(store):
    card = _bp_card()
    out = Q.confirm(card["draft_id"], store)
    iid = out["item_id"]
    A.handle_post(store, "/api/os/item/%s/verdict" % iid,
                  {"decision": "changes_requested", "feedback": "用户改成独立开发者"})
    F.rework(store, iid, "用户改成独立开发者")
    F.select_card(store, iid, "c2")
    F.sign_grant(store, iid, "c2", ACTIONS)
    F.create_workorder(store, iid)
    F.claim(store, iid, method="simulated", isolation_label="隔离验证/未获真实认领")
    res = F.execute(store, iid)
    assert res["artifact"]["version"] == "a2"
    html = open(res["artifact"]["path"], encoding="utf-8").read()
    assert "用户改成独立开发者" in html
    assert "暂无返工反馈" not in html
    A.handle_post(store, "/api/os/item/%s/verdict" % iid,
                  {"decision": "accepted", "feedback": "接受"})
    rep = F.chain_report(store, iid)
    assert rep["verdict"] == "ISOLATED_ONLY", rep
    assert rep["gates"]["G6"]["status"] == "PASS"
