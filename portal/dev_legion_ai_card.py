# -*- coding: utf-8 -*-
"""研发军团作战屏 · AI 席位卡数据（LS-05，2026-10-28）。

只读：从 org.json dev 席位取 AI/架构域席位的模型/档位/评估状态；
v1 不调外部模型、不耗额度；评估状态未接时如实标 "未接"。
"""
from __future__ import annotations

from pathlib import Path

ORG = Path(__file__).resolve().parents[1] / "agent_platform" / "registry" / "org.json"
HR = Path(__file__).resolve().parents[1] / "agent_platform" / "hr"
AI_DISCIPLINES = ("ai", "architecture")


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
    if not isinstance(org, dict):
        return []
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return [p for p in d.get("agent_profiles") or []
                    if isinstance(p, dict) and p.get("agent_id")]
    return []


def build_ai_cards(org):
    cards = []
    for p in _profiles(org):
        disc = p.get("discipline") or ""
        if disc not in AI_DISCIPLINES:
            continue
        sid = p.get("agent_id", "")
        cards.append({
            "id": sid,
            "name": p.get("cn_name") or _hr_name(sid),
            "role_title": p.get("role_title", ""),
            "discipline": disc,
            "grade": p.get("grade", ""),
            "model": p.get("llm_binding", ""),
            "fallback": list(p.get("llm_fallback") or []),
            "report_to": p.get("report_to", ""),
            "eval_status": "未接（v1 仅模型/档位/评估状态占位）",
        })
    return cards
