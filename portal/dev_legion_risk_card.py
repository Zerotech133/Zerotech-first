# -*- coding: utf-8 -*-
"""研发军团作战屏 · 回测/风控卡数据（LS-07，2026-10-28）。

只读：DEV4（回测/风控工程师）；v1 真实 Go-No-Go / 判据冻结状态未接时如实
标 "未接（项目矩阵口径）"，不伪造结论。
"""
from __future__ import annotations

from pathlib import Path

ORG = Path(__file__).resolve().parents[1] / "agent_platform" / "registry" / "org.json"
HR = Path(__file__).resolve().parents[1] / "agent_platform" / "hr"
RISK_SEATS = ("DEV4",)


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


def build_risk_cards(org):
    cards = []
    for p in _profiles(org):
        sid = p.get("agent_id", "")
        if sid not in RISK_SEATS:
            continue
        cards.append({
            "id": sid,
            "name": p.get("cn_name") or _hr_name(sid),
            "role_title": p.get("role_title", ""),
            "grade": p.get("grade", ""),
            "model": p.get("llm_binding", ""),
            "report_to": p.get("report_to", ""),
            "go_no_go_status": "未接（v1 占位）",
            "criterion_freeze_status": "未接（项目矩阵口径）",
        })
    return cards
