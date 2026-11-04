#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""司马徽研究院 8 席入职校验：台账 ↔ team_academy ↔ org ↔ HR。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "agent_platform" / "registry"
LEDGER = REG / "academy_team_onboard.json"
TEAM = REG / "team_academy.json"
ORG = REG / "org.json"
HR = ROOT / "agent_platform" / "hr"
REQUIRED = ("agent_id", "name", "actual_position", "seat", "department",
            "reports_to", "duties", "llm_binding", "status", "onboarded_at")


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _profiles(org: dict) -> dict:
    out = {}

    def walk(x):
        if isinstance(x, dict):
            if x.get("agent_id"):
                out.setdefault(x["agent_id"], x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(org)
    return out


def _hr_name(aid: str) -> str:
    p = HR / aid / "resume.yaml"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.strip().startswith("cn_name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def validate() -> dict:
    led = _load(LEDGER)
    team = _load(TEAM)
    org = _load(ORG)
    prof = _profiles(org)
    errors, warnings = [], []
    members = led.get("members") or []
    ids = [x.get("agent_id") for x in members]
    team_ids = [x.get("agent_id") for x in team.get("agents") or []]
    if len(members) != 8 or set(ids) != set(team_ids):
        errors.append("台账 8 席必须与 team_academy 一致: %s vs %s" % (ids, team_ids))
    team_map = {x["agent_id"]: x for x in team.get("agents") or [] if x.get("agent_id")}
    for m in members:
        aid = m.get("agent_id")
        for k in REQUIRED:
            if not m.get(k):
                errors.append("%s 缺字段 %s" % (aid, k))
        p = prof.get(aid)
        t = team_map.get(aid)
        if t:
            # 有 org 正式档案时，org 是运行真源；team_academy 仅作团队投影
            if m.get("org_profile") and p and p.get("llm_binding"):
                if m.get("llm_binding") != p.get("llm_binding"):
                    errors.append("%s binding 与 org 不一致: %r != %r" % (
                        aid, m.get("llm_binding"), p.get("llm_binding")))
                if p.get("llm_binding") != t.get("llm_binding"):
                    warnings.append("%s team_academy binding %r 与 org %r 不一致（org 为运行真源）" % (
                        aid, t.get("llm_binding"), p.get("llm_binding")))
            elif m.get("llm_binding") != t.get("llm_binding"):
                errors.append("%s binding 与 team_academy 不一致: %r != %r" % (
                    aid, m.get("llm_binding"), t.get("llm_binding")))
            if m.get("reports_to") != t.get("reports_to"):
                errors.append("%s reports_to 不一致: %r != %r" % (
                    aid, m.get("reports_to"), t.get("reports_to")))
        hn = _hr_name(aid)
        if hn and hn != m.get("name"):
            warnings.append("%s 台账名 %r 与 HR cn_name %r 不一致" % (aid, m.get("name"), hn))
        if m.get("org_profile"):
            if not p:
                errors.append("%s 声明 org_profile=true 但 org 无档案" % aid)
                continue
            for f in ("actual_position", "reports_to", "name"):
                if p.get(f) != m.get(f):
                    errors.append("%s org.%s 不一致: %r != %r" % (
                        aid, f, p.get(f), m.get(f)))
        else:
            if p:
                errors.append("%s 声明 team_only 但 org 出现档案" % aid)
    # AI1 双汇报
    ai1 = next((x for x in members if x.get("agent_id") == "AI1"), {})
    if ai1.get("command_reports_to") != "A0":
        errors.append("AI1 指挥汇报必须为 A0")
    return {"schema": "hq/academy_team_onboard_report/v1", "ok": not errors,
            "members": len(members), "team_ids": len(team_ids),
            "errors": errors, "warnings": warnings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="司马徽团队入职校验")
    ap.add_argument("--json", action="store_true")
    ap.parse_args(argv)
    r = validate()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
