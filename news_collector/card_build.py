# -*- coding: utf-8 -*-
"""飞书 interactive 卡片构造与推送（信息采集域共用）。

schema 2.0，与量化回测域 runner.build_card 同款按钮/标题结构（已验证可点）。
用法:
    card = build_card(title, subtitle, template, [md块], footer=..., buttons=[(label, url, type)])
    push_card(card)   # 失败返回 False，调用方降级纯文本
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _url_buttons(buttons):
    """v1 标准按钮组（tag=action + url）：兼容模式卡片下手机端可点。

    坑: 兼容模式（无 schema 2.0）卡片混用 2.0 的 column_set+behaviors
        按钮时，桌面端容错渲染、手机端点击行为失效 —— 必须用 v1 action。"""
    return {"tag": "action",
            "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": label},
                 "type": "primary" if "primary" in btype else btype,
                 "url": url}
                for label, url, btype in buttons]}


def build_card(title, subtitle, template, md_blocks, footer=None,
               buttons=None, use_schema2=False):
    """use_schema2=True 返回真 Card 2.0（schema 键 + body.elements 包装）。

    坑（K42-d 实测手机端）：顶层 elements + 2.0 markdown 组件的无 schema
    混血卡——桌面端容错渲染、手机端 elements 整体不渲染（只见彩色标题
    正文空白）；真 2.0（schema+body 包装）两端均正常。2.0 下禁用 img 组件
    （200621 拒发），带二维码的卡勿用 v2；v1 tag=action 按钮在 2.0 下
    实测拒发（K-STOCK-354 日报卡实证），按钮卡勿用 v2。"""
    els = []
    for i, md in enumerate(md_blocks):
        if i:
            els.append({"tag": "hr"})
        els.append({"tag": "markdown", "content": md})
    if buttons:
        els.append({"tag": "hr"})
        els.append(_url_buttons(buttons))
    if footer:
        els.append({"tag": "hr"})
        els.append({"tag": "markdown", "text_size": "notation",
                    "content": "<font color='grey'>" + footer + "</font>"})
    # 坑: 带 "schema": "2.0" 的卡片加 img 组件会被飞书拒（200621），
    #     去掉 schema 键走兼容模式即可发送（实测 S/C 系列验证卡）
    # v1 标准结构：elements 必须在顶层。
    # 坑: 用 2.0 的 body 包装但不带 schema 字段时，服务端容错渲染内容、
    #     但按钮点击行为失效（手机/桌面均不可点）——实测定位。
    card = {
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": template,
        },
        "elements": els,
    }
    if use_schema2:
        card["schema"] = "2.0"
        card["body"] = {"elements": els}
        del card["elements"]
    return card


def _qr_key(portal_url):
    """复用量化域 card_assets 的二维码上传/缓存（lark-cli images create）。"""
    try:
        sys.path.insert(0, os.path.abspath(
            os.path.join(HERE, "..", "quant_backtest")))
        from card_assets import ensure_qr_image_key
        return ensure_qr_image_key(portal_url)
    except Exception:
        # 豁免（C2批2）：docstring 声明——二维码上传失败自动省略（可选组件降级）
        return None


def push_card(card, qr_portal=None):
    """写 outbox/pending_card.json → send_feishu.py interactive 模式。
    qr_portal 有值时自动附门户二维码（上传失败自动省略）。"""
    key = _qr_key(qr_portal) if qr_portal else None
    if key:
        # 坑1: 裸 img 在 body 下被飞书拒绝（200621），须包 column_set
        # 坑2: column_set 不能作为 body.elements 末元素（实测 200621），
        #       其后必须再跟一个元素（hr + 灰色小字）
        card["elements"].append({"tag": "hr"})
        card["elements"].append({
            "tag": "column_set", "flex_mode": "none", "columns": [
                {"tag": "column", "width": "weighted", "weight": 1,
                 "elements": [
                     {"tag": "img", "img_key": key,
                      "alt": {"tag": "plain_text",
                              "content": "门户控制台二维码"},
                      "custom_width": 200, "custom_height": 200},
                     {"tag": "markdown", "text_size": "notation",
                      "content": "<font color='grey'>扫码打开门户控制台"
                                 "</font>"}]}]})
        card["elements"].append({
            "tag": "markdown", "text_size": "notation",
            "content": "<font color='grey'>长按识别二维码 · 手机直达"
                       "</font>"})
    tmp = os.path.join(HERE, "outbox", "pending_card.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False)
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "send_feishu.py"), tmp,
         "interactive"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    return (r.stdout or "").strip().startswith("SEND_OK")
