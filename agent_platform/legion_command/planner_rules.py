# -*- coding: utf-8 -*-
"""Planner rules from signed experience + A/B comparison.

- accepted.jsonl 中只有 status=accepted 且 signed_by 非空的记录可激活规则；
- 候选经验只能用于“模拟 A/B”，不会写 config/planner_rules.json；
- A/B 不修改生产策略，只产出对比报告。
"""
from __future__ import annotations

import logging
import json
import time
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
EXPERIENCE = REPO / "work" / "experience"
ACCEPTED = EXPERIENCE / "accepted.jsonl"
LATEST = EXPERIENCE / "latest.json"
RULES = REPO / "config" / "planner_rules.json"
AB_DIR = REPO / "work" / "planner" / "ab"

RULE_MAP = {
    "S1": {"require_checks": True},
    "S2": {"unique_write_scope": True},
    "S3": {"planner_retry": 1},
    "S4": {"min_sample": 3},
    "S5": {"require_checks": True},
}


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _accepted_records() -> list:
    out = []
    if not ACCEPTED.exists():
        return out
    for ln in ACCEPTED.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(ln)
        except Exception:
            logging.getLogger(__name__).debug("swallowed exception at line 46")
            continue
        if isinstance(d, dict) and d.get("status") == "accepted" and str(d.get("signed_by") or "").strip():
            out.append(d)
    return out


def _rules_from_suggestions(suggestions: list) -> dict:
    out = {}
    for s in suggestions or []:
        if not isinstance(s, dict):
            continue
        rule = s.get("rule")
        if not isinstance(rule, dict):
            rule = RULE_MAP.get(str(s.get("id") or ""))
        if isinstance(rule, dict):
            out.update(rule)
    return out


def signed_rules() -> dict:
    recs = _accepted_records()
    rules = {}
    signers = []
    for r in recs:
        rules.update(_rules_from_suggestions(r.get("suggestions") or []))
        if r.get("signed_by") not in signers:
            signers.append(r.get("signed_by"))
    return {"enabled": bool(recs), "signed_by": signers, "rules": rules,
            "accepted_count": len(recs)}


def candidate_rules() -> dict:
    d = _load_json(LATEST) or {}
    return {"enabled": False, "source": d.get("candidate_id") or "none",
            "candidate_id": d.get("candidate_id"),
            "rules": _rules_from_suggestions(d.get("suggestions") or [])}


def build_rules() -> dict:
    sr = signed_rules()
    if not sr["enabled"]:
        return {"ok": False, "error": "no_signed_accepted_experience"}
    payload = {"schema": "planner_rules/v1", "enabled": True,
               "signed_by": sr["signed_by"], "accepted_count": sr["accepted_count"],
               "rules": sr["rules"], "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    RULES.parent.mkdir(parents=True, exist_ok=True)
    RULES.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(RULES), **payload}


def active_rules() -> dict:
    d = _load_json(RULES) or {}
    if d.get("enabled") and isinstance(d.get("rules"), dict) and d.get("signed_by"):
        return d
    return {"enabled": False, "rules": {}}


def apply_rules(plan: dict, rules: dict) -> dict:
    out = deepcopy(plan)
    rules = (rules or {}).get("rules") if (rules or {}).get("rules") is not None else (rules or {})
    meta = {"rules": rules, "checks_added": 0, "scope_conflicts": []}
    tasks = out.get("tasks") or []
    if rules.get("require_checks"):
        for t in tasks:
            if not isinstance(t, dict) or t.get("checks"):
                continue
            scopes = t.get("write_scope") or []
            for sc in scopes[:4]:
                if not sc or str(sc).endswith("/"):
                    continue
                t.setdefault("checks", []).append(
                    {"type": "file_exists", "path": str(sc)})
                meta["checks_added"] += 1
    if rules.get("unique_write_scope"):
        for i, a in enumerate(tasks):
            for b in tasks[i + 1:]:
                for sa in a.get("write_scope") or []:
                    for sb in b.get("write_scope") or []:
                        x, y = str(sa).rstrip("/*"), str(sb).rstrip("/*")
                        if x and y and (x == y or x.startswith(y + "/") or y.startswith(x + "/")):
                            meta["scope_conflicts"].append({"a": a.get("id"), "b": b.get("id"),
                                                            "scope_a": sa, "scope_b": sb})
    out["planner_rules"] = meta
    return out


def _ab_metrics(plan: dict, dry: dict) -> dict:
    tasks = plan.get("tasks") or []
    checks = sum(len(t.get("checks") or []) for t in tasks if isinstance(t, dict))
    accept = sum(len(t.get("acceptance") or []) for t in tasks if isinstance(t, dict))
    return {"tasks": len(tasks), "checks": checks, "acceptance": accept,
            "conflicts": len(dry.get("conflicts") or []),
            "order": dry.get("order") or [], "valid": plan.get("valid"),
            "source": plan.get("source")}


def ab_compare(doc: str, name: str = "A/B 对比", use_signed: bool = True) -> dict:
    from legion_command import mission_dispatch as D
    from legion_command import mission_planner as P
    baseline = P.plan(name, doc, use_llm=False, apply_planner_rules=False)
    sr = signed_rules()
    cr = candidate_rules()
    if use_signed and sr["enabled"]:
        rules, source, signed = sr["rules"], "accepted-signed", True
    else:
        rules, source, signed = cr["rules"], "candidate-simulation", False
    candidate = apply_rules(baseline, rules)
    base_dry = D.dry_run(baseline, 3)
    cand_dry = D.dry_run(candidate, 3)
    report = {
        "schema": "planner_ab/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mission_name": name,
        "rules_source": source,
        "rules_signed": signed,
        "rules": rules,
        "baseline": _ab_metrics(baseline, base_dry),
        "candidate": _ab_metrics(candidate, cand_dry),
        "delta": {
            "checks": _ab_metrics(candidate, cand_dry)["checks"] - _ab_metrics(baseline, base_dry)["checks"],
            "conflicts": _ab_metrics(candidate, cand_dry)["conflicts"] - _ab_metrics(baseline, base_dry)["conflicts"],
        },
        "no_production_change": not signed,
        "note": ("已签字经验规则，可申请激活" if signed else
                 "候选经验仅用于模拟 A/B；未签字不激活 production rules"),
    }
    AB_DIR.mkdir(parents=True, exist_ok=True)
    p = AB_DIR / ("ab-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report["report_path"] = str(p)
    return report
