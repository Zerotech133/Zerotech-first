# -*- coding: utf-8 -*-
"""K-ORG-201 W37/W38：公告审批流（卡片确认）+ 送达证据/死信 单测。

全离线：决策 DB / push_subscribers / 流水 / 死信全部 tmp_path 注入，
飞书 HTTP 全 mock（零触网零真实发送）。红线验收点：
  - approve_from_card 身份配对 fail-closed；
  - 卡片确认只留痕，execute 开关（PUSH_DECISION_EXECUTE）不松动；
  - 死信补达不冒充原发送成功（原行字段不动）。
"""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):  # portal 本目录 + 仓根（push_flow）
    if p not in sys.path:
        sys.path.insert(0, p)

import notification_decision as nd  # noqa: E402
import dead_letter  # noqa: E402
import approval_card  # noqa: E402
import card_actions  # noqa: E402
import push_flow  # noqa: E402

LAOTIAN = "ou_laotian_mock"


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(nd, "DB_PATH", str(tmp_path / "decisions.sqlite3"))
    monkeypatch.setattr(nd, "targets", lambda: [{"name": "测试群", "id": "oc_mock", "type": "chat_id"}])
    monkeypatch.setenv(nd.LAOTIAN_OPEN_ID_ENV, LAOTIAN)
    monkeypatch.delenv("PUSH_DECISION_EXECUTE", raising=False)
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps({"version": 1, "groups": {
        "laotian_self": {"name": "老田", "markets": ["cn"], "tier": "daily",
                         "chat_id": LAOTIAN, "chat_id_type": "open_id"}}}),
        encoding="utf-8")
    monkeypatch.setattr(nd, "SUBSCRIBERS_PATH", str(subs))
    monkeypatch.setattr(dead_letter, "DEAD_PATH", str(tmp_path / "dead_letters.jsonl"))
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(tmp_path / "push_flow.jsonl"))
    return tmp_path


def _announcement(**over):
    req = {"id": str(uuid.uuid4()), "category": "announcement",
           "target_name": "测试群",
           "send_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
           "reason": "管理需要", "content": "系统维护公告",
           "agent_source": "human", "operator": "管理员", "basis": "常规维护",
           "confirmed_target": "测试群"}
    req.update(over)
    return req


def _record(req):
    r = nd.record(req, caller="admin_ui")
    assert r["ok"], r
    return req


# ---------- approve_from_card：身份配对（验收点） ----------

def test_approve_identity_pairing_blocks(iso, monkeypatch):
    req = _record(_announcement())
    # open_id 不匹配 → 阻断
    r = nd.approve_from_card({"id": req["id"]}, "ou_someone_else")
    assert not r["ok"] and "阻断" in r["error"]
    # None / 空串 → 阻断
    assert not nd.approve_from_card({"id": req["id"]}, None)["ok"]
    assert not nd.approve_from_card({"id": req["id"]}, "  ")["ok"]
    # 授权身份缺失（env 与订阅文件都无）→ fail-closed 阻断
    monkeypatch.delenv(nd.LAOTIAN_OPEN_ID_ENV, raising=False)
    monkeypatch.setattr(nd, "SUBSCRIBERS_PATH", str(iso / "nope.json"))
    assert not nd.approve_from_card({"id": req["id"]}, LAOTIAN)["ok"]
    with nd._db() as db:
        assert db.execute("SELECT COUNT(*) FROM announcement_approvals").fetchone()[0] == 0


