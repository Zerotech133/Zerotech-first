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

# 卡片按钮的 URL 在飞书客户端（手机/外部设备）打开 → 必须用跨设备可达地址。
# K-STOCK-140 判例：100.110.192.72:8080 随 052 收敛失效（nginx 仅绑回环）。
# K-STOCK-305 判例：Tailscale 链（100.110.192.72:8734）对手机是死路——手机从未入
# Tailscale 网，按钮必死；公网鉴权站（WorkBuddy 托管 picks_public 静态快照）
# 2026-10-13 外部网络实测 /picks.html 全页可达。注意：/kg/stock/<code> 个股页
# 被腾讯云 WAF 拦（403），不可用于卡片按钮。8734 仍是本机/在网设备唯一入口（K-STOCK-113）。
# 2026-10-18：项目 E 盘→C 盘搬迁后按目录重建了发布沙箱，域名换新
# （老 quant-picks-board.app.workbuddy.host 为孤儿沙箱、数据陈旧且本账号
# 无发布记录无法下线，已决策弃用；老域名勿再写入任何卡片/链接）。
PORTAL_BASE = "https://quant-picks-board-74446.app.workbuddy.host"  # 公网鉴权站（访问码门禁）


def _load_portal_key():
    """K-STOCK-408：访问码（发码侧）唯一事实源 = portal/work/portal_key.json
    （gitignored，轮换只改该文件即全链生效；daily_report/news_digest/
    kg_brief/morning_report/push_picks_* 等 7+ 消费方均经本模块取码）。
    优先级：env PORTAL_KEY（测试/应急覆盖）> 配置文件 > 空串。
    空串时卡片按钮带 ?key= 打到 401 手输页——fail-safe，不裸奔旧码。
    校验侧事实源 = portal/server_gate.py（sync_publish 映射为
    picks_public/server.py），两源一致性由 test_portal_key_rotation.py 守卫。
    """
    env_k = os.environ.get("PORTAL_KEY")
    if env_k:
        return env_k
    try:
        with open(os.path.join(HERE, "work", "portal_key.json"),
                  encoding="utf-8") as f:
            k = (json.load(f).get("key") or "").strip()
            if k:
                return k
    except Exception:
        pass  # 文件缺失/坏 JSON：空码 fail-safe（守卫测试有专项告警断言）
    return ""


PORTAL_KEY = _load_portal_key()  # 访问码（泄露应急：改配置+deploy 即作废）
# K-STOCK-414：本值仅供外部一次性脚本 import；长跑进程（WS bot 等）发卡
# 走 _load_portal_key() 现读，保证轮换 portal_key.json 即时生效、无需重启。


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
                       "default_url": "%s/picks.html?key=%s" % (
                           PORTAL_BASE, _load_portal_key())}]})  # K-STOCK-414 现读
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


def send_card(card, receive_id=None, receive_id_type="open_id", kind=None):
    """发送 interactive 卡片：K-STOCK-228 起委托 feishu_client 统一底座。
    P3.5-lite：可选 kind 透传（非 None 时传给 feishu_client.send_msg，
    走 8097 hub 按 kind 套卡片模板；缺省 None 零变化，向后兼容）。
    """
    if receive_id is None:
        receive_id = USER_ID
    from feishu_client import send_msg  # NEWS 已挂 path（K-STOCK-228 出口收敛）
    return send_msg(json.dumps(card, ensure_ascii=False), "interactive",
                    receive_id, receive_id_type, kind=kind)


# ── K-PUSH-004：卡片 v3 域色模板（统一构件规范）──────────────
# 构件：品牌 header（域色+来源）+ 标题行 + 键值区 + 动作区（仅注册按钮）
#       + footer（#tags · 来源 · 时间 · act 审计号）
# 域色规范（原型 tpl 分区拍板）：蓝媒（资讯）/ 紫交互（审批·双向）/
# 绿例行（日报）/ 金回执（凭证）/ 红预警（critical，不受静默限制）。
V3_DOMAINS = {
    "news":     ("blue",   "📰", "资讯"),
    "interact": ("violet", "⚖", "交互"),
    "daily":    ("green",  "🌅", "例行"),
    "receipt":  ("yellow", "🧾", "回执"),
    "alert":    ("red",    "🚨", "预警"),
}


def v3_audit_id(title):
    import hashlib
    import time
    return hashlib.md5(("%s|%s" % (title, time.time())).encode(
        "utf-8", "replace")).hexdigest()[:4]


def build_v3(domain, title, source, rows=None, md=None, buttons=None,
             tags="", audit=None, subtitle=None, extra_els=None):
    """v3 统一卡 → (card, audit_id)。

    rows:       [(label, value_md), ...] 键值区（value 支持 markdown 着色）
    md:         正文 markdown（与 rows 可同用，rows 在前）
    buttons:    [(label, spec, style)] —— spec 二选一：
                ("action", action_id, payload_dict)  回调按钮（须已注册，否则拒造）
                ("url", url)                          URL 跳转按钮
                style: primary / default / danger
    tags:       footer 左侧 #标签（如 "#tune #pending"）
    audit:      审计号（None 自动生成 4 位，进 footer 右侧）
    subtitle:   header 副标题（K-PUSH-008：news/km 迁移保副标题信息）
    extra_els:  追加 2.0 元素列表（md 之后、buttons 之前；collapsible_panel 等）
    """
    import datetime
    if domain not in V3_DOMAINS:
        raise ValueError("未知域：%s（可用 %s）" % (domain, "/".join(V3_DOMAINS)))
    tpl, emoji, dname = V3_DOMAINS[domain]
    audit = audit or v3_audit_id(title)
    now = datetime.datetime.now()

    els = [{"tag": "markdown", "content": "**%s**" % title}]
    if rows:
        els.append({"tag": "hr"})
        for label, val in rows:
            els.append({"tag": "markdown",
                        "content": "**%s**　%s" % (label, val)})
    if md:
        els.append({"tag": "hr"})
        els.append({"tag": "markdown", "content": md})
    if extra_els:
        els.append({"tag": "hr"})
        els.extend(extra_els)

    if buttons:
        els.append({"tag": "hr"})
        import card_actions
        for label, spec, style in buttons:
            if spec[0] == "action":
                _, aid, payload = spec
                info = card_actions.lookup(aid)
                if not info:
                    raise ValueError("按钮动作未注册：%s（先到按钮归档登记）" % aid)
                if not info["enabled"]:
                    raise ValueError("按钮动作已禁用：%s" % aid)
                val = {"action_id": aid}
                val.update(payload or {})
                els.append({"tag": "button", "type": style or "primary",
                            "text": {"tag": "plain_text", "content": label},
                            "behaviors": [{"type": "callback", "value": val}]})
            elif spec[0] == "url":
                els.append({"tag": "button", "type": style or "default",
                            "text": {"tag": "plain_text", "content": label},
                            "behaviors": [{"type": "open_url",
                                           "default_url": spec[1]}]})
            else:
                raise ValueError("未知按钮 spec：%r" % (spec[0],))

    els.append({"tag": "markdown", "content":
                "<font color='grey'>%s · %s · %s · act:%s</font>" % (
                    (tags or ("#%s" % domain)), source,
                    now.strftime("%m-%d %H:%M"), audit)})
    card = {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text",
                                 "content": "%s %s · %s" % (emoji, dname, title)},
                       "template": tpl},
            "body": {"elements": els}}
    if subtitle:
        card["header"]["subtitle"] = {"tag": "plain_text",
                                      "content": subtitle}
    return card, audit
