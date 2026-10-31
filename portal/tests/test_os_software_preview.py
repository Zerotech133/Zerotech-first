# -*- coding: utf-8 -*-
"""G1b：只读预览路由（白名单 + 路径限定）与软件面工作台页面静态检查。"""

import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
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


def build_item(store):
    goal = store.create_goal(
        "把零界做成持续创业者的个人事业OS",
        "连续创业者",
        [{"id": "A1", "text": "用户愿付费", "status": "hypothesis"}],
        ["四类成果各验收一次"],
    )
    iid = store.create_item(
        goal["payload"]["goal_id"], "做一页可试用原型", "老田今天要试用", "software"
    )["payload"]["item_id"]
    F.freeze_brief(
        store,
        iid,
        ["把一句需求做成可试用单页"],
        [
            {
                "id": "SC1",
                "statement": "页面可打开",
                "verify_method": "静态自检 data-artifact",
            }
        ],
    )
    F.freeze_card(
        store,
        iid,
        {"option": "A｜隔离单页", "why": "可逆"},
        [{"option": "B｜改现网", "why": "不可逆"}],
        "本人试用并反馈",
    )
    F.select_card(store, iid, "c1")
    F.sign_grant(store, iid, "c1", ACTIONS)
    F.create_workorder(store, iid)
    F.claim(store, iid, method="simulated", isolation_label="隔离验证/未获真实认领")
    F.execute(store, iid)
    return iid


def test_preview_serves_index_html_as_plain_text(store):
    """H1（K-ORG-20261031-PORTALAUTH）：预览不再以 text/html 执行（防存储型 XSS）。"""
    iid = build_item(store)
    status, ctype, body = A.read_workspace_file(store, iid, "index.html")
    assert status == 200
    assert ctype == "text/plain; charset=utf-8"
    assert b"data-artifact" in body and b"<h1>" in body  # 原文可读、但不执行


def test_preview_rejects_traversal_and_unknown(store):
    iid = build_item(store)
    assert A.read_workspace_file(store, iid, "../events.jsonl")[0] == 404
    assert A.read_workspace_file(store, iid, "secret.txt")[0] == 404
    assert A.read_workspace_file(store, iid, "index.html/../../events.jsonl")[0] == 404


def test_preview_missing_item_404(store):
    assert A.read_workspace_file(store, "ITM-20261024-9999", "index.html")[0] == 404


def test_workbench_page_three_piece_and_five_steps():
    page = os.path.join(PORTAL, "os_software.html")
    assert os.path.isfile(page)
    text = open(page, encoding="utf-8").read()
    assert 'nav.js" defer' in text
    assert "tokens.css" in text and "shortscreen.js" in text
    for step in ("① 需求", "② 推荐卡", "③ 本人选卡", "④ 工单", "⑤ 本人试用"):
        assert step in text
    assert "未获真实认领" in text and "规则辅助" in text