def test_laotian_open_id_sources(iso, monkeypatch):
    monkeypatch.delenv(nd.LAOTIAN_OPEN_ID_ENV, raising=False)
    # 文件兜底：laotian_self chat_id_type=open_id 才认
    assert nd._laotian_open_id() == LAOTIAN
    bad = iso / "subs_bad.json"
    bad.write_text(json.dumps({"groups": {"laotian_self": {
        "chat_id": LAOTIAN, "chat_id_type": "chat_id"}}}), encoding="utf-8")
    monkeypatch.setattr(nd, "SUBSCRIBERS_PATH", str(bad))
    assert nd._laotian_open_id() is None       # 非 open_id 口径不认
    monkeypatch.setattr(nd, "SUBSCRIBERS_PATH", str(iso / "nope.json"))
    assert nd._laotian_open_id() is None       # 文件缺失 → None
    monkeypatch.setenv(nd.LAOTIAN_OPEN_ID_ENV, LAOTIAN)
    assert nd._laotian_open_id() == LAOTIAN    # env 优先


# ---------- approve_from_card：行核对 / 留痕 / 幂等 ----------

def test_approve_row_checks_and_reApproval_writes(iso):
    alert = _record(_announcement(category="alert", id=str(uuid.uuid4()),
                                  reason="风险", basis="预警"))
    r = nd.approve_from_card({"id": alert["id"]}, LAOTIAN)
    assert not r["ok"]          # 非 announcement 不可走卡片审批
    req = _record(_announcement())
    assert not nd.approve_from_card({"id": "no-such-id"}, LAOTIAN)["ok"]
    # 内容不一致拒（req 显式携带字段须与行一致）
    r = nd.approve_from_card({"id": req["id"], "content": "篡改"}, LAOTIAN)
    assert not r["ok"] and "不一致" in r["error"]
    r = nd.approve_from_card({"id": req["id"],
                              "send_at": "2000-01-01T00:00:00+00:00"}, LAOTIAN)
    assert not r["ok"]
    # 正确通过（卡片链只传 id）→ 写 announcement_approvals，status 仍 recorded
    assert nd.approve_from_card({"id": req["id"]}, LAOTIAN)["ok"]
    with nd._db() as db:
        row = db.execute("SELECT status FROM decisions WHERE id=?",
                         (req["id"],)).fetchone()
        assert row["status"] == "recorded"
        ap = db.execute("SELECT operator,caller,decision_id FROM "
                        "announcement_approvals").fetchone()
    assert ap["caller"] == "feishu_card_laotian"
    assert ap["operator"] == LAOTIAN[:8] + "(飞书卡片)"
    assert ap["decision_id"] == req["id"]
    # 幂等：已审批过再确认 → duplicate
    r = nd.approve_from_card({"id": req["id"]}, LAOTIAN)
    assert not r["ok"] and r.get("duplicate") is True


# ---------- 红线：审批后 execute 仍受开关门控，开关开则发并落 mid ----------

def test_execute_switch_not_loosened_by_approval(iso, monkeypatch):
    req = _record(_announcement())
    assert nd.approve_from_card({"id": req["id"]}, LAOTIAN)["ok"]
    sent = []
    sender = lambda payload: sent.append(payload) or {"ok": True}
    # 开关未开 → 审批留痕也拦（红线验收点）
    r = nd.execute(req["id"], sender)
    assert not r["ok"] and "PUSH_DECISION_EXECUTE" in r["error"] and not sent
    # 开关开 → fake sender 成功，status=sent 且 message_id 落列
    monkeypatch.setenv("PUSH_DECISION_EXECUTE", "1")
    monkeypatch.setattr(nd, "group_info",
                        lambda: {"groups": [{"target_name": "测试群", "verified": True}]})
    r = nd.execute(req["id"], lambda p: {"ok": True, "message_id": "om_mid123"})
    assert r["ok"] and r["decision"]["status"] == "sent"
    with nd._db() as db:
        row = db.execute("SELECT message_id FROM decisions WHERE id=?",
                         (req["id"],)).fetchone()
    assert row["message_id"] == "om_mid123"
    assert not sent  # 上面 execute 用的是内联 lambda，此处确认开关前未发


