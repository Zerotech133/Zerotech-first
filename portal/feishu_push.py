# -*- coding: utf-8 -*-
"""飞书推送统一封装 v1（2026-10-25）。

目标：所有从飞书推出去的卡片/消息，统一成同一套"分装标准"：
  标题（场景+emoji） → 一句话结论 → 关键指标(≤4) → 数据时点/来源
  → 主按钮（H5 详情，自动签发 /m 链接） → 次按钮(可选) → 页脚(免责/审计)

规则（与《飞书推送统一封装规范 v1》一致）：
  · 卡片最多 2 个按钮；主按钮必须指向 /m H5（m_links 自动签发）
  · 关键指标 ≤4 条；结论 ≤80 字；标题 ≤18 字
  · 必须带数据时点 ts 与来源 source；缓存/非实时数据标 stale
  · level 决定 header 颜色：info/ok=green, warn=orange, bad=red, news=blue
  · 链接签发失败时**不渲染按钮**（绝不放死链），并记录日志

用法：
  from feishu_push import build, SCENES
  card = build("daily", "统一日报", summary="今日情报 1055 条，中性为主…",
               kpis=[("偏多","17.5%"), ("高置信","63.8%")],
               ts="2026-10-25 16:00", source="推送中台")
  # 发送：feishu_client.send_msg(json.dumps(card), "interactive", ...)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MAX_BUTTONS = 2
MAX_KPIS = 4
MAX_SUMMARY = 80
MAX_TITLE = 18

# 场景注册表：统一命名/emoji/颜色/H5 场景 key
SCENES = {
    "picks": {"name": "今日选股", "emoji": "🎯", "color": "green"},
    "chan": {"name": "缠论详情", "emoji": "📈", "color": "orange"},
    "position": {"name": "持仓", "emoji": "💼", "color": "red"},
    "daily": {"name": "数据日报", "emoji": "📊", "color": "turquoise"},
    "ledger": {"name": "账本", "emoji": "🧾", "color": "purple"},
    "lineage": {"name": "数据血缘", "emoji": "🕸️", "color": "indigo"},
    "ticket": {"name": "工单", "emoji": "🎫", "color": "grey"},
    "notify": {"name": "通知流水", "emoji": "📨", "color": "blue"},
    "subscription": {"name": "订阅", "emoji": "💳", "color": "purple"},
    "ops": {"name": "管理台", "emoji": "🛠️", "color": "grey"},
    "alert": {"name": "预警", "emoji": "🚨", "color": "red"},
    "brief": {"name": "晨报", "emoji": "🌅", "color": "blue",
              "route": "picks"},
    "news": {"name": "新闻速报", "emoji": "⚡", "color": "blue"},
    "push": {"name": "推送中台", "emoji": "📨", "color": "blue"},
    "feed": {"name": "投喂面", "emoji": "📥", "color": "teal"},
}
LEVEL_COLORS = {"info": "green", "ok": "green", "warn": "orange",
                "bad": "red", "news": "blue"}


def _m_url(scene, **kw):
    try:
        import m_links
        return m_links.url_for(scene, **kw) or ""
    except Exception:  # noqa: BLE001
        return ""


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


def _clip_text(text, n):
    """段落级截断：优先在换行处切，其次在中文句末标点切，避免半句。"""
    t = str(text or "").strip()
    if len(t) <= n:
        return t
    head = t[:n]
    cut = head.rfind("\n")
    if cut < n * 0.5:
        import re as _re
        m = None
        for m in _re.finditer(r"[。！？；\n]", head):
            pass
        if m and m.end() > n * 0.5:
            cut = m.end()
    if cut <= 0:
        cut = n
    return t[:cut].rstrip() + "\n\n…（内容较长，完整版见交易台账/选股看板）"


def build(scene, title, summary="", kpis=None, ts="", source="",
          stale=False, level="info", buttons=None, note="", tags="",
          audit="", link_kw=None, items=None, links=None, detail_md=""):
    """统一推送卡片（schema 2.0）。返回 dict；按钮链接失败则省略按钮。"""
    meta = SCENES.get(scene) or {"name": title, "emoji": "📌", "color": "grey"}
    color = LEVEL_COLORS.get(level) or meta.get("color") or "grey"
    head_title = "%s %s · %s" % (meta.get("emoji", "📌"), meta.get("name", title),
                                 _clip(title, MAX_TITLE))
    els = []
    if summary:
        els.append({"tag": "markdown",
                    "content": "**%s**" % _clip(summary, MAX_SUMMARY)})
    items = items or []
    links = links or []
    if detail_md:
        if els:
            els.append({"tag": "hr"})
        els.append({"tag": "markdown",
                    "content": _clip_text(detail_md, 3600)})
    kpis = kpis or []
    if items:
        if els:
            els.append({"tag": "hr"})
        for it in items[:6]:
            els.append({"tag": "markdown",
                        "content": "· " + _clip(it, 180)})
    if kpis:
        if els:
            els.append({"tag": "hr"})
        for label, value in kpis[:MAX_KPIS]:
            els.append({"tag": "markdown",
                        "content": "**%s**　%s" % (label, value)})
    if links:
        if els:
            els.append({"tag": "hr"})
        els.append({"tag": "markdown",
                    "content": " · ".join("[%s](%s)" % (_clip(a, 16), b)
                                          for a, b in links[:3])})
    # 数据时点/来源/缓存声明
    meta_bits = []
    if ts:
        meta_bits.append("时点 %s" % ts)
    if source:
        meta_bits.append("来源 %s" % source)
    if stale:
        meta_bits.append("⚠️ 缓存数据，非实时")
    if note:
        meta_bits.append(str(note))
    if meta_bits:
        if els:
            els.append({"tag": "hr"})
        els.append({"tag": "markdown",
                    "content": "<font color='grey'>%s</font>"
                               % " · ".join(meta_bits)})
    # 按钮：默认主按钮=看 H5 详情；可传自定义 buttons
    acts = []
    if buttons:
        for spec in buttons[:MAX_BUTTONS]:
            if spec.get("type") == "url" and spec.get("url"):
                acts.append({"tag": "button",
                             "text": {"tag": "plain_text",
                                      "content": _clip(spec.get("text", "打开"), 12)},
                             "type": spec.get("style", "default"),
                             "url": spec["url"]})
            elif spec.get("type") == "action" and spec.get("value"):
                acts.append({"tag": "button",
                             "text": {"tag": "plain_text",
                                      "content": _clip(spec.get("text", "操作"), 12)},
                             "type": spec.get("style", "default"),
                             "behaviors": [{"type": "callback",
                                            "value": spec["value"]}]})
    else:
        route = meta.get("route") or scene
        url = _m_url(route, **(link_kw or {})) if route else ""
        if url:
            acts.append({"tag": "button", "type": "primary",
                         "text": {"tag": "plain_text", "content": "看详情"},
                         "url": url})
    if acts:
        els.append({"tag": "hr"})
        els.extend(acts)
    footer = "零界 · %s" % (tags or ("#" + scene))
    if audit:
        footer += " · 审计 %s" % audit
    els.append({"tag": "markdown",
                "content": "<font color='grey'>%s</font>" % footer})
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": head_title},
                   "template": color},
        "body": {"elements": els},
    }


UNIFIED_CFG = os.path.join(HERE, "work", "push_unified.json")


def unified_config():
    """生产者统一开关：{"enabled": true, "shadow": false, "producers": {...}}。"""
    try:
        d = json.load(open(UNIFIED_CFG, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("enabled", True)
    d.setdefault("shadow", False)
    d.setdefault("producers", {})
    return d


def should_unify(name):
    """→ (send_new, shadow)。生产可用 producers.<name> 单独覆盖。"""
    c = unified_config()
    ov = (c.get("producers") or {}).get(name) or {}
    enabled = ov.get("enabled", c.get("enabled", True))
    shadow = ov.get("shadow", c.get("shadow", False))
    return bool(enabled), bool(shadow)


def shadow_log(name, old_card, new_card):
    """双跑记录（只落盘，不发）：两版卡片的标题/按钮数/长度摘要。"""
    try:
        path = os.path.join(HERE, "data", "push_unified_shadow.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        row = {"ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "producer": name,
               "old": {"header": (old_card or {}).get("header", {}).get("title", {}).get("content"),
                       "len": len(json.dumps(old_card, ensure_ascii=False))},
               "new": {"header": (new_card or {}).get("header", {}).get("title", {}).get("content"),
                       "len": len(json.dumps(new_card, ensure_ascii=False))}}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 影子记录失败不影响主链
        pass


def from_sections(name, scene, title, sections, ts="", source="",
                  footer="", buttons=None, links=None, level="info",
                  full=False):
    """旧「分节文本」→ 统一分装：首节首句当结论，其余作要点列表。

    sections: [markdown 文本块]；buttons: [(label, url, style)] 旧格式。
    """
    sections = [str(s) for s in (sections or []) if str(s).strip()]
    summary = ""
    if sections:
        first = sections[0].replace("**", "").strip().split("\n")[0]
        summary = _clip(first, MAX_SUMMARY)
    items = []
    detail_md = ""
    if full:
        # 全文块：保留每节全部行（去 ** 标记），段落级截断，避免只给首行
        parts = []
        for sec in sections:
            clean = sec.replace("**", "").strip()
            if clean:
                parts.append(clean)
        detail_md = "\n\n".join(parts)
    else:
        for sec in sections[1:] if summary else sections:
            line = sec.replace("**", "").strip().split("\n")[0]
            if line and line not in items:
                items.append(_clip(line, 170))
    btns = None
    if buttons:
        btns = [{"type": "url", "text": lab, "url": url,
                 "style": (st or "default")} for lab, url, st in buttons]
    return build(scene, title, summary=summary, ts=ts, source=source,
                 level=level, buttons=btns, tags="#unified #%s" % name,
                 items=items, links=links, note=footer or "",
                 detail_md=detail_md)


# ── 常用分装器（生产者直接调用）────────────────────────────
def brief(summary, kpis=None, ts="", source="晨报引擎", level="info"):
    return build("brief", "盘前晨报", summary=summary, kpis=kpis, ts=ts,
                 source=source, level=level)


def daily(summary, kpis=None, ts="", source="推送中台", stale=False):
    return build("daily", "统一日报", summary=summary, kpis=kpis, ts=ts,
                 source=source, stale=stale)


def news(summary, kpis=None, ts="", source="新闻采集"):
    return build("notify", "新闻命中", summary=summary, kpis=kpis, ts=ts,
                 source=source, level="news")


def dispatch(ticket_id, summary, kpis=None, ts="", source="派单受理"):
    return build("ticket", "派单受理", summary=summary, kpis=kpis, ts=ts,
                 source=source, level="warn",
                 buttons=[{"type": "url", "text": "查看工单",
                           "url": _m_url("ticket", id=ticket_id),
                           "style": "primary"}])


def picks_digest(summary, kpis=None, ts="", source="选股引擎"):
    return build("picks", "选股摘要", summary=summary, kpis=kpis, ts=ts,
                 source=source)


def alert(title, summary, kpis=None, ts="", source="预警引擎"):
    return build("alert", title, summary=summary, kpis=kpis, ts=ts,
                 source=source, level="bad")


if __name__ == "__main__":
    # --demo：输出 4 种分装样例的摘要（不发送）
    samples = {
        "brief": brief("今日 3 只 T1 候选，缠论三买 1 只，注意汇率波动。",
                       [("候选", "3"), ("三买", "1")],
                       ts="2026-10-26 07:00", source="晨报引擎"),
        "daily": daily("今日情报 1055 条，中性 71.5%，高置信 63.8%。",
                       [("偏多", "17.5%"), ("高置信", "63.8%")],
                       ts="2026-10-25 16:00", source="推送中台"),
        "news": news("铜连接订单超预期，板块共振 +2.1%。",
                     [("标的", "华丰科技"), ("涨幅", "+6.8%")],
                     ts="2026-10-25 10:24", source="新闻采集"),
        "dispatch": dispatch("lg-5917d4c4357b",
                             "收到个股咨询派单，已转司马懿席，预计 5 分钟回复。",
                             [("工单", "lg-5917d4c4357b")],
                             ts="2026-10-25 16:04", source="派单受理"),
    }
    for k, card in samples.items():
        btns = [e for e in card["body"]["elements"] if e.get("tag") == "button"]
        print(k, "| header:", card["header"]["title"]["content"],
              "| buttons:", len(btns),
              "| url:", (btns[0].get("url", "")[:60] if btns else "—"))
