# -*- coding: utf-8 -*-
"""多角色分析摘要（2026-10-30 WO-INV-POOL-ROLES）。

从各席 decision 记忆读取最新一条按票分析（T1/T2/N1/F1），供组合计划按票展示。
只读；不执行、不写记忆。
"""
from __future__ import annotations

import io
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY = os.path.join(REPO, "agent_platform", "data", "memory")
SEATS = ("T1", "T2", "N1", "F1")
LABELS = {"T1": "T1 多头技术", "T2": "T2 空头/风险", "N1": "N1 情绪", "F1": "F1 基本面"}


def normalize_stock(v):
    s = str(v or "").strip().upper().split(".")[0]
    return s.zfill(6) if s.isdigit() else s


def latest_analyses(codes, memory_dir=None, max_chars=420):
    """返回 {code: {seat: output}}；每席每票取最后一条。"""
    wanted = {normalize_stock(c) for c in codes if c}
    out = {c: {} for c in wanted}
    root = memory_dir or MEMORY
    for seat in SEATS:
        p = os.path.join(root, "agent__%s__decision.jsonl" % seat)
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                pay = e.get("payload") or {}
                code = normalize_stock(pay.get("stock"))
                if code not in wanted:
                    continue
                text = str(pay.get("output") or "").strip()
                if text:
                    out[code][seat] = text[:max_chars]
    return out


def render_for_code(code, analyses):
    lines = []
    for seat in SEATS:
        text = (analyses or {}).get(seat)
        if text:
            lines.append("- **%s**：%s" % (LABELS[seat], text.replace("\n", " ")[:420]))
    return "\n".join(lines)