def test_update_send_evidence(iso):
    req = _record(_announcement())
    assert nd.update_send_evidence(req["id"], "om_x", "receiver_verified")["ok"]
    with nd._db() as db:
        row = db.execute("SELECT evidence,message_id FROM decisions WHERE id=?",
                         (req["id"],)).fetchone()
    assert (row["evidence"], row["message_id"]) == ("receiver_verified", "om_x")
    r = nd.update_send_evidence(req["id"], "om_x", "haha")
    assert not r["ok"] and "非法" in r["error"]      # 非法值拒
    r = nd.update_send_evidence("no-such", "om_x", "api_accepted")
    assert not r["ok"]                               # 行不存在拒
    assert nd.update_send_evidence(req["id"], None, "dead_letter")["ok"]
    with nd._db() as db:  # message_id 传 None 不抹掉已知 mid
        row = db.execute("SELECT message_id FROM decisions WHERE id=?",
                         (req["id"],)).fetchone()
    assert row["message_id"] == "om_x"


# ---------- dead_letter：登记 / 补达独立行 / list 联动 ----------

def test_dead_letter_register_list_redeliver(iso):
    assert dead_letter.list_dead_letters() == []       # 文件缺失 → 空表
    d1 = dead_letter.register("decision:announcement", "openapi", "oc_a", "send fail")
    d2 = dead_letter.register("hub", "openapi", "oc_b", "timeout", decision_id="dec-2")
    assert d1 and d2
    rows = dead_letter.list_dead_letters()
    assert [r["dead_id"] for r in rows] == [d2, d1]    # 倒序
    assert rows[1]["evidence"] == "dead_letter"
    assert rows[1]["redelivered_at"] is None and "redeliveries" not in rows[1]
    # 补达：追加独立行，原行字段不动
    assert dead_letter.mark_redelivered(d1, True, message_id="om_r1", why="人工补达")
    rows = dead_letter.list_dead_letters()
    orig = next(r for r in rows if r["dead_id"] == d1)
    assert orig["redelivered_at"] is None              # 原行不被改写
    assert orig["redeliveries"][0]["action"] == "redelivery"
    assert orig["redeliveries"][0]["redelivery_of"] == d1
    assert orig["redeliveries"][0]["ok"] is True
    assert orig["redeliveries"][0]["message_id"] == "om_r1"
    assert "redeliveries" not in next(r for r in rows if r["dead_id"] == d2)
    # limit 与非法 evidence 醒目归 dead_letter
    assert len(dead_letter.list_dead_letters(limit=1)) == 1
    d3 = dead_letter.register("k", "openapi", "oc_c", "x", evidence="bogus")
    assert next(r for r in dead_letter.list_dead_letters()
                if r["dead_id"] == d3)["evidence"] == "dead_letter"


# ---------- approval_card：卡片构造 + handle_card_action ----------

def _buttons(card):
    out = []
    stack = [card]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("tag") == "button":
                out.append(n)
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)
    return out


def test_build_review_card_buttons_and_truncation(iso):
    card = approval_card.build_review_card({
        "id": "dec-1", "content": "长" * 300, "target_name": "测试群",
        "target_id": "oc_mock", "send_at": "2026-10-19T08:00:00+08:00",
        "reason": "维护", "basis": "计划"})
    btns = _buttons(card)
    assert len(btns) == 2
    values = [b["behaviors"][0]["value"] for b in btns]
    assert {"action_id": "decision_approve", "req_id": "dec-1"} in values
    assert {"action_id": "decision_reject", "req_id": "dec-1"} in values
    texts = [b["text"]["content"] for b in btns]
    assert any(t == "确认（审批留痕）" for t in texts)
    assert any(t == "驳回" for t in texts)
    md = [e["content"] for e in card["body"]["elements"]
          if e.get("tag") == "markdown"]
    body = next(m for m in md if "公告内容" in m)
    assert "…" in body and body.count("长") <= 201  # 截断 200 字


