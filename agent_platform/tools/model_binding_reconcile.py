# -*- coding: utf-8 -*-
"""Phase 0：把 model_policy.yaml 应用到三套注册表（默认 dry-run）。

只改：
- org.json 的 agent_profiles[].llm_binding / llm_fallback / binding_note
- team_dev.json 的 agents[].llm_binding / llm_fallback
- claude_agents.json 的 [role].model（模型短名）

不碰角色、名单、职责、评分；不改 model_tiers。
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml

PLATFORM = Path(__file__).resolve().parents[1]
REGISTRY = PLATFORM / "registry"
POLICY = REGISTRY / "model_policy.yaml"
ORG = REGISTRY / "org.json"
TEAM_DEV = REGISTRY / "team_dev.json"
CLAUDE = REGISTRY / "claude_agents.json"
REPORTS = PLATFORM.parent / "research" / "reports"
STAMP = datetime.now().strftime("%Y%m%dT%H%M%S")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _short(binding: str) -> str:
    return binding.split("/", 1)[1] if "/" in binding and binding.split("/", 1)[0] in {
        "glm", "deepseek", "ollama", "openai", "openai-direct", "nova"
    } else binding


def _norm_model(binding: str) -> str:
    # model_tiers 键是 provider/model（glm/glm-5.3、deepseek/deepseek-flash）；
    # 统一剥掉 provider 前缀再查表。
    return binding.split("/", 1)[1] if "/" in binding else binding


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    roles = policy.get("roles") or {}
    tiers = _read(REGISTRY / "model_tiers.json")
    models = {
        k.split("/", 1)[1] if k.startswith(("glm/", "deepseek/")) else k: v
        for k, v in (tiers.get("models") or {}).items()
    }
    org = _read(ORG)
    team = _read(TEAM_DEV)
    claude = _read(CLAUDE)
    backup_dir = REGISTRY / "backups" / f"model_policy_{STAMP}"
    changes = []
    problems = []

    # org
    by_id = {}
    for dept in org.get("departments") or []:
        for p in dept.get("agent_profiles") or []:
            if p.get("agent_id") in roles:
                by_id[p["agent_id"]] = p
    for aid, pol in roles.items():
        primary, fallback = pol["primary"], pol.get("fallback") or ""
        prof = by_id.get(aid)
        if prof is None:
            problems.append(f"org.json 缺档案: {aid}")
            continue
        old = prof.get("llm_binding")
        grade = str(prof.get("grade") or "")
        tier = (models.get(_norm_model(primary)) or {}).get("tier")
        allow = (tiers.get("grade_policy", {}).get(grade) or {}).get("allow") or []
        if tier is None:
            problems.append(f"{aid}: primary {primary} 不在 model_tiers")
        elif allow and tier not in allow:
            problems.append(f"{aid}: primary tier={tier} 不在 {grade}.allow={allow}")
        changes.append({"file": "org.json", "agent_id": aid, "old": old, "new": primary,
                        "fallback": fallback, "grade": grade})
        if args.apply:
            prof["llm_binding"] = primary
            prof["llm_fallback"] = [fallback, policy["bindings"]["local"]] if fallback else [policy["bindings"]["local"]]
            prof["binding_note"] = f"Phase0 model_policy.yaml {STAMP}"
            prof["binding_source"] = "registry/model_policy.yaml"

    # team_dev
    for a in team.get("agents") or []:
        aid = a.get("agent_id")
        if aid in roles:
            old = a.get("llm_binding")
            changes.append({"file": "team_dev.json", "agent_id": aid, "old": old,
                            "new": roles[aid]["primary"], "fallback": roles[aid].get("fallback", "")})
            if args.apply:
                a["llm_binding"] = roles[aid]["primary"]
                a["llm_fallback"] = [roles[aid]["fallback"], policy["bindings"]["local"]]
                a["binding_source"] = "registry/model_policy.yaml"

    # claude_agents
    for aid, pol in roles.items():
        rec = claude.get(aid)
        if isinstance(rec, dict):
            old = rec.get("model")
            changes.append({"file": "claude_agents.json", "agent_id": aid, "old": old,
                            "new": _short(pol["primary"])})
            if args.apply:
                rec["model"] = _short(pol["primary"])
                rec["llm_binding"] = pol["primary"]
                rec["binding_source"] = "registry/model_policy.yaml"
        else:
            problems.append(f"claude_agents.json 缺角色: {aid}")

    report = {
        "policy": str(POLICY),
        "apply": bool(args.apply),
        "stamp": STAMP,
        "changes": changes,
        "problems": problems,
        "backup_dir": str(backup_dir) if args.apply else None,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"model_binding_reconcile_{STAMP}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if problems:
        print("REFUSE: policy/registry problems", problems)
        return 2
    if not args.apply:
        print(f"[dry-run] wrote {out}; add --apply to write registries")
        return 0
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in (ORG, TEAM_DEV, CLAUDE):
        shutil.copy2(f, backup_dir / f.name)
    _write(ORG, org)
    _write(TEAM_DEV, team)
    _write(CLAUDE, claude)
    print(f"[apply] backups -> {backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
