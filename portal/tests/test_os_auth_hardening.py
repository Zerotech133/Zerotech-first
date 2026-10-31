# -*- coding: utf-8 -*-
"""H2（K-ORG-20261031-PORTALAUTH，审计 v2）：sign_real_grant 服务端一次性 nonce。

审计实证：旧实现只校验 `len(nonce)>=8`、signer 硬编码 laotian → 任意客户端可自造
nonce 冒充"真实签准"。修复：nonce 必须由服务端 POST .../grant/nonce 签发（TTL 120s、
单次消费），且绑定 item/动作/方式/会话 sub；signer 取会话 sub，不再硬编码。
"""
from __future__ import annotations

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


def make_item(store, title="H2 事项"):
    g = store.create_goal("H2 签准收敛", "老田",
                          [{"id": "A1", "text": "nonce 一次性", "status": "hypothesis"}],
                          ["重放/过期/伪造全部拒绝"])
    return store.create_item(g["payload"]["goal_id"], title, "now",
                             "software")["payload"]["item_id"]


def _issue(store, iid, actions, signer_id="u-sess-1", **kw):
    return X.issue_grant_nonce(store, iid, actions, signer_id=signer_id, **kw)


def test_issue_requires_session_signer_and_l1(store):
    iid = make_item(store)
    with pytest.raises(C.ContractError):
        _issue(store, iid, ["artifact.generate"], signer_id="")
    with pytest.raises(C.ContractError):
        _issue(store, iid, ["publish"], signer_id="u-sess-1")   # L2 动作
    with pytest.raises(C.ContractError):
        _issue(store, iid, [], signer_id="u-sess-1")            # 空动作
    with pytest.raises(C.ContractError):
        _issue(store, iid, ["artifact.generate"], signer_id="u-sess-1",
               signer_method="nickname")                        # 非法方式
    n = _issue(store, iid, ["artifact.generate"])
    assert n["nonce"].startswith("n1-") and len(n["nonce"]) > 20
    assert n["expires_in"] == X.NONCE_TTL_SECONDS
    assert n["actions"] == ["artifact.generate"]


def test_sign_with_issued_nonce_uses_session_signer(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"])
    g = X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"],
                          signer_id="u-sess-1")
    assert g["env"] == "controlled"
    assert g["signer"]["kind"] == "user"
    assert g["signer"]["id"] == "u-sess-1"          # 不再硬编码 laotian
    assert g["signer"]["nonce_sha256"].startswith("sha256:")


def test_replay_same_nonce_rejected(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"])
    X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"],
                      signer_id="u-sess-1")
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"],
                          signer_id="u-sess-1")


def test_self_made_nonce_rejected(store):
    iid = make_item(store)
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"],
                          nonce="nonce-123456", signer_id="u-sess-1")


def test_expired_nonce_rejected(store, monkeypatch):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"], ttl_seconds=30)
    real = X.time.time
    monkeypatch.setattr(X.time, "time", lambda: real() + 31)
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"],
                          signer_id="u-sess-1")


def test_nonce_bound_to_item(store):
    iid1 = make_item(store, "事项1")
    iid2 = make_item(store, "事项2")
    n = _issue(store, iid1, ["artifact.generate"])
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid2, ["artifact.generate"], nonce=n["nonce"],
                          signer_id="u-sess-1")


def test_nonce_bound_to_actions(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"])
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["test.run"], nonce=n["nonce"],
                          signer_id="u-sess-1")


def test_nonce_bound_to_signer_method(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"], signer_method="session_nonce")
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"],
                          signer_method="local_password_session", signer_id="u-sess-1")


def test_forged_signer_rejected(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"], signer_id="u-a")
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"],
                          signer_id="u-b")   # 拿别人的 nonce 伪造 signer


def test_sign_without_session_sub_rejected(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"])
    with pytest.raises(C.ContractError):
        X.sign_real_grant(store, iid, ["artifact.generate"], nonce=n["nonce"])


# ── API 路由：nonce 端点 + grant/real 携带会话 sub ──────────────────────────

def _post(store, path, body, auth_sub=""):
    return A.handle_post(store, path, body, auth_sub=auth_sub)


def test_api_nonce_then_grant(store):
    iid = make_item(store)
    st, p = _post(store, "/api/os/item/%s/grant/nonce" % iid,
                  {"actions": ["artifact.generate"]}, auth_sub="u-api")
    assert st == 201 and p["nonce"], p
    st, p = _post(store, "/api/os/item/%s/grant/real" % iid,
                  {"actions": ["artifact.generate"], "nonce": p["nonce"]},
                  auth_sub="u-api")
    assert st == 201 and p["grant"]["signer"]["id"] == "u-api", p
    # 重放 → 422 contract_violation（不是 201 假成功）
    st, p = _post(store, "/api/os/item/%s/grant/real" % iid,
                  {"actions": ["artifact.generate"], "nonce": "n1-forged"},
                  auth_sub="u-api")
    assert st == 422 and p["error"] == "contract_violation"


def test_api_nonce_requires_auth_sub(store):
    iid = make_item(store)
    st, p = _post(store, "/api/os/item/%s/grant/nonce" % iid,
                  {"actions": ["artifact.generate"]}, auth_sub="")
    assert st == 422, p   # ContractError：签发 nonce 需要登录会话


def test_api_grant_requires_session_sub(store):
    iid = make_item(store)
    n = _issue(store, iid, ["artifact.generate"])
    st, p = _post(store, "/api/os/item/%s/grant/real" % iid,
                  {"actions": ["artifact.generate"], "nonce": n["nonce"]},
                  auth_sub="")
    assert st == 422, p
