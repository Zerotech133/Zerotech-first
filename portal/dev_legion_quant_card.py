# -*- coding: utf-8 -*-
"""研发军团作战屏 · 量化席位卡数据（LS-06，2026-10-28）。

只读：dev 席位 discipline=quant（DEV3/DEV4）；v1 只出模型/档位/角色，
策略注册/影子/回测状态未接时如实标 "未接（项目矩阵口径）"，不伪造。
"""
from __future__ import annotations

from pathlib import Path

ORG = Path(__file__).resolve().parents[1] / "agent_platform" / "registry" / "org.json"
HR = Path(__file__).resolve().parents[1] / "agent_platform" / "hr"
QUANT = "quant"


def _hr_name(seat):
    try:
        for line in (HR / seat / "resume.yaml").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.startswith("cn_name:"):
                return line.split(":", 1)[1].strip().strip('"')
    except Exception:  # noqa: BLE001
        pass
    return ""


def _profiles(org):
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return [p for p in d.get("agent_profiles") or []
                    if isinstance(p, dict) and p.get("agent_id")]
    return []


def build_quant_cards(org):
    cards = []
    for p in _profiles(org):
        if (p.get("discipline") or "") != QUANT:
            continue
        sid = p.get("agent_id", "")
        cards.append({
            "id": sid,
            "name": p.get("cn_name") or _hr_name(sid),
            "role_title": p.get("role_title", ""),
            "discipline": QUANT,
            "grade": p.get("grade", ""),
            "model": p.get("llm_binding", ""),
            "report_to": p.get("report_to", ""),
            "strategy_status": "未接（v1 占位）",
            "registry_status": "未接（项目矩阵口径）",
            "backtest_status": "未接（项目矩阵口径）",
        })
    return cards
