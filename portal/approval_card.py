# -*- coding: utf-8 -*-
"""K-ORG-201 W37：管理公告审批卡（老田飞书卡片确认 → 审批留痕）。

链路：8097 /api/decisions/record（announcement）→ 本模块 push_review_card
（via_hub=True，闸门唯一通路）→ 老田卡片按钮（v3 value
{"action_id": "decision_approve"/"decision_reject", "req_id": <决策id>}）
→ feishu_bot_ws → card_actions.dispatch → handle_card_action →
notification_decision.approve_from_card（身份配对 + 行核对 + 留痕）。

红线：卡片「确认」只完成审批留痕（announcement_approvals），决策 status
保持 recorded，真实执行仍由 8097 execute 端点受 PUSH_DECISION_EXECUTE
门控——审批不绕开关。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def build_review_card(decision):
    """decision dict → v3 审批卡（schema 2.0）。公告内容截断 200 字防超长；
    按钮须已在 card_actions.DEFAULT_ACTIONS 注册（decision_approve/reject）。"""
    d = decision or {}
    did = str(d.get("id") or "")
    content = str(d.get("content") or "").replace("\n", " ")
    if len(content) > 200:
        content = content[:200] + "…"
    tid = str(d.get("target_id") or "")
    # K-ORG-203：决策附带的飞书文档引用原样展示（未核实标记随行；仅展示，
    # 不并入发送正文——文档引用不外发）
    lines = [
        "**公告内容**　%s" % (content or "（空）"),
        "**目标**　%s%s" % (d.get("target_name") or "?",
                            ("（%s）" % tid) if tid else ""),
        "**拟发送时间**　%s" % (d.get("send_at") or "?"),
        "**理由**　%s" % (d.get("reason") or "未提供"),
        "**依据**　%s" % (d.get("basis") or "未提供"),
    ]
    if d.get("doc_url"):
        doc_title = str(d.get("doc_title") or "未命名文档")
        lines.append("**参考文档（未核实）**　[%s](%s)" % (
            doc_title.replace("[", "（").replace("]", "）"), d["doc_url"]))
    md = "\n".join(lines)
    els = [
        {"tag": "markdown", "content": "**管理公告 · 待老田确认**"},
        {"tag": "hr"},
        {"tag": "markdown", "content": md},
        {"tag": "markdown", "content":
            "<font color='red'>「确认」仅完成审批留痕，真实执行仍受 "
            "PUSH_DECISION_EXECUTE 开关门控</font>"},
        {"tag": "hr"},
        {"tag": "button", "type": "primary",
         "text": {"tag": "plain_text",
                  "content": "确认（审批留痕）"},
         "behaviors": [{"type": "callback",
                        "value": {"action_id": "decision_approve",
                                  "req_id": did}}]},
        {"tag": "button", "type": "danger",
         "text": {"tag": "plain_text", "content": "驳回"},
         "behaviors": [{"type": "callback",
                        "value": {"action_id": "decision_reject",
                                  "req_id": did}}]},
    ]
    return {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text",
                                 "content": "⚖ 交互 · 管理公告审批"},
                       "template": "violet"},
            "body": {"elements": els}}


def push_review_card(decision):
    """审批卡推老田本人（via_hub=True：静默/配额/按钮注册闸门唯一通路）。
    → (ok, why)。open_id 取 env LAOTIAN_OPEN_ID / push_subscribers.json
    laotian_self（notification_decision._laotian_open_id 同源）。"""
    sys.path.insert(0, os.path.join(ROOT, "news_collector"))
    import feishu_client
    import notification_decision as nd
    receive_id = nd._laotian_open_id()
    if not receive_id:
        return False, "老田 open_id 未配置（LAOTIAN_OPEN_ID / push_subscribers）"
    card = build_review_card(decision)
    # critical=True：审批卡是发给老田本人的动作请求（非群广播），静默窗吞卡
    # 会让审批流 22:00-07:30 瘫痪；走闸门既有 critical 豁免（K-ORG-201 冒烟判例）。
    return feishu_client.send_msg(json.dumps(card, ensure_ascii=False),
                                  "interactive", receive_id,
                                  receive_id_type="open_id",
                                  kind="decision:announcement",
                                  critical=True)


def handle_card_action(action_id, req_id, operator_open_id):
    """卡片按钮处理（card_actions.dispatch v3 分发表注册入口）→ (ok, msg)。
    其他 action_id 返回 not mine 交给老链。"""
    import notification_decision as nd
    if action_id == "decision_approve":
        r = nd.approve_from_card({"id": req_id}, operator_open_id)
        if r.get("ok"):
            return True, "已确认（审批留痕完成；真实执行仍受 execute 开关门控）"
        if r.get("duplicate"):
            return False, "该公告决策已确认过，无需重复操作"
        return False, str(r.get("error") or "确认失败")
    if action_id == "decision_reject":
        return _reject(req_id, operator_open_id)
    return False, "not mine"


def _reject(req_id, operator_open_id):
    """驳回：身份配对同确认口径；status 置 rejected（rejected_at 借
    executed_at 字段存时间戳，outcome=rejected_by_laotian）+ 审计流水。
    被驳回决策不再可执行（execute 只认 recorded）。"""
    import notification_decision as nd
    expected = nd._laotian_open_id()
    got = str(operator_open_id or "").strip()
    if not expected or not got or got != expected:
        return False, "操作者身份与授权名单不匹配，阻断"
    with nd._lock, nd._db() as db:
        row = db.execute("SELECT * FROM decisions WHERE id=?",
                         (req_id,)).fetchone()
        if row is None:
            return False, "决策不存在"
        if row["status"] != "recorded":
            return False, "决策已流转，不可驳回"
        db.execute("UPDATE decisions SET status='rejected', executed_at=?, "
                   "outcome='rejected_by_laotian' WHERE id=?",
                   (datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    req_id))
    try:
        import card_actions
        card_actions.log_event("decision_reject", who=got[:16],
                               card=str(req_id)[:60],
                               result="rejected_by_laotian")
    except Exception:
        pass  # 审计失败不拦驳回主链路
    return True, "已驳回（决策不再执行）"
