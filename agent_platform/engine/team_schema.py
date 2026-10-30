# -*- coding: utf-8 -*-
"""菜鸟团队分析结构化输出移植（2026-10-30 WO-INV-CAINIAO-PORT）。

来源：`TeamTaskServiceImpl` / `StructuredOutputSchema` / `StructuredOutputParser`
（thunder-model-admin，Java+Hutool，已提取到 reference，见回执）。
本模块是 Python 等价实现：按 analysis_type 注入 JSON Schema 指令、解析
Markdown+JSON 混合输出、分离正文；不引入 Java/网络依赖。
"""
from __future__ import annotations

import json
import re

# 每类节点的结构化输出字段（描述来自菜鸟源码，按我们现有 analysis_type 命名对齐）
SCHEMAS = {
    "market_technical": {
        "overallTrend": "string // bullish / bearish / sideways",
        "selectedIndicators": "string[] // 选用的技术指标",
        "keySignals": "string[] // 关键技术信号，最多5条",
        "supportLevel": "string // 支撑位",
        "resistanceLevel": "string // 阻力位",
        "confidence": "string // high / medium / low",
        "summaryTable": "string // Markdown 总结表",
    },
    "news_event": {
        "overallImpact": "string // positive / neutral / negative",
        "keyEvents": "string[] // 关键事件，最多5条",
        "macroFactors": "string[] // 宏观因素，最多3条",
        "confidence": "string // high / medium / low",
        "summaryTable": "string // Markdown 总结表",
    },
    "fundamental": {
        "overallAssessment": "string // positive / neutral / negative",
        "keyStrengths": "string[] // 核心优势，最多5条",
        "keyRisks": "string[] // 核心风险，最多5条",
        "financialHealth": "string // 健康/一般/需关注",
        "confidence": "string // high / medium / low",
        "summaryTable": "string // Markdown 总结表",
    },
    "sentiment": {
        "overallBand": "string // Bullish/Mildly Bullish/Neutral/Mixed/Mildly Bearish/Bearish",
        "overallScore": "number // 0-10",
        "confidence": "string // high / medium / low",
        "narrative": "string // 完整情绪分析",
    },
    "bull_research": {
        "position": "string // bullish",
        "keyArguments": "string[] // 核心多头论点，最多5条",
        "counterArguments": "string[] // 反驳空头的论点，最多5条",
        "conclusionVerdict": "string // 坚持/部分认可/完全认可",
        "newEvidence": "string // 本轮新证据，无则填'无'",
    },
    "bear_research": {
        "position": "string // bearish",
        "keyArguments": "string[] // 核心空头论点，最多5条",
        "counterArguments": "string[] // 反驳多头的论点，最多5条",
        "conclusionVerdict": "string // 坚持/部分认可/完全认可",
        "newEvidence": "string // 本轮新证据，无则填'无'",
    },
}
# final_decision 保持既有严格四字段契约，不注入菜鸟 Schema（避免破坏 parse_decision）
FINAL_TYPES = ("final_decision",)


def build_instruction(analysis_type):
    """生成注入 user prompt 的 JSON 输出格式要求；未知/final 返回空串。"""
    schema = SCHEMAS.get(str(analysis_type or ""))
    if not schema:
        return ""
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "\n\n---\n## 输出格式要求\n"
        "先输出完整的 Markdown 分析报告正文；然后在报告末尾追加一段 JSON，"
        "用 ```json ... ``` 包裹。JSON 字段如下（注释仅说明，实际输出不要带注释）：\n"
        "```json\n" + schema_json + "\n```\n"
        "要求：所有字段必须填写；字符串双引号、数字不加引号、数组用 []。"
    )


_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.S | re.I)


def _strip_line_comments(text):
    out = []
    in_str = False
    esc = False
    i = 0
    while i < len(text):
        c = text[i]
        if esc:
            out.append(c)
            esc = False
            i += 1
            continue
        if c == "\\" and in_str:
            out.append(c)
            esc = True
            i += 1
            continue
        if c == '"':
            in_str = not in_str
            out.append(c)
            i += 1
            continue
        if not in_str and c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            if i < len(text):
                out.append("\n")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_structured(full_content):
    """从 Markdown+JSON 混合内容提取结构化 dict；失败返回 {'error': ...}，不抛异常。"""
    if not full_content or not str(full_content).strip():
        return {"error": "empty_content"}
    text = str(full_content)
    try:
        m = _JSON_BLOCK.search(text)
        if m:
            return json.loads(_strip_line_comments(m.group(1).strip()))
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return json.loads(_strip_line_comments(stripped))
        return {"error": "no_json_block_found"}
    except Exception as exc:  # noqa: BLE001
        return {"error": "parse_failed", "message": str(exc)[:200]}


def extract_markdown(full_content):
    """去掉 JSON 代码块，返回报告正文（用于展示/记忆）。"""
    if not full_content:
        return ""
    text = str(full_content)
    m = _JSON_BLOCK.search(text)
    if m:
        before = text[:m.start()].strip()
        return re.sub(r"\s*---\s*$", "", before).strip()
    return text


def result_summary(decision):
    """把 D1 决策归一为结果契约（五档/置信/风险/目标/周期）；缺失不臆造。"""
    d = decision if isinstance(decision, dict) else {}
    conf = d.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = None
    if conf is not None and conf > 1:
        conf = conf / 100.0
    return {
        "recommendation": d.get("recommendation"),
        "confidence": conf,
        "risk_level": d.get("risk_level"),
        "target_price": d.get("target_price"),
        "time_horizon": d.get("time_horizon"),
        "rationale": d.get("rationale"),
        "l1_basis": d.get("l1_basis"),
    }
