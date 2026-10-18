"""AI 晨晚报主编（V1 ai_editor 移植）。

V1 prompt 铁律照搬：只用事实包数字、全中文大白话、给洞察非复述、模拟盘阶段
不给买卖建议。fail-open：无 key/HTTP 异常/空文本 → 返回 None，调用方降级
规则模板（保证报告永远出得来）。

配置（env）：REPORT_LLM_API_KEY / REPORT_LLM_BASE_URL(默认 Anthropic，
可换网关) / REPORT_LLM_MODEL(默认 claude-sonnet-5)。
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是一名私人交易系统的报告主编，替系统主人写每日晨报/晚报。铁律："
    "1)只准使用用户提供的事实数据里的数字，绝不编造或脑补任何数据；"
    "2)全中文、说人话，不要堆术语；"
    "3)给洞察而不是复述：指出反复出现的问题、跨标的的共性、可验证的假设；"
    "4)语气是操盘助手对老板的汇报，简洁直接；"
    "5)当前是模拟盘阶段，不给任何买卖建议，只描述与复盘；"
    "6)若指数数据标注 stale=true，必须写明'截至X日'，严禁当作今日数据。"
)

_MORNING_GUIDE = (
    "写一份 350 字以内的晨报，5 个要点："
    "①市场环境一句话；②今日各策略选出的标的与重点盯的 1-3 只；"
    "③值得注意的信号或异常；④风控拦截或告警摘要（若有）；⑤一句提醒。"
)

_EVENING_GUIDE = (
    "写一份 400 字以内的晚报，5 个要点："
    "①今日成绩单一句话（盈亏/胜率）；②建仓时机共性（追高还是低位）；"
    "③选股最佳与最差说明什么；④错过的信号该不该惋惜；⑤明日关注 1-2 条。"
)


def narrate(facts: dict, report_type: str) -> str | None:
    """AI 叙事；任何失败返回 None（调用方走规则模板）。"""
    api_key = os.getenv("REPORT_LLM_API_KEY", "")
    if not api_key:
        return None
    base = os.getenv("REPORT_LLM_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.getenv("REPORT_LLM_MODEL", "claude-sonnet-5")
    guide = _MORNING_GUIDE if report_type == "morning" else _EVENING_GUIDE
    try:
        import httpx

        resp = httpx.post(
            f"{base}/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=60.0,
            json={
                "model": model,
                "max_tokens": 2000,
                "system": _SYSTEM,
                "messages": [{
                    "role": "user",
                    "content": (f"{guide}\n\n【事实数据】(JSON，只用这里的数字):\n"
                                f"{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}"),
                }],
            },
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - fail-open，报告必须出得来
        logger.warning("ai_editor fallback to rule template: %s", exc)
        return None
