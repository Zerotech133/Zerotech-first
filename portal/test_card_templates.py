# -*- coding: utf-8 -*-
"""FB-L · card_templates 单测（P1 波次 2026-10-20，离线纯函数）。

纪律：零网络、零 IO、不真发飞书、不启服务；
覆盖注册 kind 包装结构、未注册原样、非 interactive 原样、
坏 content 降级、已是卡片补 header、纯文本包卡、异常降级、
注册表四种 kind 齐全与样式字段齐。
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from card_templates import KIND_CARD_TEMPLATES, apply_card_template  # noqa: E402


# FC-C（P2 2026-10-20）追加 subscription_guide：注册表快照同步扩充
# H-B（P3-lite 2026-10-20）追加 km_card：审计后补注册（蓝「🗂 KM 知识卡」）
# J-B（P3.5-lite 2026-10-20）追加 inbound:feed：投喂回执卡（绿「📥 投喂回执」）
ALL_KINDS = {"alert", "ai_finance_daily", "announcement",
             "watch_strategy", "subscription_guide", "km_card",
             "inbound:feed", "subscription_expiring"}
_COLORS = {"alert": "red", "ai_finance_daily": "blue",
           "announcement": "grey", "watch_strategy": "green",
           "subscription_guide": "grey", "km_card": "blue",
           "inbound:feed": "green", "subscription_expiring": "orange"}
_TITLES = {"alert": "⚠ 预警", "ai_finance_daily": "📰 AI 金融日报",
           "announcement": "📢 管理公告", "watch_strategy": "📈 盯盘策略",
           "subscription_guide": "🔒 订阅服务", "km_card": "🗂 KM 知识卡",
           "inbound:feed": "📥 投喂回执", "subscription_expiring": "⏳ 订阅即将到期"}


# 1. 注册 kind 包装出 header（逐种样式断言）

def test_registered_kind_wraps_header_alert():
    out, mt = apply_card_template("alert", {"text": "CPU 95%"}, "interactive")
    assert mt == "interactive"
    assert out["header"]["template"] == "red"
    assert out["header"]["title"]["content"] == "⚠ 预警"
    assert out["elements"][0]["text"] == {"text": "CPU 95%"}


# 2. 未注册 kind 原样返回（零改动）

def test_unregistered_kind_passthrough():
    content = {"text": "plain"}
    out, mt = apply_card_template("unknown_kind", content, "interactive")
    assert out is content
    assert mt == "interactive"


def test_empty_kind_passthrough():
    content = {"text": "plain"}
    out, _ = apply_card_template("", content, "interactive")
    assert out is content


# 3. msg_type 非 interactive 原样

def test_non_interactive_msg_type_passthrough():
    content = {"text": "plain"}
    for mt in ("text", "post", "image", ""):
        out, got_mt = apply_card_template("alert", content, mt)
        assert out is content
        assert got_mt == mt


# 4. content 坏类型降级原样

def test_bad_content_type_passthrough():
    for bad in (None, 123, 4.5, ["l"], ("t",)):
        out, _ = apply_card_template("alert", bad, "interactive")
        assert out is bad


# 5. content 为 str：JSON 可解析包装，坏 JSON / 非 dict JSON 原样

def test_str_json_content_wrapped():
    out, mt = apply_card_template(
        "watch_strategy", json.dumps({"text": "MA 金叉"}, ensure_ascii=False),
        "interactive")
    assert mt == "interactive"
    assert isinstance(out, str)
    card = json.loads(out)
    assert card["header"]["template"] == "green"
    assert card["elements"][0]["text"]["text"] == "MA 金叉"


def test_str_bad_json_passthrough():
    out, _ = apply_card_template("alert", "{not json", "interactive")
    assert out == "{not json"


def test_str_non_dict_json_passthrough():
    out, _ = apply_card_template("alert", "[1,2]", "interactive")
    assert out == "[1,2]"


# 6. 已是卡片的补 header；已有 header 保留

def test_existing_card_without_header_gets_header():
    card = {"config": {"wide_screen_mode": True},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "hi"}}]}
    snapshot = copy.deepcopy(card)
    out, _ = apply_card_template("announcement", card, "interactive")
    assert out is not card            # 深拷贝，不改入参
    assert card == snapshot           # 原对象零副作用
    assert out["header"]["template"] == "grey"
    assert out["header"]["title"]["content"] == "📢 管理公告"
    assert out["elements"] == card["elements"]


def test_existing_card_with_header_preserved():
    card = {"header": {"template": "purple",
                       "title": {"tag": "plain_text", "content": "自定义"}},
            "elements": [{"tag": "div", "text": {"content": "x"}}]}
    out, _ = apply_card_template("alert", card, "interactive")
    assert out["header"] == card["header"]  # 已有 header 不覆盖
    assert "config" not in out or out.get("config") == card.get("config")


# 7. 纯文本 dict 包卡片：config/header/elements 结构齐

def test_plain_text_dict_wrapped_full_card():
    out, _ = apply_card_template("ai_finance_daily",
                                 {"text": "今日要闻"}, "interactive")
    assert set(("config", "header", "elements")) <= set(out)
    assert out["config"].get("wide_screen_mode") is True
    assert out["header"]["template"] == "blue"
    assert out["elements"][0]["tag"] == "div"
    assert out["elements"][0]["text"] == {"text": "今日要闻"}


# 8. 异常降级：包装函数内部抛错 → 原样返回（closed on failure）

def test_wrap_exception_degrades_to_original(monkeypatch):
    def _boom(kind, content):
        raise RuntimeError("boom")
    monkeypatch.setitem(KIND_CARD_TEMPLATES, "alert", _boom)
    content = {"text": "keep me"}
    out, mt = apply_card_template("alert", content, "interactive")
    assert out is content
    assert mt == "interactive"


# 9. 注册表四种 kind 齐全且样式字段齐

def test_registry_four_kinds_complete():
    assert set(KIND_CARD_TEMPLATES.keys()) == ALL_KINDS
    for kind, fn in KIND_CARD_TEMPLATES.items():
        assert callable(fn)
        card = fn({"text": "x"})
        assert card["header"]["template"] == _COLORS[kind]
        assert card["header"]["title"]["content"] == _TITLES[kind]
        assert card["header"]["title"]["tag"] == "plain_text"


# 10. 注册表对入参零副作用（纯函数纪律）

def test_registry_pure_no_input_mutation():
    src = {"text": "原文", "nested": {"a": 1}}
    snapshot = copy.deepcopy(src)
    for kind in ALL_KINDS:
        KIND_CARD_TEMPLATES[kind](src)
    assert src == snapshot
