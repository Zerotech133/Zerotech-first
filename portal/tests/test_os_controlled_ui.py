# -*- coding: utf-8 -*-
"""C1 审批台：消费者列表 / 审批单列表 API 与页面存在性测试。"""

import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_controlled as X  # noqa: E402
import os_goal_store as S  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def setup(store):
    g = store.create_goal(
        "C1UI 目标",
        "老田",
        [{"id": "A1", "text": "可视化审批", "status": "hypothesis"}],
        ["审批台可用"],
    )
    iid = store.create_item(
        g["payload"]["goal_id"], "C1UI 事项", "审批台验证", "software"
    )["payload"]["item_id"]
    X.register_consumer(
        store, "CONS-UI-01", kind="agent", capabilities=["artifact.generate"]
    )
    return iid


def test_consumer_list_route(store):
    setup(store)
    st, d = A.handle_get(store, "/api/os/consumer/list")
    assert st == 200 and d["ok"] is True
    assert any(c["consumer_id"] == "CONS-UI-01" for c in d["consumers"])


def test_approval_list_route_pending_then_approved(store):
    iid = setup(store)
    apr = X.request_l2_approval(
        store, iid, "publish", "CONS-UI-01", summary="审批台演示"
    )
    st, d = A.handle_get(store, "/api/os/item/%s/approvals" % iid)
    assert st == 200
    row = [a for a in d["approvals"] if a["approval_id"] == apr["approval_id"]][0]
    assert row["status"] == "pending"
    X.decide_l2_approval(
        store,
        iid,
        apr["approval_id"],
        "approved",
        "nonce-ui-12345",
        note="同意受控 outbox",
    )
    st, d = A.handle_get(store, "/api/os/item/%s/approvals" % iid)
    row = [a for a in d["approvals"] if a["approval_id"] == apr["approval_id"]][0]
    assert row["status"] == "approved"
    assert row["decided_note"] == "同意受控 outbox"


def test_approval_page_exists():
    page = os.path.join(PORTAL, "os_approval.html")
    assert os.path.isfile(page)
    text = open(page, encoding="utf-8").read()
    assert "/static/tokens.css" in text and "/static/nav.js" in text
    assert "X-OS-Page" in text and "CONTROLLED_L2_APPROVED" in text
