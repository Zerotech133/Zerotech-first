# -*- coding: utf-8 -*-
"""Quickstart 单测：一句话→一张卡→确认即执行（三句话交互）。"""
import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_goal_contract as C  # noqa: E402
import os_goal_store as S  # noqa: E402
import os_quickstart as Q  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    Q._DRAFTS.clear()
    Q._SEQ[0] = 0
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def test_card_defaults(store):
    card = Q.create_draft("做个页面，让我每天看三条简报摘要")
    assert card["scope"]["env"] == "isolated"
    assert len(card["scope"]["actions"]) == 4
    assert card["scope"]["reversible"] is True
    assert card["honesty"]["card_source"] == "rule_assisted"
    assert card["success_criteria"] and all(
        sc.get("verify_method") for sc in card["success_criteria"])
    assert card["goals"][0].startswith("（原话）")


def test_empty_text_rejected(store):
    with pytest.raises(C.ContractError):
        Q.create_draft("   ")


def test_confirm_executes_isolated_chain(store):
    card = Q.create_draft("做个页面，让我每天看三条简报摘要")
    out = Q.confirm(card["draft_id"], store)
    assert out["item_id"].startswith("ITM-")
    assert out["artifact"]["sha256"].startswith("sha256:")
    assert os.path.isfile(out["artifact"]["path"])
    assert out["chain"]["gates"]["G6"] == "PASS"
    assert out["chain"]["verdict"] == "BLOCKED"  # 等待本人第 3 句验收
    st, d = A.handle_post(store, "/api/os/item/%s/verdict" % out["item_id"],
                          {"decision": "accepted", "feedback": "接受"})
    assert st == 201, d
    import os_software_flow as F
    rep = F.chain_report(store, out["item_id"])
    assert rep["verdict"] == "ISOLATED_ONLY", rep
    # trial 已记录（确认后自动打开），验收走既有端点
    evs = [e for e in store.events_for(out["item_id"])
           if e["type"] == "trial.opened"]
    assert evs and evs[-1]["actor"]["kind"] == "user"
    assert "自动打开试用" in evs[-1]["payload"]["note"]


def test_confirm_idempotent(store):
    card = Q.create_draft("做个页面看简报")
    a = Q.confirm(card["draft_id"], store)
    b = Q.confirm(card["draft_id"], store)
    assert a["item_id"] == b["item_id"]
    items = [e for e in store._events if e["type"] == "item.created"]
    assert len(items) == 1


def test_edit_changes_goals_and_acceptance(store):
    card = Q.create_draft("做个页面看简报")
    out = Q.confirm(card["draft_id"], store,
                    edits={"changes": "标题改成今日简报，只显示两条"})
    brief = [e for e in store.events_for(out["item_id"])
             if e["type"] == "brief.frozen"][-1]
    joined = " ".join(brief["payload"]["goals"])
    assert "只显示两条" in joined
    grant = [e for e in store.events_for(out["item_id"])
             if e["type"] == "grant.signed"][-1]
    assert set(grant["payload"]["actions"]) == set(Q.DEFAULT_ACTIONS)
    assert grant["payload"]["env"] == "isolated"


def test_default_goal_reused_across_drafts(store):
    a = Q.confirm(Q.create_draft("第一件事")["draft_id"], store)
    b = Q.confirm(Q.create_draft("第二件事")["draft_id"], store)
    assert a["goal_id"] == b["goal_id"]


def test_api_quickstart_routes(store):
    st, d = A.handle_post(store, "/api/os/quickstart", {"text": "做个页面看简报"})
    assert st == 201 and d["card"]["draft_id"].startswith("QK-")
    did = d["card"]["draft_id"]
    st2, d2 = A.handle_get(store, "/api/os/quickstart/" + did)
    assert st2 == 200 and d2["card"]["draft_id"] == did
    st3, d3 = A.handle_post(store, "/api/os/quickstart/%s/confirm" % did, {})
    assert st3 == 201 and d3["item_id"].startswith("ITM-")


def test_quick_page_three_piece_and_turns():
    page = os.path.join(PORTAL, "os_quick.html")
    assert os.path.isfile(page)
    text = open(page, encoding="utf-8").read()
    assert 'nav.js" defer' in text and "tokens.css" in text and "shortscreen.js" in text
    for kw in ("第 1 句", "第 2 句", "第 3 句", "按这个办", "这里改"):
        assert kw in text
