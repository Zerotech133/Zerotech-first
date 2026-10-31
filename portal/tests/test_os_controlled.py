# -*- coding: utf-8 -*-
"""C 阶段最小真实受控闭环（CONTROLLED_ONLY）测试。"""

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


def make_item(store, kind="software"):
    g = store.create_goal(
        "受控交付验证目标",
        "老田",
        [{"id": "A1", "text": "可受控执行", "status": "hypothesis"}],
        ["隔离首版可验收"],
    )
    return store.create_item(
        g["payload"]["goal_id"], "受控交付事项", "C 阶段最小闭环", kind
    )["payload"]["item_id"]


def _sign(store, iid, actions, signer_id="u-test"):
    """H2：先向服务端申请一次性 nonce，再以会话 sub 签准。"""
    n = X.issue_grant_nonce(store, iid, actions, signer_id=signer_id)
    return X.sign_real_grant(store, iid, actions, nonce=n["nonce"],
                             signer_id=signer_id)


def happy_setup(store, consumer_id="CONS-AGENT-01"):
    iid = make_item(store)
    X.register_consumer(
        store,
        consumer_id,
        kind="agent",
        display_name="本机 Agent",
        capabilities=["artifact.generate", "test.run"],
    )
    grant = _sign(store, iid, ["artifact.generate", "test.run"])
    wo = X.create_formal_workorder(
        store, iid, "产物可打开、可核对", required_capabilities=["artifact.generate"]
    )
    X.risk_verdict(store, iid, "artifact.generate", consumer_id=consumer_id)
    X.real_claim(store, iid, consumer_id)
    return iid, consumer_id, grant, wo


def test_register_and_list_consumer(store):
    c = X.register_consumer(
        store, "CONS-AGENT-01", kind="agent", capabilities=["artifact.generate"]
    )
    assert c["consumer_id"] == "CONS-AGENT-01" and c["status"] == "active"
    again = X.register_consumer(store, "CONS-AGENT-01")
    assert again["consumer_id"] == c["consumer_id"]
    assert len(X.list_consumers(store)) == 1
    with pytest.raises(C.ContractError):
        X.register_consumer(store, "bad id!")


def test_grant_requires_server_nonce_and_l1_actions(store):
    """H2：不再接受自造 nonce；必须服务端签发的一次性 nonce + 会话 signer。"""
    iid = make_item(store)
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"], nonce="")
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"],
                          nonce="nonce-123456", signer_id="u-test")  # 自造 nonce 拒绝
    with pytest.raises(C.ContractError):
        X.issue_grant_nonce(store, iid, ["publish"], signer_id="u-test")  # L2 动作拒绝
    with pytest.raises(C.ContractError):
        X.issue_grant_nonce(store, iid, ["artifact.generate"], signer_id="")  # 无会话拒绝
    g = _sign(store, iid, ["artifact.generate"])
    assert g["env"] == "controlled"
    assert g["signer"]["kind"] == "user"
    assert g["signer"]["id"] == "u-test"
    assert g["signer"]["nonce_sha256"].startswith("sha256:")


def test_workorder_requires_valid_grant(store):
    iid = make_item(store)
    with pytest.raises(C.ContractError):
        X.create_formal_workorder(store, iid, "验收口径")
    _sign(store, iid, ["artifact.generate"])
    wo = X.create_formal_workorder(store, iid, "产物可打开")
    assert wo["env"] == "controlled" and wo["acceptance"] == "产物可打开"


def test_real_claim_rejects_unknown_consumer(store):
    iid = make_item(store)
    _sign(store, iid, ["artifact.generate"])
    X.create_formal_workorder(store, iid, "验收口径")
    with pytest.raises(C.ContractError):
        X.real_claim(store, iid, "CONS-NOT-REGISTERED")


def test_risk_denies_l2_and_allows_l1(store):
    iid = make_item(store)
    bad = X.risk_verdict(store, iid, "publish")
    assert bad["decision"] == "deny" and bad["risk_level"] == "L2"
    good = X.risk_verdict(store, iid, "artifact.generate")
    assert good["decision"] == "allow" and good["risk_level"] == "L1"


def test_happy_path_controlled_only(store):
    iid, cid, grant, wo = happy_setup(store)
    out = X.produce_controlled_artifact(
        store,
        iid,
        cid,
        "artifact.md",
        "# 受控产物\n\n来自真实注册消费者，经签准/正式工单/风控放行后生成。\n",
    )
    art = out["artifact"]
    assert art["env"] == "controlled"
    assert art["sha256"].startswith("sha256:")
    assert os.path.isfile(art["path"])
    rep = X.chain_report_controlled(store, iid)
    assert rep["verdict"] == "CONTROLLED_ONLY", rep
    assert rep["exit_code"] == 0
    for gate in ("C0-AUTH", "C0-WO", "C0-CLAIM", "C0-RISK", "C0-ART", "C0-AUDIT"):
        assert rep["gates"][gate]["status"] == "PASS", rep["gates"][gate]


def test_artifact_requires_active_real_claim(store):
    iid = make_item(store)
    X.register_consumer(
        store, "CONS-AGENT-01", kind="agent", capabilities=["artifact.generate"]
    )
    _sign(store, iid, ["artifact.generate"])
    X.create_formal_workorder(store, iid, "验收口径")
    with pytest.raises(C.ContractError):
        X.produce_controlled_artifact(store, iid, "CONS-AGENT-01", "a.md", "内容")


def test_bad_filename_rejected(store):
    iid, cid, _, _ = happy_setup(store)
    with pytest.raises(C.ContractError):
        X.produce_controlled_artifact(store, iid, cid, "../evil.sh", "x")


def test_api_routes_controlled(store):
    iid = make_item(store)
    st, p = A.handle_post(
        store,
        "/api/os/consumer/register",
        {"consumer_id": "CONS-API-01", "capabilities": ["artifact.generate"]},
    )
    assert st == 201 and p["consumer"]["status"] == "active"
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/grant/nonce" % iid,
        {"actions": ["artifact.generate"]},
        auth_sub="u-api",
    )
    assert st == 201 and p["nonce"]
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/grant/real" % iid,
        {"actions": ["artifact.generate"], "nonce": p["nonce"]},
        auth_sub="u-api",
    )
    assert st == 201 and p["grant"]["env"] == "controlled"
    assert p["grant"]["signer"]["id"] == "u-api"
    st, p = A.handle_post(
        store, "/api/os/item/%s/workorder/formal" % iid, {"acceptance": "API 验收"}
    )
    assert st == 201
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/risk/verdict" % iid,
        {"action": "artifact.generate", "consumer_id": "CONS-API-01"},
    )
    assert st == 201 and p["risk"]["decision"] == "allow"
    st, p = A.handle_post(
        store, "/api/os/item/%s/claim/real" % iid, {"consumer_id": "CONS-API-01"}
    )
    assert st == 201 and p["claim"]["claim"]["method"] == "real"
    st, p = A.handle_post(
        store,
        "/api/os/item/%s/controlled/artifact" % iid,
        {"consumer_id": "CONS-API-01", "filename": "x.md", "content": "# x\n"},
    )
    assert st == 201 and p["artifact"]["env"] == "controlled"
    st, rep = A.handle_get(store, "/api/os/item/%s/chain/controlled" % iid)
    assert st == 200 and rep["verdict"] == "CONTROLLED_ONLY", rep
