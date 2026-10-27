# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 五节点提示词与结构化输出

analysis_type 枚举对齐 ai_team_role DDL：
  market_technical（技术面 T1/T2）→ news_event（N1）→ fundamental（F1）
  → investment_advice（A1）→ final_decision（D1）
结构化输出契约：各节点固定 JSON schema，D1 落 stock_trade_strategy_pool 语义。
"""

from __future__ import annotations

import json
from typing import Any

ROLES: dict[str, dict[str, str]] = {
    # T1/T2 人设（保守/激进）——S2 阶段从 ai_chat_role system_message 提取替换
    "T1": "你是激进多头技术面分析师，擅长从量价结构中发现进攻机会，倾向给出多头论据，但不忽视关键风险。",
    "T2": "你是保守空头技术面分析师，擅长识别顶部结构与资金撤离信号，倾向给出空头论据，但尊重明确的多头证据。",
    "N1": "你是新闻事件与情绪面分析师，擅长判断消息对股价的短期冲击方向与持续性，区分噪音与实质。",
    "F1": "你是基本面分析师，擅长从财务质量、行业景气与估值水平判断公司中长期价值。",
    "A1": "你是投资建议汇总分析师，综合前序所有分析师结论，权衡多空证据强度，给出立场建议。",
    "D1": "你是最终决策者，输出可执行的五档决策（STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL），附置信度、目标价区间与风险等级。",
}

SCHEMAS: dict[str, str] = {
    "market_technical": '{"stance": "BULL|BEAR|NEUTRAL", "confidence": 0~1, "key_points": ["..."], "risks": ["..."]}',
    "news_event": '{"stance": "BULL|BEAR|NEUTRAL", "confidence": 0~1, "key_points": ["..."], "risks": ["..."]}',
    "fundamental": '{"stance": "BULL|BEAR|NEUTRAL", "confidence": 0~1, "key_points": ["..."], "risks": ["..."]}',
    "investment_advice": '{"stance": "BULL|BEAR|NEUTRAL", "confidence": 0~1, "key_points": ["..."], "risks": ["..."]}',
    "final_decision": '{"action": "STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL", "confidence": 0~1, '
    '"target_price_low": 数字, "target_price_high": 数字, "risk_level": "LOW|MEDIUM|HIGH", '
    '"reason": "一句话", "stock": "代码"}',
}


def build_messages(
    node: dict[str, Any],
    stock: str,
    context: str,
    outputs: dict[str, Any],
    opponent_last: str = "",
    round_no: int = 1,
) -> list[dict[str, str]]:
    at = node.get("analysis_type", "final_decision")
    persona = node.get("persona") or ROLES.get(
        node.get("node_key", ""), "你是专业分析师。"
    )
    schema = SCHEMAS.get(at, SCHEMAS["final_decision"])
    system = f"{persona}\n只输出 JSON，不要多余文字。输出 schema：{schema}"
    user_parts = [f"股票：{stock}", f"分析日期：{node.get('asof', '今日')}"]
    if context:
        user_parts.append(f"输入数据：\n{context}")
    deps = node.get("parent_node_keys") or []
    if deps:
        dep_txt = json.dumps(
            {k: outputs.get(k, {}) for k in deps}, ensure_ascii=False, default=str
        )[:2000]
        user_parts.append(f"上游结论：\n{dep_txt}")
    if node.get("is_debate_node") and opponent_last:
        user_parts.append(
            f"【辩论第 {round_no} 轮】你的辩论对手上一轮观点：\n{opponent_last[:800]}\n"
            "请直接回应对方论据：坚持或修正你的立场，给出新论据。"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
