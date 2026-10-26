# -*- coding: utf-8 -*-
"""zhuge_challenge_model.py — P2 真模型质询生成（可选、默认不启用）。

设计纪律：
- **不进入离线主链**：`tools/zhuge_challenge.py` 默认 `--model-mode off`，不 import 本模块；
  仅当显式 `--model-mode real3` 时才惰性导入并调用。
- 载体：OpenClaw agent-config（当前 DeepSeek 中转，`engine/openclaw_cli.run`），
  与 L5/8796 同一载体；失败/空回返回 (None, error)，由主工具降级规则模板并 warning。
- 只读快照→生成问题文本；不写沙盘仓、不接 Kanban、不派单。
- 每日试点预算：最多 3 个问题/卡（`max_questions` 上限 3）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "agent_platform"
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))

from engine.openclaw_cli import run as oc_run  # noqa: E402

MODEL_AGENT = "zhuge"
MAX_MODEL_QUESTIONS = 3
_LEAD_RE = re.compile(r"^\s*(?:\d+[\).、]|[-*•])\s*")


def build_prompt(items, max_questions=MAX_MODEL_QUESTIONS):
    """把快照任务标题拼成单行 prompt（与 OpenClaw CLI 单行 prompt 纪律一致）。"""
    top = list(items or [])[:max_questions]
    parts = [
        "你是诸葛亮（反向军师）。下面是田丰当日执行计划快照中的任务。",
        "请为这些任务生成 %d 个质询问题（每任务 1 个，优先目标偏离/风险/预算）。" % max_questions,
        "输出格式：每行一个，以 1) 2) 3) 开头；只输出问题本身，不解释、不寒暄、不编号以外的前缀。",
        "任务清单：",
    ]
    for it in top:
        parts.append("- %s：%s" % (it.get("task_id") or "?", it.get("title") or it.get("desc") or "?"))
    return " ".join(parts)


def generate_questions(items, max_questions=MAX_MODEL_QUESTIONS, timeout=120,
                       agent=MODEL_AGENT):
    """调 OpenClaw 载体生成质询问题。

    → (questions|None, info)。问题为纯文本列表（≤max_questions），info 记录 mode/agent/
    requested/returned/error/raw 摘要；任何失败都不抛，交给主工具降级。
    """
    cap = max(1, min(int(max_questions or MAX_MODEL_QUESTIONS), MAX_MODEL_QUESTIONS))
    if not items:
        return None, {"mode": "real3", "error": "empty_items"}
    prompt = build_prompt(items, cap)
    try:
        text, err = oc_run(agent, prompt, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 —— 失败必须降级，不反噬主链
        return None, {"mode": "real3", "agent": agent,
                      "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    if not text:
        return None, {"mode": "real3", "agent": agent, "error": err or "empty_output"}
    questions = []
    seen = set()
    for line in str(text).splitlines():
        s = _LEAD_RE.sub("", line).strip()
        if len(s) < 4:  # 过短行（编号残留/空）丢弃；正常问题≥4 字
            continue
        if s in seen:
            continue
        seen.add(s)
        questions.append(s[:300])
        if len(questions) >= cap:
            break
    if not questions:
        return None, {"mode": "real3", "agent": agent,
                      "error": "no_questions_parsed", "raw": str(text)[:200]}
    return questions, {"mode": "real3", "agent": agent, "requested": cap,
                       "returned": len(questions)}
