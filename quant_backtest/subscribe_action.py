# -*- coding: utf-8 -*-
"""K250 · 飞书卡「订阅这个买点」动作处理（A 方案核心）。

职责：把飞书卡的 callback 事件 → 落订阅表 → 组装确认卡。

设计原则（与 K249 一脉相承）：
  - 纯逻辑，不碰 HTTP/门户——门户只负责解 event 然后调这里；
  - 幂等：同 code+kind 的订阅重复点不重复插入，只回报「已在订阅中」；
  - 任何异常返回 (ok, msg, card)，绝不抛——回调链路不能因单点失败断掉。

数据契约（订阅条目，兼容 subscribe.py 既有字段）：
  {
    "id": "sub_buypoint_{code6}",
    "name": "{名称} 买点提醒",
    "kind": "buy_point",              # 新 kind，须在 subscribe.SUPPORTED 里登记
    "when": ["intraday", "eod"],
    "params": {"code": "sz000993", "trigger_px": 10.52,
               "trigger_kind": "pullback_hold"},
    "receivers": ["个人"],
    "enabled": true,
    "note": "飞书卡一键订阅（K250）；价触 10.52 不破提醒"
  }
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SUB_PATH = os.path.join(HERE, "subscriptions.json")
RECV_PATH = os.path.join(HERE, "receivers.json")
NEWS_DIR = os.path.normpath(os.path.join(HERE, "..", "news_collector"))

# 按 kind 归类，缺省「买点提醒」
KIND_LABEL = {
    "buy_point": "买点提醒",
    "sell_point": "卖点提醒",
    "stock_report": "分析师报告",
}


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _norm_code(code):
    """统一成 sz000993 / sh603268 形态（门户/订阅引擎口径）。
    2026-10-16 章程 §4.3 收编：判定委托 codes.normalize_code 唯一权威
    （4 开头北交所等边缘判定随之修复），此处只做 TS→小写前缀形态转换。"""
    from codes import normalize_code
    ts = normalize_code(code)
    if ts is None:
        return str(code or "").strip().lower().replace(".", "")
    six, suf = ts.split(".")
    return suf.lower() + six


def _code6(code):
    c = _norm_code(code)
    return c[2:] if len(c) == 8 else c


def stock_name(code6):
    """取股票名。优先 news_config.json 的 watchlist（门户权威口径），
    再退订阅表/清单；取不到返回 None——不阻断订阅流程。"""
    # 1) news_config.json watchlist（权威）
    cfg = _read_json(os.path.join(NEWS_DIR, "news_config.json"), {})
    for it in (cfg.get("watchlist") or []):
        if not isinstance(it, dict):
            continue
        c = str(it.get("code") or "").lower().replace(".", "")
        if len(c) == 8 and c[:2] in ("sz", "sh", "bj"):
            c = c[2:]
        if c == code6 and it.get("name"):
            return it["name"]
    # 2) 本地清单兜底
    for fn in ("watchlist.json", "pool.json", "stocks.json"):
        d = _read_json(os.path.join(HERE, fn), None)
        if not isinstance(d, dict):
            continue
        for key in ("watchlist", "stocks", "pool", "items"):
            for it in (d.get(key) or []):
                if not isinstance(it, dict):
                    continue
                cd = str(it.get("code") or it.get("ts_code") or "")
                if _code6(cd) == code6 and it.get("name"):
                    return it["name"]
    return None


# ------------------------------------------------------------------ 主入口

def subscribe_from_card(value, operator=None):
    """飞书卡 callback 的 value → 落订阅 → 返回 (ok, msg, card)。

    value 结构（按钮里写死）：
      {"act": "subscribe_buypoint", "code": "sz000993",
       "px": 10.52, "kind": "buy_point", "label": "回踩不破 10.52"}
    operator：点击者的 open_id（飞书事件里带）——用于把订阅指向点击者本人；
              取不到则回落到 receivers.json 第一个「个人」。
    """
    try:
        v = value or {}
        act = v.get("act") or "subscribe_buypoint"
        code = _norm_code(v.get("code"))
        if not code:
            return False, "缺少股票代码", None
        kind = v.get("kind") or "buy_point"
        px = v.get("px")
        label = v.get("label") or ""

        if act in ("unsubscribe", "unsub"):
            return _unsubscribe(code, kind, operator)

        name = stock_name(_code6(code)) or _code6(code)
        sid = "sub_%s_%s" % ("bp" if kind == "buy_point" else kind[:4],
                             _code6(code))

        # 接收人：优先点击者本人，否则「个人」
        recv = _pick_receiver(operator)

        d = _read_json(SUB_PATH, {"subscriptions": []})
        subs = d.get("subscriptions") or []
        for s in subs:
            if s.get("id") == sid:
                if s.get("enabled"):
                    card = _confirm_card(
                        name, code, px, label, kind, already=True)
                    return True, "已在订阅中", card
                s["enabled"] = True
                s["params"] = _params(code, px, kind, label)
                s["receivers"] = [recv]
                _atomic(SUB_PATH, {"subscriptions": subs})
                card = _confirm_card(name, code, px, label, kind, already=False)
                return True, "已恢复订阅", card

        subs.append({
            "id": sid,
            "name": "%s %s" % (name, KIND_LABEL.get(kind, "提醒")),
            "kind": kind,
            "when": ["intraday", "eod"],
            "params": _params(code, px, kind, label),
            "receivers": [recv],
            "enabled": True,
            "note": "飞书卡一键订阅（K250）%s" % (
                "；触发位 %.2f %s" % (px, label) if px else ""),
        })
        _atomic(SUB_PATH, {"subscriptions": subs})
        card = _confirm_card(name, code, px, label, kind, already=False)
        return True, "订阅成功", card
    except Exception as ex:
        return False, "订阅异常：%s" % str(ex)[:120], None


def _params(code, px, kind, label):
    p = {"code": code}
    if px:
        try:
            p["trigger_px"] = round(float(px), 2)
        except (TypeError, ValueError):
            pass
    p["trigger_kind"] = ("pullback_hold" if kind == "buy_point"
                         else "break_down" if kind == "sell_point"
                         else "eod")
    if label:
        p["trigger_label"] = label
    return p


def _pick_receiver(operator):
    """优先点击者本人（按 open_id 在 receivers.json 里找名字），否则第一个。"""
    d = _read_json(RECV_PATH, {"receivers": []})
    rs = d.get("receivers") or []
    if operator:
        for r in rs:
            if r.get("id") == operator and r.get("name"):
                return r["name"]
    for r in rs:
        if r.get("type") == "open_id" and r.get("name"):
            return r["name"]
    return "个人"


def _unsubscribe(code, kind, operator):
    d = _read_json(SUB_PATH, {"subscriptions": []})
    subs = d.get("subscriptions") or []
    sid = "sub_%s_%s" % ("bp" if kind == "buy_point" else kind[:4],
                         _code6(code))
    hit = False
    for s in subs:
        if s.get("id") == sid:
            s["enabled"] = False
            hit = True
    if not hit:
        return False, "未找到该订阅", None
    _atomic(SUB_PATH, {"subscriptions": subs})
    name = stock_name(_code6(code)) or _code6(code)
    return True, "已取消订阅", _confirm_card(
        name, code, None, "", kind, already=False, cancelled=True)


def _confirm_card(name, code, px, label, kind, already=False, cancelled=False):
    """回一条确认卡（飞书卡 v1 兼容结构，与报告卡同风格）。"""
    kl = KIND_LABEL.get(kind, "提醒")
    if cancelled:
        head, tpl, line = "已取消订阅", "grey", (
            "已关闭「%s %s」。之后不会再收到该标的的提醒。" % (name, kl))
    elif already:
        head, tpl, line = "已在订阅中", "grey", (
            "「%s %s」此前已订阅，无需重复操作。" % (name, kl))
    else:
        head, tpl, line = "订阅成功", "green", None
    if line is None:
        if px:
            line = ("已订阅「%s %s」——当价格触及 **%.2f**（%s）时提醒你。"
                    % (name, kl, px, label or "触发位"))
        else:
            line = "已订阅「%s %s」。" % (name, kl)
    els = [
        {"tag": "markdown", "content":
         "%s\n\n标的：**%s**（%s）\n触发条件：%s"
         % (line, name, code,
            ("价格触及 %.2f · %s" % (px, label)) if px else "盘后更新")},
        {"tag": "hr"},
        {"tag": "markdown", "text_size": "notation", "content":
         "<font color='grey'>青萍之末订阅 · 提醒仅为纸面研究辅助，"
         "不构成投资建议 · 可在门户 /bt/subscribe 管理</font>"},
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": tpl,
            "title": {"tag": "plain_text", "content": "📌 " + head},
        },
        "elements": els,
    }


# ---------------------------------------------------- 飞书卡动作按钮组装

def subscribe_button(code, px=None, label=None, kind="buy_point",
                     text=None):
    """生成一张卡内的「订阅」按钮元素（v1 兼容结构，与报告卡同款）。

    注意：旧版卡片是「兼容模式」，button 需走 v1 结构（tag=button + value），
    不能用 2.0 的 behaviors——这是 runner.py:166 记的坑。
    """
    val = {"act": "subscribe_buypoint", "code": _norm_code(code),
           "kind": kind}
    if px is not None:
        val["px"] = round(float(px), 2)
    if label:
        val["label"] = label
    if not text:
        text = ("📌 订阅这个买点" if kind == "buy_point"
                else "📌 订阅这个卖点" if kind == "sell_point"
                else "📌 订阅提醒")
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "primary",
        "value": val,
    }


if __name__ == "__main__":
    # 冒烟：本地模拟一次订阅
    ok, msg, card = subscribe_from_card(
        {"act": "subscribe_buypoint", "code": "sz000993", "px": 10.52,
         "kind": "buy_point", "label": "回踩不破"})
    print("ok=%s msg=%s" % (ok, msg))
    print(json.dumps(card, ensure_ascii=False, indent=1)[:600])
    print("--- 幂等复测 ---")
    ok, msg, card = subscribe_from_card(
        {"act": "subscribe_buypoint", "code": "sz000993", "px": 10.52,
         "kind": "buy_point", "label": "回踩不破"})
    print("ok=%s msg=%s" % (ok, msg))