def test_build_review_card_doc_reference():
    # K-ORG-203：决策附带文档引用 → 卡片「参考文档（未核实）」行
    card = approval_card.build_review_card({
        "id": "dec-2", "content": "带引用公告", "target_name": "测试群",
        "target_id": "oc_mock", "send_at": "2026-10-19T08:00:00+08:00",
        "reason": "维护", "basis": "计划",
        "doc_url": "https://example.feishu.cn/docx/AbC123",
        "doc_title": "公告[背景]说明"})
    md = [e["content"] for e in card["body"]["elements"]
          if e.get("tag") == "markdown"]
    body = next(m for m in md if "参考文档" in m)
    assert "参考文档（未核实）" in body
    assert "公告（背景）说明" in body          # 标题内 [] 转全角防 markdown 断链
    assert "https://example.feishu.cn/docx/AbC123" in body
    plain = approval_card.build_review_card({          # 无引用 → 不出现参考文档行
        "id": "dec-3", "content": "x", "target_name": "测试群",
        "target_id": "oc_mock", "send_at": "2026-10-19T08:00:00+08:00",
        "reason": "r", "basis": "b"})
    assert not any("参考文档" in e["content"]
                   for e in plain["body"]["elements"] if e.get("tag") == "markdown")


def test_dispatch_routes_decision_actions(iso, monkeypatch):
    seen = []

    def fake_handle(action_id, req_id, operator_open_id):
        seen.append((action_id, req_id, operator_open_id))
        return True, "captured"

    monkeypatch.setattr(approval_card, "handle_card_action", fake_handle)
    ok, msg = card_actions.dispatch("decision_approve", {"req_id": "d-9"},
                                    who=LAOTIAN)
    assert ok and msg == "captured"
    assert seen == [("decision_approve", "d-9", LAOTIAN)]
    ok, msg = card_actions.dispatch("decision_reject", {"req_id": "d-8"},
                                    who=LAOTIAN)
    assert ok and seen[-1][0] == "decision_reject"
    assert card_actions.lookup("decision_approve")["domain"] == "通知指挥"
    ok, msg = card_actions.dispatch("decision_unknown", {}, who="")
    assert not ok and "未注册" in msg          # 未注册动作不路由


def test_handle_card_action_reject_and_status(iso):
    req = _record(_announcement())
    # 驳回身份配对同样 fail-closed
    ok, msg = approval_card.handle_card_action("decision_reject", req["id"],
                                               "ou_other")
    assert not ok and "阻断" in msg
    ok, msg = approval_card.handle_card_action("decision_reject", req["id"],
                                               LAOTIAN)
    assert ok, msg
    with nd._db() as db:
        row = db.execute("SELECT status,outcome,executed_at FROM decisions "
                         "WHERE id=?", (req["id"],)).fetchone()
    assert row["status"] == "rejected"
    assert row["outcome"] == "rejected_by_laotian" and row["executed_at"]
    # 驳回后不可审批、不可执行（execute 只认 recorded）
    assert not nd.approve_from_card({"id": req["id"]}, LAOTIAN)["ok"]
    assert not nd.execute(req["id"], lambda p: {"ok": True})["ok"]
    # 未知 action_id 交老链
    ok, msg = approval_card.handle_card_action("er:list", "x", LAOTIAN)
    assert not ok and msg == "not mine"


# ---------- push_flow.record evidence 键 ----------

def test_push_flow_record_evidence(iso):
    f = iso / "push_flow.jsonl"
    push_flow.record("k1", "openapi", "oc_a", 1, True, evidence="receiver_verified")
    push_flow.record("k2", "openapi", "oc_a", 1, True)
    push_flow.record("k3", "openapi", "oc_a", 1, False, evidence="bogus")
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["evidence"] == "receiver_verified"
    assert "evidence" not in rows[1]           # None 不落键（行结构向后兼容）
    assert set(rows[1]) == {"ts", "date", "kind", "channel", "target",
                            "count", "ok", "tenant"}
    assert rows[2]["evidence"] == "dead_letter"  # 非法值醒目归 dead_letter
