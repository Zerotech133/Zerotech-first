# -*- coding: utf-8 -*-
"""FB-L · 卡片差异化模板注册表（8097 发送侧，P1 波次 2026-10-20）。

用途：实现"推不同的信息，看到的点不一样"第一层——按 kind 差异化
飞书 interactive 卡片（config/elements/header 结构），表驱动、零破坏。

纪律（P1 派工单 · 通用纪律）：
  - 纯函数无 IO：不读盘、不发网络、不碰 push_secrets.json / work/；
  - closed on failure：任何异常不抛出，一律降级返回原文，
    绝不阻断主发送链路；
  - 无注册 kind / msg_type 非 interactive / content 不可用 → 原样返回，
    调用方行为与未接入本模块时完全一致（零破坏）。

用法（发送侧钩子，≤10 行）：
    try:
        from card_templates import apply_card_template
        content, msg_type = apply_card_template(kind, content, msg_type)
    except Exception:
        pass  # 原样透传
"""
import copy
import functools
import json

__all__ = ["KIND_CARD_TEMPLATES", "apply_card_template"]


# ── 内置模板：kind → (header template 色, 标题文案) ──────────────

_BUILTIN_TEMPLATES = {
    "alert": ("red", "⚠ 预警"),
    "ai_finance_daily": ("blue", "📰 AI 金融日报"),
    "announcement": ("grey", "📢 管理公告"),
    "watch_strategy": ("green", "📈 盯盘策略"),
}

_DEFAULT_CONFIG = {"wide_screen_mode": True}


def _make_header(kind):
    """按 kind 构造飞书卡片 header（差异化色 + 标题）。"""
    color, title = _BUILTIN_TEMPLATES[kind]
    return {
        "template": color,
        "title": {"tag": "plain_text", "content": title},
    }


def _wrap_text_as_card(kind, text_dict):
    """把纯文本 dict（如 {"text": ...}）包成完整 interactive 卡片。

    原文 dict 原样放入 elements 的 div 文本节点，不增删改其内容。
    """
    return {
        "config": dict(_DEFAULT_CONFIG),
        "header": _make_header(kind),
        "elements": [
            {"tag": "div", "text": copy.deepcopy(text_dict)},
        ],
    }


def _wrap_built_in(kind, content):
    """内置模板包装函数：已是卡片补 header，纯文本包整卡。"""
    if isinstance(content, dict) and ("elements" in content or "header" in content):
        # 已视为卡片：仅确保 header 按 kind 差异化，无 header 则补。
        card = copy.deepcopy(content)
        if "header" not in card:
            card["header"] = _make_header(kind)
        return card
    if isinstance(content, dict):
        return _wrap_text_as_card(kind, content)
    # 非文本型 dict（不应到达此处，防御性原样）
    return content


# ── 注册表：kind → 包装函数（纯函数，无 IO） ─────────────────────

KIND_CARD_TEMPLATES = {
    kind: functools.partial(_wrap_built_in, kind)
    for kind in _BUILTIN_TEMPLATES
}

# ── FC-C 追加注册：订阅引导卡（灰卡，P2 波次 2026-10-20） ─────
# M3 收银台上线前不放任何购买链接，文案只引导"联系开通"。
# 追加注册不改写 FB-L 既有语义：未注册 kind 原样返回的规则不变。
_SUBSCRIPTION_GUIDE_MD = (
    "当前账号未开通订阅，**盯盘 / 预警**等订阅能力暂不可用。\n"
    "如需使用，请**联系开通**（自助购买渠道 M3 上线前请找管理员办理）。")


def _wrap_subscription_guide(kind, content):
    """订阅引导卡：固定文案整卡返回，忽略调用方 content（防注入链接）。"""
    del content
    return {
        "config": dict(_DEFAULT_CONFIG),
        "header": {"template": "grey",
                   "title": {"tag": "plain_text",
                             "content": "🔒 订阅服务"}},
        "elements": [
            {"tag": "div",
             "text": {"tag": "lark_md",
                      "content": _SUBSCRIPTION_GUIDE_MD}},
        ],
    }


KIND_CARD_TEMPLATES["subscription_guide"] = functools.partial(
    _wrap_subscription_guide, "subscription_guide")


def apply_card_template(kind, content, msg_type):
    """发送前差异化钩子：按 kind 包装飞书 interactive 卡片。

    规则（全部 closed on failure，任何分支异常均降级原文）：
      - msg_type != "interactive" → 原样返回；
      - kind 为空或未注册 → 原样返回；
      - content 非 dict 且非 str → 原样返回；
      - content 为 str → 尝试 json.loads 解析为 dict，失败原样返回；
      - 已是卡片 dict（含 elements/header）→ 仅确保 header 按 kind；
      - 纯文本 dict → 包成完整卡片 dict；
      - 返回 (content, msg_type)，msg_type 原样透传不改动。
    """
    try:
        if msg_type != "interactive":
            return content, msg_type
        if not kind or kind not in KIND_CARD_TEMPLATES:
            return content, msg_type
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except (ValueError, TypeError):
                return content, msg_type
            if not isinstance(parsed, dict):
                return content, msg_type
            wrapped = KIND_CARD_TEMPLATES[kind](parsed)
            return json.dumps(wrapped, ensure_ascii=False), msg_type
        if not isinstance(content, (dict,)):
            return content, msg_type
        wrapped = KIND_CARD_TEMPLATES[kind](content)
        return wrapped, msg_type
    except Exception:
        # closed on failure：任何异常降级原文，不阻断主发送。
        return content, msg_type
