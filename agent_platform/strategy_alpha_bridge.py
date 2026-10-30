# -*- coding: utf-8 -*-
"""ST1 策略旁路 → alpha/D1 上下文桥（2026-10-30 WO-INV-CHAIN）。

单向、只读：读取 `agent_platform/data/invest/thesis/ST1_*.md` 最新周记，
渲染为 D1 final_decision 数据段附加文本。无文件/过期则返回空串，不阻塞 alpha。
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THESIS_DIR = REPO / "agent_platform" / "data" / "invest" / "thesis"
MAX_CHARS = 2000
MAX_AGE_DAYS = 7


def latest_thesis(thesis_dir=None, now=None, max_age_days=MAX_AGE_DAYS):
    """返回最新 ST1_*.md 的 (path, text)；无/过期/读失败返回 (None, "")。"""
    d = Path(thesis_dir or THESIS_DIR)
    now = now or datetime.now()
    try:
        files = sorted(d.glob("ST1_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            files = sorted(d.glob("ST1_*.md"), reverse=True)
    except OSError:
        return None, ""
    for p in files:
        try:
            if now - datetime.fromtimestamp(p.stat().st_mtime) > timedelta(days=max_age_days):
                continue
            text = io.open(p, encoding="utf-8", errors="replace").read().strip()
            if text:
                return p, text
        except OSError:
            continue
    return None, ""


def render_strategy_context(thesis_dir=None, now=None, max_chars=MAX_CHARS):
    """渲染为可附加到 D1 数据段的只读文本；无内容返回空串。"""
    p, text = latest_thesis(thesis_dir=thesis_dir, now=now)
    if not text:
        return ""
    return (
        "\n\n[ST1策略旁路｜只读｜draft≠adopt]\n"
        "来源：%s\n%s\n"
        "引用纪律：仅在 D1 l1_basis 中写 `ST1: <文件名>#<小节>` 才视为采用；"
        "与 alpha 证据冲突时以 D1 裁决为准。\n"
        % (p.name, text[:max_chars])
    )
