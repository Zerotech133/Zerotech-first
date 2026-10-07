# -*- coding: utf-8 -*-
"""飞书卡片 2.0 通用多层折叠卡 builder（下钻形态 L0）。

首屏=判定+关键位（三行看完），细节按主题分折叠层，尾部按钮深链门户。
实测坑位（已规避）：collapsible_panel 不支持 background_style；
schema V2 不支持 note 组件（灰字用 markdown font 标签）。
用法:
  from feishu_card_v2 import build_drill_card
  card = build_drill_card(code, facts, sections=[("📈 缠论中枢与操作方案", md1), ...])
  ok = send_card(card)
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(os.path.dirname(HERE), "news_collector")
sys.path.insert(0, NEWS)
from send_feishu import _load_app_creds, USER_ID  # noqa: E402

PORTAL_BASE = "http://127.0.0.1:8080"  # 内网门户（与 nginx 一致）


def _fmt_quote(facts):
    q = facts.get("quote") or {}
    if not q.get("px"):
        return "行情源暂不可用"
    pct = q.get("pct")
    color = "red" if (pct or 0) >= 0 else "green"
    s = "现价 **%s**（<font color='%s'>%+.2f%%</font>）" % (
        q.get("px"), color, pct or 0)
    if q.get("amount"):
        s += " · 成交 %.2f 亿" % (q["amount"] / 1e8)
    return s


def _verdict_line(facts):
    nb = facts.get("no_buy_reasons") or {}
    v = nb.get("verdict") or "无决策链"
    if "不买" in v:
        return "**系统判定：%s**（趋势闸门/三灯详见「应对」层）" % v
    return "**系统判定：%s**" % v


def build_drill_card(code, facts, sections, title_extra="",
                     trade_basis="2026-09-30 收盘（国庆休市）"):
    """sections: [(layer_title, md_content), ...] → 卡片 2.0 JSON dict。"""
    name = facts.get("name") or code
    cl = facts.get("chanlun") or {}
    key_line = "行情加载中" if not facts.get("quote") else _fmt_quote(facts)
    if cl.get("dzg"):
        key_line += "\n**关键位** 中枢 %s~%s ｜ 止损 %s ｜ 操作 %s" % (
            cl.get("dzd"), cl.get("dzg"), cl.get("stop"),
            (cl.get("plan") or "")[:40])
    elements = [
        {"tag": "markdown", "content":
            "%s\n%s" % (_verdict_line(facts), key_line)},
        {"tag": "hr"},
    ]
    for t, md in sections:
        elements.append({
            "tag": "collapsible_panel", "expanded": False,
            "header": {"title": {"tag": "plain_text", "content": t}},
            "border": {"color": "grey"}, "vertical_spacing": "8px",
            "padding": "8px 12px",
            "elements": [{"tag": "markdown", "content": md}]})
    elements.append({"tag": "hr"})
    # schema V2 不支持 action 容器 → button 直接放 elements，URL 走 behaviors
    elements.append({
        "tag": "button", "type": "primary", "size": "medium",
        "text": {"tag": "plain_text", "content": "门户详情页"},
        "behaviors": [{"type": "open_url",
                       "default_url": "%s/picks.html" % PORTAL_BASE}]})
    elements.append({
        "tag": "markdown", "content":
            "<font color='grey'>老田的AI工作站 · 缠论引擎K78+K246 · "
            "数据基准 %s</font>" % trade_basis})
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "%s %s %s" % (code, name, title_extra)},
            "template": "orange"},
        "body": {"elements": elements}}


def send_card(card, receive_id=None, receive_id_type="open_id"):
    """OpenAPI 直发 interactive（与 send_feishu 同凭证同 API）。"""
    if receive_id is None:
        receive_id = USER_ID
    app_id, app_secret = _load_app_creds()
    if not app_id:
        return False, "no app creds"
    try:
        body = json.dumps({"app_id": app_id,
                           "app_secret": app_secret}).encode("utf-8")
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/"
            "tenant_access_token/internal",
            data=body, headers={"Content-Type": "application/json"})
        tok = json.loads(urllib.request.urlopen(req, timeout=15).read()
                         ).get("tenant_access_token")
        body2 = json.dumps({
            "receive_id": receive_id, "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)
        }).encode("utf-8")
        req2 = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages"
            "?receive_id_type=" + receive_id_type,
            data=body2, headers={"Content-Type": "application/json",
                                 "Authorization": "Bearer " + tok})
        resp = json.loads(urllib.request.urlopen(req2, timeout=15).read())
        return resp.get("code") == 0, ("code=%s %s" % (
            resp.get("code"), str(resp.get("msg"))[:120]))
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:150]
