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
    kind: functools.partial(_wrap_built_in, kind) for kind in _BUILTIN_TEMPLATES
}

# ── FC-C 追加注册：订阅引导卡（灰卡，P2 波次 2026-10-20） ─────
# M3 收银台上线前不放任何购买链接，文案只引导"联系开通"。
# 追加注册不改写 FB-L 既有语义：未注册 kind 原样返回的规则不变。
_SUBSCRIPTION_GUIDE_MD = (
    "当前账号未开通订阅，**盯盘 / 预警**等订阅能力暂不可用。\n"
    "如需使用，请**联系开通**（自助购买渠道 M3 上线前请找管理员办理）。"
)


def _wrap_subscription_guide(kind, content):
    """订阅引导卡：固定文案整卡返回，忽略调用方 content（防注入链接）。"""
    del content
    return {
        "config": dict(_DEFAULT_CONFIG),
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": "🔒 订阅服务"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": _SUBSCRIPTION_GUIDE_MD},
            },
        ],
    }


KIND_CARD_TEMPLATES["subscription_guide"] = functools.partial(
    _wrap_subscription_guide, "subscription_guide"
)


# ── H-B 追加注册：KM 知识卡（蓝卡，P3-lite 波次 2026-10-20） ─────
# 审计实测（portal/card_audit.py，docs/plans/2026-10-20_卡片模板覆盖审计）：
# km_card 为 interactive 发送且语义明确（km_feishu_hub.py:252 经
# feishu_card_v2.send_card）；发送侧透传 kind 后生效，未透传/未注册
# kind 原样返回的既有语义不变（closed on failure 不破坏）。
KIND_CARD_TEMPLATES["km_card"] = functools.partial(_wrap_built_in, "km_card")
_BUILTIN_TEMPLATES["km_card"] = ("blue", "🗂 KM 知识卡")


# ── J-B 追加注册：投喂回执卡（绿卡，P3.5-lite 波次 2026-10-20） ─────
# 审计实测（docs/plans/2026-10-20_卡片模板覆盖审计）：inbound:feed 为
# interactive 发送且语义明确（inbound_gateway.py:575-578 经
# feishu_client.send_msg，kind 已透传、critical=True 豁免静默窗）。
# 语义=回执确认，green 对齐 V3_DOMAINS 例行色；包装走 _wrap_built_in
# 判例：已是卡片且有 header 保留原 header，无 header 按 kind 补，
# 纯文本包整卡，elements 不注入任何链接文案。未注册/未透传 kind
# 原样返回的既有语义不变（closed on failure 不破坏）。
KIND_CARD_TEMPLATES["inbound:feed"] = functools.partial(_wrap_built_in, "inbound:feed")
_BUILTIN_TEMPLATES["inbound:feed"] = ("green", "📥 投喂回执")


# ── 订阅到期提醒卡（橙卡，P3.7-lite L-B 2026-10-20） ─────────────────────
# 语义=到期前提醒（提醒，不是告警也不是回执），orange 与 alert 的 red 区分，
# 避免客户把"还有 3 天"误读成"已失权"。文案由 renewal_reminder.py 生成，
# 本模板只负责 header 差异化。
#
# 收编口（2026-10-20 13:48）：L-B 初版另开 KIND_CARD_TEMPLATES_EXTRA 追加表
# 绕开冻结快照断言，导致主表与追加表两处并存。主会话按 FC-C 快照同步扩充
# 判例（0596b37e）把该 kind 并入主表，并同步扩充 ALL_KINDS/_COLORS/_TITLES
# —— 属恒等搬迁 + 快照追加，不改写任何既有断言语义。
KIND_CARD_TEMPLATES["subscription_expiring"] = functools.partial(
    _wrap_built_in, "subscription_expiring"
)
_BUILTIN_TEMPLATES["subscription_expiring"] = ("orange", "⏳ 订阅即将到期")


def _template_for(kind):
    """查 kind 的包装函数：命中主表返回，未注册返回 None。"""
    return KIND_CARD_TEMPLATES.get(kind)


def apply_card_template(kind, content, msg_type):
    """发送前差异化钩子：按 kind 包装飞书 interactive 卡片。

    规则（全部 closed on failure，任何分支异常均降级原文）：
      - msg_type != "interactive" → 原样返回；
      - kind 为空或未注册（主表与追加表均无）→ 原样返回；
      - content 非 dict 且非 str → 原样返回；
      - content 为 str → 尝试 json.loads 解析为 dict，失败原样返回；
      - 已是卡片 dict（含 elements/header）→ 仅确保 header 按 kind；
      - 纯文本 dict → 包成完整卡片 dict；
      - 返回 (content, msg_type)，msg_type 原样透传不改动。
    """
    try:
        if msg_type != "interactive":
            return content, msg_type
        wrap = _template_for(kind) if kind else None
        if wrap is None:
            return content, msg_type
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except (ValueError, TypeError):
                return content, msg_type
            if not isinstance(parsed, dict):
                return content, msg_type
            wrapped = wrap(parsed)
            return json.dumps(wrapped, ensure_ascii=False), msg_type
        if not isinstance(content, (dict,)):
            return content, msg_type
        wrapped = wrap(content)
        return wrapped, msg_type
    except Exception:
        # closed on failure：任何异常降级原文，不阻断主发送。
        return content, msg_type
