# -*- coding: utf-8 -*-
"""诸葛亮 L5 注入护栏：高置信度规则命中才阻断；其余放行并留痕。

设计：
- 只做确定性规则，不调模型、不写业务；
- 高置信度覆盖：忽略既有指令、索要 system prompt、角色覆盖、越权执行；
- 疑似但不达阈值 → `risk` 记录，shadow 放行，供 tracing 观察；
- 失败 fail-open：护栏异常不阻断正常咨询（但会记录）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class GuardResult:
    blocked: bool = False
    risk: str = "none"
    hits: list[str] = field(default_factory=list)
    note: str = ""


_BLOCK_PATTERNS = [
    ("ignore_instructions", re.compile(
        r"(忽略|无视|忘记|忘掉).{0,8}(之前|以上|前面|所有|全部).{0,8}(指令|指示|规则|要求|系统提示)",
        re.IGNORECASE)),
    ("ignore_en", re.compile(
        r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)",
        re.IGNORECASE)),
    ("system_prompt", re.compile(
        r"(system\s*prompt|系统提示词|系统指令|开发者指令|developer\s*message).{0,12}(是什么|显示|输出|告诉我|reveal|print|show)",
        re.IGNORECASE)),
    ("system_prompt_before", re.compile(
        r"(输出|打印|显示|告诉我|reveal|print|show).{0,12}(system\\s*prompt|系统提示词|系统指令|developer\\s*message)",
        re.IGNORECASE)),
    ("role_override", re.compile(
        r"(从现在开始|现在起).{0,8}(你是|扮演|假装).{0,20}(管理员|root|系统|开发者|另一个模型)",
        re.IGNORECASE)),
    ("exfiltrate_reverse", re.compile(
        r"(api[\\s_-]?key|密钥|口令|密码|token|secret).{0,12}(输出|打印|泄露|给我|reveal|print|show)",
        re.IGNORECASE)),
    ("exfiltrate", re.compile(
        r"(输出|打印|泄露|给我).{0,8}(api[_\s-]?key|密钥|口令|密码|token|secret)",
        re.IGNORECASE)),
]

_SUSPECT_PATTERNS = [
    ("jailbreak", re.compile(r"(越狱|jailbreak|DAN模式|developer mode)", re.IGNORECASE)),
    ("tool_abuse", re.compile(r"(执行|运行|调用).{0,8}(rm\s+-rf|shutdown|format\s+[a-z]:)", re.IGNORECASE)),
]


def scan(text: str) -> GuardResult:
    q = str(text or "").strip()
    if not q:
        return GuardResult(note="empty")
    hits = []
    for name, pat in _BLOCK_PATTERNS:
        if pat.search(q):
            hits.append(name)
    if hits:
        return GuardResult(True, "high", hits, "规则命中：已阻断，不调用模型")
    suspects = [name for name, pat in _SUSPECT_PATTERNS if pat.search(q)]
    if suspects:
        return GuardResult(False, "medium", suspects, "疑似风险：shadow 放行并留痕")
    return GuardResult(False, "none", [], "")
