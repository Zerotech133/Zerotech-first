# -*- coding: utf-8 -*-
"""G0 API 纯函数单测（不起 HTTP、不绑端口）。"""
import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_goal_store as S  # noqa: E402

SHA = "sha256:" + "c" * 64
CITE = {"source_surface": "research", "source_ref": "RES-001",
        "source_kind": "data", "claim": "样本内胜率 55%", "confidence": "fact"}


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def create_goal(store):
    st, d = A.handle_post(store, "/api/os/goal", {
        "statement": "把零界做成持续创业者的个人事业OS",
        "target_user": "连续创业者",
        "assumptions": [{"id": "A1", "text": "用户愿付费", "status": "hypothesis"}],
        "success_signals": ["四类成果各验收一次"],
        "idempotency_key": "IDEM-API-1"})
    assert st == 201, d
    return d["goal_id"]


def test_create_and_list_goal(store):
    gid = create_goal(store)
    st, d = A.handle_get(store, "/api/os/goals")
    assert st == 200 and d["goals"][0]["goal_id"] == gid


def test_create_item_and_get(store):
    gid = create_goal(store)
    st, d = A.handle_post(store, "/api/os/item", {
        "goal_id": gid, "title": "做一页原型", "why_now": "今天要试用",
        "outcome_kind": "software", "attempt_no": 1})
    assert st == 201
    iid = d["item_id"]
    st2, item = A.handle_get(store, "/api/os/item/" + iid)
    assert st2 == 200 and item["item_id"] == iid


def test_item_with_missing_goal_422(store):
    st, d = A.handle_post(store, "/api/os/item", {
        "goal_id": "GOL-20261024-9999", "title": "t", "why_now": "now",
        "outcome_kind": "software"})
    assert st == 422 and d["error"] == "contract_violation"


def test_outcome_kind_mismatch_422(store):
    gid = create_goal(store)
    _, d = A.handle_post(store, "/api/os/item", {
        "goal_id": gid, "title": "t", "why_now": "now",
        "outcome_kind": "software"})
    st, resp = A.handle_post(store, "/api/os/item/%s/outcome" % d["item_id"], {
        "outcome_kind": "research", "version": "v1",
        "content_ref": "x", "sha256": SHA, "citations": [CITE]})
    assert st == 422


def test_duplicate_idempotency_409(store):
    create_goal(store)
    st, d = A.handle_post(store, "/api/os/goal", {
        "statement": "x", "target_user": "y",
        "assumptions": [{"id": "A1", "text": "h", "status": "hypothesis"}],
        "success_signals": ["s"], "idempotency_key": "IDEM-API-1"})
    assert st == 409 and d["error"] == "duplicate_idempotency_key"


def test_get_unknown_item_404(store):
    st, d = A.handle_get(store, "/api/os/item/ITM-20261024-0001")
    assert st == 404


def test_evidence_bundle_api(store):
    gid = create_goal(store)
    _, d = A.handle_post(store, "/api/os/item", {
        "goal_id": gid, "title": "t", "why_now": "now",
        "outcome_kind": "content"})
    iid = d["item_id"]
    A.handle_post(store, "/api/os/item/%s/outcome" % iid, {
        "outcome_kind": "content", "version": "v1", "content_ref": "x",
        "sha256": SHA, "citations": [CITE]})
    st, bundle = A.handle_get(store, "/api/os/item/%s/evidence" % iid)
    assert st == 200 and bundle["report"]["verdict"] == "PASS"
    assert bundle["event_count"] >= 3


def test_health(store):
    st, d = A.handle_get(store, "/api/os/health")
    assert st == 200 and d["ok"] is True
