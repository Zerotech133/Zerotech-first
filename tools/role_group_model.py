# -*- coding: utf-8 -*-
"""研发军团角色分工与分组适配模型校验（2026-10-27 WO-ROLE-GROUP-MODEL）。

读取 org.json（dev.agent_profiles/discipline/role_title/groups）与
agent_platform/config/role_group_model.json，校验：
  1) 每个 dev 席有 discipline/role_title，且 discipline 在 role_families；
  2) 组名/组长与配置一致；组员 discipline 属于该组 disciplines；
  3) 组长职级 E3；
  4) 模型绑定在该角色+档位 allow 清单内；fallback_required 席必须有 fallback 链。

用法：--json / --md / --check（PASS/FAIL，rc 0/1）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
CFG = ROOT / "agent_platform" / "config" / "role_group_model.json"


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _dev(org):
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return d
    return {}


def validate(org, cfg):
    errors, rows = [], []
    dev = _dev(org)
    groups = {g.get("id"): g for g in dev.get("groups") or []}
    cfg_groups = (cfg or {}).get("groups") or {}
    families = (cfg or {}).get("role_families") or {}
    policy = (cfg or {}).get("model_policy") or {}
    if not families:
        errors.append("config.role_families 为空")
    if not policy:
        errors.append("config.model_policy 为空")
    # 组一致性
    for gid, cg in cfg_groups.items():
        og = groups.get(gid)
        if not og:
            errors.append("org 缺组 %s" % gid)
            continue
        if og.get("name") != cg.get("name"):
            errors.append("组 %s 名称不一致：org=%r cfg=%r"
                          % (gid, og.get("name"), cg.get("name")))
        if og.get("lead") != cg.get("lead"):
            errors.append("组 %s 组长不一致：org=%r cfg=%r"
                          % (gid, og.get("lead"), cg.get("lead")))
    # 席位
    profiles = [p for p in dev.get("agent_profiles") or []
                if isinstance(p, dict) and p.get("agent_id")]
    if not profiles:
        errors.append("org.dev.agent_profiles 为空")
    seat_group = {}
    for p in profiles:
        sid = p["agent_id"]
        disc = p.get("discipline") or ""
        title = p.get("role_title") or ""
        if not disc:
            errors.append("%s 缺 discipline" % sid)
            continue
        if not title:
            errors.append("%s 缺 role_title" % sid)
        fam = families.get(disc)
        if not fam:
            errors.append("%s discipline=%r 不在 role_families" % (sid, disc))
            continue
        gid = fam.get("group") or ""
        seat_group[sid] = gid
        grade = p.get("grade") or ""
        if gid:
            og = groups.get(gid) or {}
            if sid in (og.get("members") or []):
                pass
            elif og.get("lead") != sid:
                errors.append("%s 不在 %s 组员列表" % (sid, gid))
            if og.get("lead") == sid and grade != "E3":
                errors.append("%s 是 %s 组长但职级=%r（需 E3）" % (sid, gid, grade))
        pol = (policy.get(disc) or {}).get(grade)
        if not pol:
            errors.append("%s 无 %s/%s 模型策略" % (sid, disc, grade))
            continue
        binding = p.get("llm_binding") or ""
        if binding not in (pol.get("allowed") or []):
            errors.append("%s 绑定 %r 不在 %s/%s allow=%s"
                          % (sid, binding, disc, grade, pol.get("allowed")))
        fb = p.get("llm_fallback") or []
        if pol.get("fallback_required") and not fb:
            errors.append("%s 策略要求 fallback，但 llm_fallback 为空" % sid)
        rows.append({"seat": sid, "role_title": title, "discipline": disc,
                     "family_cn": fam.get("cn"), "group": gid,
                     "grade": grade, "llm": binding,
                     "preferred": pol.get("preferred"),
                     "fallback": fb, "status": "ok"})
    # 组员角色归属是否正确（组员列表不含组长，组长由 lead 字段表意）
    for sid, gid in seat_group.items():
        if not gid:
            continue
        og = groups.get(gid) or {}
        families_map = families
        if sid in (og.get("members") or []):
            if families_map[dev_disc(profiles, sid)].get("group") != gid:
                errors.append("%s 所属组与角色族不一致" % sid)
    return {"errors": errors, "rows": rows,
            "groups": cfg_groups, "families": families,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")}


def dev_disc(profiles, sid):
    for p in profiles:
        if p.get("agent_id") == sid:
            return p.get("discipline")
    return ""


def render_md(rep):
    lines = ["# 研发军团角色分工与分组适配模型", "",
             "| 席位 | 岗位 | 工程域 | 组 | 职级 | 模型绑定 |",
             "|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        lines.append("| %(seat)s | %(role_title)s | %(family_cn)s(%(discipline)s) | "
                     "%(group)s | %(grade)s | `%(llm)s` |" % r)
    lines += ["", "## 组适配", ""]
    for gid, g in (rep["groups"] or {}).items():
        lines.append("- **%s %s**（组长 %s）：角色域 %s"
                     % (gid, g.get("name"), g.get("lead"),
                        "/".join(g.get("disciplines") or [])))
    lines += ["", "## 校验", "",
              "PASS" if not rep["errors"] else "FAIL：" + "；".join(rep["errors"])]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="角色分工/分组适配模型校验")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    org = load(ORG) or {}
    cfg = load(CFG) or {}
    rep = validate(org, cfg)
    if args.check:
        ok = not rep["errors"]
        print(("PASS" if ok else "FAIL") + "：" +
              ("角色分工/分组/模型适配一致" if ok else "；".join(rep["errors"])))
        return 0 if ok else 1
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    print(render_md(rep))
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
