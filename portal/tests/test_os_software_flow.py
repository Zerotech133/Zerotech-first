# -*- coding: utf-8 -*-
"""G1 软件面流程单测（同一账本：需求→卡→授权→工单/认领→隔离执行→测试→试用→验收/返工）。"""

import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_goal_contract as C  # noqa: E402
import os_goal_store as S  # noqa: E402
import os_software_flow as F  # noqa: E402

ACTIONS = [
    "prototype.generate",
    "prototype.preview",
    "prototype.test",
    "workspace.cleanup",
]


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


@pytest.fixture()
def iid(store):
    goal = store.create_goal(
        "把零界做成持续创业者的个人事业OS",
        "连续创业者",
        [{"id": "A1", "text": "用户愿付费", "status": "hypothesis"}],
        ["四类成果各验收一次"],
    )
    return store.create_item(
        goal["payload"]["goal_id"], "做一页可试用原型", "老田今天要试用", "software"
    )["payload"]["item_id"]


def freeze_all(store, iid, claim_method="simulated"):
    F.freeze_brief(
        store,
        iid,
        ["把一句需求做成可试用单页"],
        [
            {
                "id": "SC1",
                "statement": "页面可打开且含版本标记",
                "verify_method": "静态自检 data-artifact",
            }
        ],
    )
    F.freeze_card(
        store,
        iid,
        {"option": "A｜隔离单页", "why": "可逆、可试用"},
        [{"option": "B｜改现网首页", "why": "不可逆"}],
        "本人打开试用并给一句反馈",
    )
    F.select_card(store, iid, "c1")
    F.sign_grant(store, iid, "c1", ACTIONS)
    F.create_workorder(store, iid)
    F.claim(
        store,
        iid,
        method=claim_method,
        isolation_label="隔离验证/未获真实认领"
        if claim_method == "simulated"
        else None,
    )


def test_happy_chain_isolated_only(store, iid):
    freeze_all(store, iid)
    result = F.execute(store, iid)
    assert result["ok"] is True
    assert os.path.isfile(result["artifact"]["path"])
    F.open_trial(store, iid)
    F.record_verdict(store, iid, "accepted", "能打开也可以用，接受")
    rep = F.chain_report(store, iid)
    assert rep["verdict"] == "ISOLATED_ONLY", rep
    assert rep["gates"]["G6"]["status"] == "PASS"
    assert rep["gates"]["G7"]["status"] == "PASS"
    assert rep["gates"]["G5"]["status"] == "ISOLATED_ONLY"
    real_base = os.path.realpath(store.base_dir)
    assert os.path.realpath(result["artifact"]["path"]).startswith(real_base)


def test_rework_produces_v2(store, iid):
    freeze_all(store, iid)
    F.execute(store, iid)
    F.open_trial(store, iid)
    F.record_verdict(store, iid, "changes_requested", "标题文案要改")
    rep1 = F.chain_report(store, iid)
    assert rep1["gates"]["G7"]["status"] == "BLOCKED"
    out = F.rework(store, iid, "标题文案要改")
    assert out["brief_version"] == "v2" and out["card_version"] == "c2"
    F.select_card(store, iid, "c2")
    F.sign_grant(store, iid, "c2", ACTIONS)
    F.create_workorder(store, iid)
    F.claim(store, iid, method="simulated", isolation_label="隔离验证/未获真实认领")
    F.execute(store, iid)
    F.open_trial(store, iid)
    F.record_verdict(store, iid, "accepted", "改好了，接受")
    rep2 = F.chain_report(store, iid)
    assert rep2["verdict"] == "ISOLATED_ONLY"
    arts = [e for e in store.events_for(iid) if e["type"] == "artifact.produced"]
    assert [a["payload"]["version"] for a in arts] == ["a1", "a2"]


def test_brief_twice_rejected(store, iid):
    F.freeze_brief(
        store, iid, ["g"], [{"id": "SC1", "statement": "s", "verify_method": "v"}]
    )
    with pytest.raises(C.ContractError):
        F.freeze_brief(
            store, iid, ["g2"], [{"id": "SC2", "statement": "s", "verify_method": "v"}]
        )


def test_card_before_brief_rejected(store, iid):
    with pytest.raises(C.ContractError):
        F.freeze_card(
            store,
            iid,
            {"option": "A", "why": "w"},
            [{"option": "B", "why": "w"}],
            "验收",
        )


def test_select_wrong_version_rejected(store, iid):
    F.freeze_brief(
        store, iid, ["g"], [{"id": "SC1", "statement": "s", "verify_method": "v"}]
    )
    F.freeze_card(
        store, iid, {"option": "A", "why": "w"}, [{"option": "B", "why": "w"}], "验收"
    )
    with pytest.raises(C.ContractError):
        F.select_card(store, iid, "c9")


