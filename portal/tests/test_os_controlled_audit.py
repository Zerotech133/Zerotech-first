# -*- coding: utf-8 -*-
"""C0/C1 审计修复回归测试：撤销/停用/重复裁决/过期/能力匹配。"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import os_controlled as X  # noqa: E402
import os_goal_contract as C  # noqa: E402
import os_goal_store as S  # noqa: E402

BJT = timezone(timedelta(hours=8))


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def make_item(store):
    g = store.create_goal(
        "审计目标",
        "老田",
        [{"id": "A1", "text": "受控", "status": "hypothesis"}],
        ["审计通过"],
    )
    return store.create_item(g["payload"]["goal_id"], "审计事项", "审计", "software")[
        "payload"
    ]["item_id"]


def _sign(store, iid, actions, signer_id="u-test"):
    n = X.issue_grant_nonce(store, iid, actions, signer_id=signer_id)
    return X.sign_real_grant(store, iid, actions, nonce=n["nonce"],
                             signer_id=signer_id)


def setup(store, caps=None, workorder_caps=None):
    iid = make_item(store)
    X.register_consumer(
        store,
        "CONS-AUD",
        kind="agent",
        capabilities=caps or ["artifact.generate", "test.run"],
    )
    g = _sign(store, iid, ["artifact.generate", "test.run"])
    X.create_formal_workorder(
        store,
        iid,
        "验收口径",
        required_capabilities=workorder_caps or ["artifact.generate"],
    )
    return iid, g


def test_deactivated_consumer_cannot_claim(store):
    iid, _ = setup(store)
    X.deactivate_consumer(store, "CONS-AUD")
    with pytest.raises(C.ContractError):
        X.real_claim(store, iid, "CONS-AUD")


def test_revoked_grant_blocks_new_workorder(store):
    iid, g = setup(store)
    X.revoke_grant(store, iid, g["grant_id"])
    with pytest.raises(C.ContractError):
        X.create_formal_workorder(store, iid, "二次工单")
    with pytest.raises(C.ContractError):
        X.real_claim(store, iid, "CONS-AUD")


def test_revoke_after_claim_blocks_artifact(store):
    iid, g = setup(store)
    X.risk_verdict(store, iid, "artifact.generate", consumer_id="CONS-AUD")
    X.real_claim(store, iid, "CONS-AUD")
    X.revoke_grant(store, iid, g["grant_id"])
    with pytest.raises(C.ContractError):
        X.produce_controlled_artifact(store, iid, "CONS-AUD", "a.md", "内容")


def test_chain_auth_pass_after_revoke_when_artifact_precedes(store):
    iid, g = setup(store)
    X.risk_verdict(store, iid, "artifact.generate", consumer_id="CONS-AUD")
    X.real_claim(store, iid, "CONS-AUD")
    X.produce_controlled_artifact(store, iid, "CONS-AUD", "a.md", "内容")
    X.revoke_grant(store, iid, g["grant_id"])
    rep = X.chain_report_controlled(store, iid)
    assert rep["gates"]["C0-AUTH"]["status"] == "PASS", rep["gates"]["C0-AUTH"]
    assert rep["verdict"] == "CONTROLLED_ONLY"


def test_chain_auth_fail_when_revoked_before_artifact(store):
    iid, g = setup(store)
    X.revoke_grant(store, iid, g["grant_id"])
    rep = X.chain_report_controlled(store, iid)
    assert rep["gates"]["C0-AUTH"]["status"] == "FAIL"
    assert rep["verdict"] != "CONTROLLED_ONLY"


def test_consumer_capability_mismatch_blocks_claim(store):
    iid, _ = setup(store, caps=["test.run"], workorder_caps=["artifact.generate"])
    with pytest.raises(C.ContractError):
        X.real_claim(store, iid, "CONS-AUD")


def test_double_approval_decision_rejected(store):
    iid, _ = setup(store)
    apr = X.request_l2_approval(store, iid, "publish", "CONS-AUD")
    X.decide_l2_approval(store, iid, apr["approval_id"], "approved", "nonce-c1-12345")
    with pytest.raises(C.ContractError):
        X.decide_l2_approval(store, iid, apr["approval_id"], "denied", "nonce-c1-12345")


def test_expired_approval_blocks_outbox(store):
    iid, _ = setup(store)
    aid = "APR-EXPIRED-001"
    past = (datetime.now(BJT) - timedelta(minutes=5)).isoformat(timespec="seconds")
    store.append_event(
        "approval.requested",
        {"item_id": iid},
        {
            "approval_id": aid,
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "summary": "过期测试",
            "expires_at": past,
            "status": "pending",
            "env": "controlled",
            "external_sent": False,
        },
        actor={"kind": "consumer", "id": "CONS-AUD"},
    )
    store.append_event(
        "approval.decided",
        {"item_id": iid},
        {
            "approval_id": aid,
            "decision": "approved",
            "note": "历史批准",
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "signer": {"kind": "user", "id": "laotian"},
        },
        actor={"kind": "user", "id": "laotian"},
    )
    st = X._approval_state(store, iid, aid)
    assert st["status"] == "expired"
    with pytest.raises(C.ContractError):
        X.record_l2_outbox(store, iid, "CONS-AUD", aid, "publish")


def test_chain_c1_blocked_when_approval_expired(store):
    iid, _ = setup(store)
    aid = "APR-EXPIRED-002"
    past = (datetime.now(BJT) - timedelta(minutes=5)).isoformat(timespec="seconds")
    store.append_event(
        "approval.requested",
        {"item_id": iid},
        {
            "approval_id": aid,
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "summary": "过期测试",
            "expires_at": past,
            "status": "pending",
        },
        actor={"kind": "consumer", "id": "CONS-AUD"},
    )
    store.append_event(
        "approval.decided",
        {"item_id": iid},
        {
            "approval_id": aid,
            "decision": "approved",
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "signer": {"kind": "user", "id": "laotian"},
        },
        actor={"kind": "user", "id": "laotian"},
    )
    rep = X.chain_report_c1(store, iid)
    assert rep["gates"]["C1-DECISION"]["status"] == "BLOCKED"
    assert rep["verdict"] != "CONTROLLED_L2_APPROVED"


def test_api_grant_revoke_route(store):
    iid, g = setup(store)
    st, d = __import__("api_os_goal").handle_post(
        store, "/api/os/item/%s/grant/revoke" % iid, {"grant_id": g["grant_id"]}
    )
    assert st == 201 and d["ok"] is True
    with __import__("pytest").raises(C.ContractError):
        X.create_formal_workorder(store, iid, "撤销后工单")


def test_api_consumer_deactivate_route(store):
    iid, _ = setup(store)
    st, d = __import__("api_os_goal").handle_post(
        store, "/api/os/consumer/deactivate", {"consumer_id": "CONS-AUD", "reason": "t"}
    )
    assert st == 201 and d["ok"] is True
    with __import__("pytest").raises(C.ContractError):
        X.real_claim(store, iid, "CONS-AUD")


def test_consumer_reactivation_works(store):
    iid, _ = setup(store)
    X.deactivate_consumer(store, "CONS-AUD")
    c = X.register_consumer(
        store, "CONS-AUD", kind="agent", capabilities=["artifact.generate", "test.run"]
    )
    assert c["status"] == "active" and c.get("reactivated") is True
    X.risk_verdict(store, iid, "artifact.generate", consumer_id="CONS-AUD")
    X.real_claim(store, iid, "CONS-AUD")


def test_bad_numeric_field_returns_422_not_500(store):
    import api_os_goal as A

    iid, _ = setup(store)
    st, d = A.handle_post(
        store,
        "/api/os/item/%s/grant/real" % iid,
        {
            "actions": ["artifact.generate"],
            "nonce": "nonce-audit-123",
            "valid_hours": "abc",
        },
    )
    assert st == 422 and d.get("error") == "bad_value", (st, d)


def test_bad_ttl_returns_422_not_500(store):
    import api_os_goal as A

    iid, _ = setup(store)
    st, d = A.handle_post(
        store,
        "/api/os/item/%s/approval/request" % iid,
        {"action": "publish", "consumer_id": "CONS-AUD", "ttl_minutes": "xx"},
    )
    assert st == 422 and d.get("error") == "bad_value", (st, d)


def _happy_produce(store):
    iid, g = setup(store)
    X.risk_verdict(store, iid, "artifact.generate", consumer_id="CONS-AUD")
    X.real_claim(store, iid, "CONS-AUD")
    X.produce_controlled_artifact(store, iid, "CONS-AUD", "a.md", "内容")
    return iid, g


def test_c0_chain_history_stable_after_revoke(store):
    iid, g = _happy_produce(store)
    X.revoke_grant(store, iid, g["grant_id"])
    rep = X.chain_report_controlled(store, iid)
    assert rep["gates"]["C0-AUTH"]["status"] == "PASS", rep["gates"]["C0-AUTH"]
    assert rep["verdict"] == "CONTROLLED_ONLY", rep


def test_c0_chain_history_stable_after_deactivate(store):
    iid, _ = _happy_produce(store)
    X.deactivate_consumer(store, "CONS-AUD")
    rep = X.chain_report_controlled(store, iid)
    assert rep["gates"]["C0-CLAIM"]["status"] == "PASS", rep["gates"]["C0-CLAIM"]
    assert rep["verdict"] == "CONTROLLED_ONLY", rep


def test_c0_chain_history_stable_after_grant_expiry(store, monkeypatch):
    """签准真实过期后，历史产物仍按产物产生时刻判定（不追溯翻案）。

    回归：曾误读事件字段 art["ts"]（实际字段是 at），恒回落 now()，
    导致真实签准过期后历史链被判定超期 → 现网 C0-AUTH 翻 FAIL。
    """
    iid, _ = _happy_produce(store)

    class _FutureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=3)

    monkeypatch.setattr(X, "datetime", _FutureDatetime)
    rep = X.chain_report_controlled(store, iid)
    assert rep["gates"]["C0-AUTH"]["status"] == "PASS", rep["gates"]["C0-AUTH"]
    assert rep["verdict"] == "CONTROLLED_ONLY", rep


def test_c1_chain_history_stable_after_expiry(store):
    iid, _ = setup(store)
    aid = "APR-HIST-001"
    past = (datetime.now(BJT) - timedelta(minutes=10)).isoformat(timespec="seconds")
    store.append_event(
        "approval.requested",
        {"item_id": iid},
        {
            "approval_id": aid,
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "summary": "历史审批",
            "expires_at": past,
            "status": "pending",
        },
        actor={"kind": "consumer", "id": "CONS-AUD"},
    )
    store.append_event(
        "approval.decided",
        {"item_id": iid},
        {
            "approval_id": aid,
            "decision": "approved",
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "signer": {"kind": "user", "id": "laotian"},
        },
        actor={"kind": "user", "id": "laotian"},
    )
    store.append_event(
        "risk.verdict",
        {"item_id": iid},
        {
            "action": "publish",
            "decision": "allow",
            "approval_id": aid,
            "consumer_id": "CONS-AUD",
            "reasons": ["历史"],
            "risk_level": "L2",
        },
        actor={"kind": "system", "id": "os_controlled"},
    )
    store.append_event(
        "l2.outbox.recorded",
        {"item_id": iid},
        {
            "outbox_id": "OUT-HIST-001",
            "approval_id": aid,
            "action": "publish",
            "consumer_id": "CONS-AUD",
            "external_sent": False,
            "env": "controlled",
        },
        actor={"kind": "consumer", "id": "CONS-AUD"},
    )
    rep = X.chain_report_c1(store, iid)
    assert rep["gates"]["C1-DECISION"]["status"] == "PASS", rep["gates"]["C1-DECISION"]
    assert rep["verdict"] == "CONTROLLED_L2_APPROVED", rep
