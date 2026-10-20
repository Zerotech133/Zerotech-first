# -*- coding: utf-8 -*-
"""J-B · inbound:feed 注册单测（P3.5-lite 波次 2026-10-20，离线纯函数）。

纪律：零网络、零 IO（审计报告/网关源码只读为静态断言所需）、
不真发飞书、不启服务；覆盖：
  - 注册存在与 header 齐全（绿「📥 投喂回执」）；
  - 包装行为：纯文本包整卡 / 已是卡片保留原 header / str JSON 解析包装；
  - 未注册 kind 原样回归（closed on failure 语义不变）；
  - inbound_gateway.py:578 kind="inbound:feed" 静态断言（零改动锚点）；
  - test_card_templates.py FC-C 快照同步；
  - 审计报告"处置落实"小节存在。
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import card_templates  # noqa: E402
import test_card_templates  # noqa: E402
from card_templates import KIND_CARD_TEMPLATES, apply_card_template  # noqa: E402

ROOT = os.path.dirname(HERE)
GATEWAY_PATH = os.path.join(ROOT, "portal", "inbound_gateway.py")
AUDIT_PATH = os.path.join(ROOT, "docs", "plans",
                          "2026-10-20_卡片模板覆盖审计_2026-10-20.md")

KIND = "inbound:feed"
COLOR = "green"
TITLE = "📥 投喂回执"


# 1. 注册存在与 header 齐全（绿「📥 投喂回执」）

def test_registered_and_header_complete():
    assert KIND in KIND_CARD_TEMPLATES
    assert callable(KIND_CARD_TEMPLATES[KIND])
    card = KIND_CARD_TEMPLATES[KIND]({"text": "x"})
    assert card["header"]["template"] == COLOR
    assert card["header"]["title"]["content"] == TITLE
    assert card["header"]["title"]["tag"] == "plain_text"
    assert card["config"].get("wide_screen_mode") is True


# 2. 纯文本 dict 经 apply_card_template 包整卡

def test_plain_text_wrapped_full_card():
    out, mt = apply_card_template(KIND, {"text": "已受理"}, "interactive")
    assert mt == "interactive"
    assert set(("config", "header", "elements")) <= set(out)
    assert out["header"]["template"] == COLOR
    assert out["header"]["title"]["content"] == TITLE
    assert out["elements"][0]["text"] == {"text": "已受理"}


# 3. 已是卡片且带 header：保留原 header（build_v3 回执卡判例）

def test_existing_card_header_preserved():
    card = {"header": {"template": "purple",
                       "title": {"tag": "plain_text", "content": "入向受理"}},
            "elements": [{"tag": "div", "text": {"content": "x"}}]}
    snapshot = copy.deepcopy(card)
    out, _ = apply_card_template(KIND, card, "interactive")
    assert out is not card            # 深拷贝，不改入参
    assert card == snapshot           # 原对象零副作用
    assert out["header"] == card["header"]  # 已有 header 不覆盖


# 4. str JSON content 解析后包装，msg_type 原样透传

def test_str_json_content_wrapped():
    out, mt = apply_card_template(
        KIND, json.dumps({"text": "回执"}, ensure_ascii=False), "interactive")
    assert mt == "interactive"
    assert isinstance(out, str)
    card = json.loads(out)
    assert card["header"]["template"] == COLOR
    assert card["elements"][0]["text"]["text"] == "回执"


# 5. 未注册 kind 原样回归（closed on failure 语义不变）

def test_unregistered_kind_passthrough():
    content = {"text": "plain"}
    out, mt = apply_card_template("unknown_kind", content, "interactive")
    assert out is content
    assert mt == "interactive"


# 6. inbound_gateway.py:578 kind="inbound:feed" 静态断言（本轮零改动锚点）

def test_gateway_send_msg_kind_literal():
    with open(GATEWAY_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    hits = [i + 1 for i, ln in enumerate(lines)
            if 'kind="inbound:feed"' in ln]
    assert hits == [578]  # 有且仅有一处，行号锚点不漂移
    call = "".join(lines[573:578])  # :574-578 send_msg 调用体
    assert "feishu_client.send_msg" in call
    assert '"interactive"' in call
    assert "critical=True" in call
    assert "receive_id_type=\"open_id\"" in call


# 7. test_card_templates.py FC-C 快照同步（三快照均含 inbound:feed）

def test_snapshot_sync_in_test_card_templates():
    assert KIND in test_card_templates.ALL_KINDS
    assert test_card_templates._COLORS[KIND] == COLOR
    assert test_card_templates._TITLES[KIND] == TITLE
    # 快照与注册表一致（既有 test_registry_four_kinds_complete 语义不改写）
    assert set(KIND_CARD_TEMPLATES.keys()) == test_card_templates.ALL_KINDS


# 8. 审计报告"处置落实"小节存在且四项处置齐

def test_audit_report_disposal_section():
    with open(AUDIT_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    assert "处置落实" in text
    section = text.split("处置落实", 1)[1]
    assert "inbound:feed" in section and "已注册" in section
    assert "osint:cookie_expired" in section and "维持不注册" in section
    assert "osint:dead_letter" in section
    assert "km_card" in section and "J-A" in section
    assert "daily:alert" in section and "daily:report" in section
    assert "挂账" in section and "下波" in section