def test_grant_beyond_whitelist_rejected(store, iid):
    F.freeze_brief(
        store, iid, ["g"], [{"id": "SC1", "statement": "s", "verify_method": "v"}]
    )
    F.freeze_card(
        store, iid, {"option": "A", "why": "w"}, [{"option": "B", "why": "w"}], "验收"
    )
    F.select_card(store, iid, "c1")
    with pytest.raises(C.ContractError):
        F.sign_grant(store, iid, "c1", ACTIONS + ["data.write.prod"])


def test_execute_before_claim_rejected(store, iid):
    F.freeze_brief(
        store, iid, ["g"], [{"id": "SC1", "statement": "s", "verify_method": "v"}]
    )
    F.freeze_card(
        store, iid, {"option": "A", "why": "w"}, [{"option": "B", "why": "w"}], "验收"
    )
    F.select_card(store, iid, "c1")
    F.sign_grant(store, iid, "c1", ACTIONS)
    F.create_workorder(store, iid)
    with pytest.raises(C.ContractError):
        F.execute(store, iid)


def test_expired_grant_rejected(store, iid):
    F.freeze_brief(
        store, iid, ["g"], [{"id": "SC1", "statement": "s", "verify_method": "v"}]
    )
    F.freeze_card(
        store, iid, {"option": "A", "why": "w"}, [{"option": "B", "why": "w"}], "验收"
    )
    F.select_card(store, iid, "c1")
    F.sign_grant(store, iid, "c1", ACTIONS, valid_until="2026-10-01T00:00:00+08:00")
    with pytest.raises(C.ContractError):
        F.create_workorder(store, iid)


def test_verdict_before_trial_rejected(store, iid):
    freeze_all(store, iid)
    F.execute(store, iid)
    with pytest.raises(C.ContractError):
        F.record_verdict(store, iid, "accepted", "")


def test_chain_blocked_when_only_item(store, iid):
    rep = F.chain_report(store, iid)
    assert rep["verdict"] == "BLOCKED"
    assert rep["gates"]["G1"]["status"] == "BLOCKED"


def test_api_flow_and_chain(store, iid):
    st, d = A.handle_post(
        store,
        "/api/os/item/%s/brief" % iid,
        {
            "goals": ["g"],
            "success_criteria": [
                {"id": "SC1", "statement": "页面可打开", "verify_method": "静态自检"}
            ],
        },
    )
    assert st == 201, d
    st, d = A.handle_post(
        store,
        "/api/os/item/%s/card" % iid,
        {
            "recommended": {"option": "A", "why": "w"},
            "discarded": [{"option": "B", "why": "w"}],
            "acceptance_method": "本人试用并反馈",
        },
    )
    assert st == 201, d
    st, d = A.handle_post(
        store, "/api/os/item/%s/card/select" % iid, {"card_version": "c1"}
    )
    assert st == 200, d
    st, d = A.handle_post(
        store, "/api/os/item/%s/grant" % iid, {"card_version": "c1", "actions": ACTIONS}
    )
    assert st == 200, d
    st, d = A.handle_post(store, "/api/os/item/%s/workorder" % iid, {})
    assert st == 201, d
    st, d = A.handle_post(
        store,
        "/api/os/item/%s/claim" % iid,
        {"method": "simulated", "isolation_label": "隔离验证/未获真实认领"},
    )
    assert st == 200, d
    st, d = A.handle_post(store, "/api/os/item/%s/execute" % iid, {})
    assert st == 201 and d["ok"] is True, d
    st, rep = A.handle_get(store, "/api/os/item/%s/chain" % iid)
    assert st == 200
    assert rep["gates"]["G6"]["status"] == "PASS"
    assert rep["verdict"] == "BLOCKED"  # 还没试用/验收


def test_real_claim_rejected_by_flow(store, iid):
    F.freeze_brief(
        store, iid, ["g"], [{"id": "SC1", "statement": "s", "verify_method": "v"}]
    )
    F.freeze_card(
        store, iid, {"option": "A", "why": "w"}, [{"option": "B", "why": "w"}], "验收"
    )
    F.select_card(store, iid, "c1")
    F.sign_grant(store, iid, "c1", ACTIONS)
    F.create_workorder(store, iid)
    with pytest.raises(C.ContractError):
        F.claim(store, iid, method="real", consumer_id="EVIL-CONSUMER")


def test_forged_agent_verdict_fails_chain(store, iid):
    freeze_all(store, iid)
    F.execute(store, iid)
    F.open_trial(store, iid)
    store.append_event(
        "verdict.recorded",
        {"item_id": iid},
        {
            "verdict_id": "VER-EVIL",
            "artifact_id": "x",
            "decision": "accepted",
            "feedback": "",
        },
        actor={"kind": "agent", "id": "zhuge"},
    )
    rep = F.chain_report(store, iid)
    assert rep["verdict"] == "FAIL"
    assert any(c["id"] == "G7_ACTOR" for c in rep["checks"])


def test_verdict_cannot_flip_after_accept(store, iid):
    freeze_all(store, iid)
    F.execute(store, iid)
    F.open_trial(store, iid)
    F.record_verdict(store, iid, "accepted", "接受")
    with pytest.raises(C.ContractError):
        F.record_verdict(store, iid, "changes_requested", "反悔想改")
