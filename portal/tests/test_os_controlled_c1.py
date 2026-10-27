# -*- coding: utf-8 -*-
"""C1：L2 人工审批通道测试（不外发，仅审批+受控 outbox）。"""

import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_controlled as X  # noqa: E402
import os_goal_contract as C  # noqa: E402
import os_goal_store as S  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def setup(store):
    g = store.create_goal(
        "C1 审批目标",
        "老田",
        [{"id": "A1", "text": "审批可控", "status": "hypothesis"}],
        ["L2 逐次审批"],
    )
    iid = store.create_item(
        g["payload"]["goal_id"], "C1 发布事项", "验证人工审批", "software"
    )["payload"]["item_id"]
    X.register_consumer(
        store,
        "CONS-C1-01",
        kind="agent",
        capabilities=["artifact.generate", "test.run"],
    )
    return iid, "CONS-C1-01"


def test_request_only_for_l2(store):
    iid, cid = setup(store)
    with pytest.raises(C.ContractError):
        X.request_l2_approval(store, iid, "artifact.generate", cid)
    with pytest.raises(C.ContractError):
        X.request_l2_approval(store, iid, "publish", "CONS-NOT-REG")


def test_approve_requires_nonce(store):
    iid, cid = setup(store)
    apr = X.request_l2_approval(store, iid, "publish", cid, summary="发布一篇文章")
    assert apr["status"] == "pending"
    with pytest.raises(C.ContractError):
        X.decide_l2_approval(store, iid, apr["approval_id"], "approved", "")
    d = X.decide_l2_approval(
        store, iid, apr["approval_id"], "approved", "nonce-c1-12345"
    )
    assert d["decision"] == "approved" and d["signer"]["kind"] == "user"


def test_risk_denies_before_approval(store):
    iid, cid = setup(store)
    apr = X.request_l2_approval(store, iid, "publish", cid)
    r = X.risk_verdict_with_approval(store, iid, "publish", cid, apr["approval_id"])
    assert r["decision"] == "deny"


def test_outbox_and_chain_after_approval(store):
    iid, cid = setup(store)
    apr = X.request_l2_approval(store, iid, "publish", cid, summary="发布草稿 01")
    X.decide_l2_approval(
        store,
        iid,
        apr["approval_id"],
        "approved",
        "nonce-c1-12345",
        note="同意发布草稿 01",
    )
    out = X.record_l2_outbox(
        store,
        iid,
        cid,
        apr["approval_id"],
        "publish",
        summary="发布草稿 01",
        payload_text="标题/正文摘要",
    )
    assert out["external_sent"] is False
    assert os.path.isfile(out["path"])
    rep = X.chain_report_c1(store, iid)
    assert rep["verdict"] == "CONTROLLED_L2_APPROVED", rep
    for gate in ("C1-REQ", "C1-DECISION", "C1-RISK", "C1-OUTBOX", "C1-AUDIT"):
        assert rep["gates"][gate]["status"] == "PASS", rep["gates"][gate]
    assert rep["honesty"]["external_sent"] is False


def test_denied_blocks_outbox(store):
    iid, cid = setup(store)
    apr = X.request_l2_approval(store, iid, "external.send", cid)
    X.decide_l2_approval(store, iid, apr["approval_id"], "denied", "nonce-c1-12345")
    r = X.risk_verdict_with_approval(
        store, iid, "external.send", cid, apr["approval_id"]
    )
    assert r["decision"] == "deny"
    with pytest.raises(C.ContractError):
        X.record_l2_outbox(store, iid, cid, apr["approval_id"], "external.send")


def test_api_routes_c1(store):
    iid, cid = setup(store)
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/approval/request" % iid,
        {"action": "publish", "consumer_id": cid, "summary": "API 发布"},
    )
    assert st == 201
    aid = p["approval"]["approval_id"]
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/approval/decide" % iid,
        {"approval_id": aid, "decision": "approved", "nonce": "nonce-c1-12345"},
    )
    assert st == 201 and p["decision"]["decision"] == "approved"
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/l2/outbox" % iid,
        {
            "consumer_id": cid,
            "approval_id": aid,
            "action": "publish",
            "summary": "API 发布",
            "payload_text": "x",
        },
    )
    assert st == 201 and p["outbox"]["external_sent"] is False
    st, rep = A.handle_get(store, "/api/os/item/%s/chain/c1" % iid)
    assert st == 200 and rep["verdict"] == "CONTROLLED_L2_APPROVED", rep
