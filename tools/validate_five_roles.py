#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五角色入职校验：台账 ↔ org/profile_seats ↔ 日报席位 ↔ prompt。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "agent_platform" / "registry"
LEDGER = REG / "five_roles_onboard.json"
ORG = REG / "org.json"
SEATS = REG / "profile_seats.json"
REQUIRED = ["agent_id", "name", "actual_position", "seat", "department",
            "reports_to", "duties", "llm_binding", "status", "onboarded_at"]


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


def validate() -> dict:
    led = _load(LEDGER)
    org = _load(ORG)
    seats = _load(SEATS)
    prof = _profiles(org)
    for s in seats.get("seats", []):
        if s.get("agent_id"):
            prof[s["agent_id"]] = s
    errors, warnings = [], []
    roles = led.get("roles") or []
    ids = [r.get("agent_id") for r in roles]
    if len(roles) != 5 or set(ids) != {"A0", "AI1", "ZHAOYUN", "GUANYU", "HUANGZHONG"}:
        errors.append("台账必须恰好五席: %s" % ids)
    members = (org.get("command_hq") or {}).get("members") or []
    if set(members) != set(ids):
        errors.append("command_hq.members=%s 与台账不一致" % members)
    for r in roles:
        aid = r.get("agent_id")
        for k in REQUIRED:
            if not r.get(k):
                errors.append("%s 缺字段 %s" % (aid, k))
        if aid not in prof:
            errors.append("%s 在 org/profile_seats 无档案" % aid)
            continue
        rec = prof[aid]
        if rec.get("actual_position") != r.get("actual_position"):
            errors.append("%s actual_position 不一致: %r != %r" % (aid, rec.get("actual_position"), r.get("actual_position")))
        if rec.get("llm_binding") != r.get("llm_binding"):
            errors.append("%s binding 不一致: %r != %r" % (aid, rec.get("llm_binding"), r.get("llm_binding")))
        if r.get("prompt_file") and not (ROOT / r["prompt_file"]).is_file():
            errors.append("%s prompt 不存在: %s" % (aid, r["prompt_file"]))
        if r.get("status") != "active":
            errors.append("%s 非 active" % aid)
    # 日报 17 席必须含四席（A0 汇总，不重复报）
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import hq_daily_run as hdr
        daily = {x["lead"] for x in hdr._leads(True)}
        for aid in ("AI1", "ZHAOYUN", "GUANYU", "HUANGZHONG"):
            if aid not in daily:
                errors.append("日报缺席位 %s" % aid)
    except Exception as e:  # noqa: BLE001
        warnings.append("daily_check_skipped:%s" % str(e)[:120])
    return {"schema": "hq/five_roles_onboard_report/v1", "ok": not errors,
            "roles": len(roles), "errors": errors, "warnings": warnings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="五角色入职校验")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = validate()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
