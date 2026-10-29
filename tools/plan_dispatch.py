# -*- coding: utf-8 -*-
"""研发计划拆解 → 工单/派工（半自动，2026-10-29 WO-PLAN-DISPATCH）。

补"曹操拿研发计划→拆解→分到每个人"缺口：输入 WBS JSON（要素/owner/验收/依赖/工作量），
校验后生成 devtask new + tianfeng_dispatch 命令；`--apply` 才真正执行（默认 dry-run）。

计划 schema=plan_dispatch/v1：
{
 "project": "war-screen-v2",
 "elements": [
   {"id":"E1","title":"...","owner":"DEV1","discipline":"backend",
    "acceptance":"...","effort":"S","deps":[]}
 ]
}
用法：
  python -X utf8 tools/plan_dispatch.py --plan plan.json --dry-run
  python -X utf8 tools/plan_dispatch.py --plan plan.json --apply
  python -X utf8 tools/plan_dispatch.py --plan plan.json --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "plan_dispatch/v1"
DEV_LIMIT = 12


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _profiles(root):
    org = _load(Path(root) / "agent_platform" / "registry" / "org.json", {})
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return {p.get("agent_id"): p for p in d.get("agent_profiles") or []
                    if isinstance(p, dict) and p.get("agent_id")}
    return {}


def _cycle(ids, deps):
    graph = {i: [d for d in (deps.get(i) or [])] for i in ids}
    state = {}

    def dfs(n):
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        for d in graph.get(n, []):
            if d in graph and dfs(d):
                return True
        state[n] = 2
        return False

    return any(dfs(n) for n in ids)


def validate(plan, root=None):
    root = Path(root or ROOT)
    errors, warnings = [], []
    if not isinstance(plan, dict):
        return ["计划不是对象"], warnings
    if plan.get("schema") not in (SCHEMA, None):
        errors.append("schema 应为 %s" % SCHEMA)
    project = str(plan.get("project") or "").strip()
    if not project:
        errors.append("缺 project")
    elements = plan.get("elements")
    if not isinstance(elements, list) or not elements:
        return errors + ["elements 不能为空"], warnings
    profs = _profiles(root)
    ids = []
    deps = {}
    for i, e in enumerate(elements):
        if not isinstance(e, dict):
            errors.append("elements[%d] 不是对象" % i)
            continue
        eid = str(e.get("id") or "").strip()
        if not eid:
            errors.append("elements[%d] 缺 id" % i)
            continue
        if eid in ids:
            errors.append("id 重复：%s" % eid)
        ids.append(eid)
        owner = str(e.get("owner") or "").strip()
        if not owner or owner not in profs:
            errors.append("%s owner 非法/不在 dev 席位：%r" % (eid, owner))
        else:
            disc = str(e.get("discipline") or "").strip()
            if disc and disc != (profs[owner].get("discipline") or ""):
                errors.append("%s discipline=%r 与 owner %s 域 %r 不匹配"
                              % (eid, disc, owner, profs[owner].get("discipline")))
        if not str(e.get("acceptance") or "").strip():
            errors.append("%s 缺 acceptance（验收命令）" % eid)
        deps[eid] = [str(d) for d in (e.get("deps") or [])]
    for eid, ds in deps.items():
        for d in ds:
            if d not in ids:
                errors.append("%s 依赖不存在：%s" % (eid, d))
    if _cycle(ids, deps):
        errors.append("依赖存在环")
    tasks = _load(root / ".worktrees" / "tasks.json", {})
    inflight = sum(1 for t in (tasks or {}).values()
                   if isinstance(t, dict) and (t.get("dept") or "").lower() == "dev"
                   and t.get("status") in ("doing", "review"))
    if inflight + len(elements) > DEV_LIMIT:
        warnings.append("容量告警：在制 %d + 新增 %d > %d"
                        % (inflight, len(elements), DEV_LIMIT))
    return errors, warnings


def _code(project):
    s = re.sub(r"[^A-Za-z0-9]+", "-", project).strip("-").upper()
    return s or "PLAN"


def commands(plan, root=None, due=""):
    root = Path(root or ROOT)
    code = _code(str(plan.get("project") or "plan"))
    out = []
    for e in plan.get("elements") or []:
        eid = str(e.get("id") or "").strip()
        owner = str(e.get("owner") or "").strip()
        tid = str(e.get("tid") or ("WO-%s-%s" % (code, eid))).strip()
        title = str(e.get("title") or eid).strip()
        acc = str(e.get("acceptance") or "").strip()
        effort = str(e.get("effort") or "S").strip().upper()
        out.append({
            "tid": tid,
            "owner": owner,
            "devtask": [sys.executable, "-X", "utf8", "tools/devtask.py", "new",
                        tid, title, "--dept", "dev", "--approver", "ARCH1",
                        "--acceptance", acc, "--effort", effort],
            "dispatch": [sys.executable, "-X", "utf8", "tools/tianfeng_dispatch.py",
                         "--to", "研发部/" + owner, "--task", tid + " " + title,
                         "--acceptance", acc]
                        + (["--due", due] if due else [])
                        + ["--status", "dispatched",
                           "--note", "plan=%s; owner=%s; effort=%s"
                                     % (plan.get("project"), owner, effort)],
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--due", default="")
    ap.add_argument("--root")
    args = ap.parse_args(argv)
    root = Path(args.root or ROOT)
    plan = _load(args.plan, {})
    errors, warnings = validate(plan, root)
    cmds = commands(plan, root, args.due) if not errors else []
    result = {"ok": not errors, "errors": errors, "warnings": warnings,
              "commands": [{"tid": c["tid"], "owner": c["owner"],
                            "devtask": " ".join(c["devtask"]),
                            "dispatch": " ".join(c["dispatch"])} for c in cmds]}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        for e in errors:
            print("ERR", e)
        for w in warnings:
            print("WARN", w)
        if not errors:
            print("PLAN_OK elements=%d" % len(cmds))
            for c in cmds:
                print("  ticket=%s owner=%s" % (c["tid"], c["owner"]))
                print("    " + " ".join(c["devtask"]))
                print("    " + " ".join(c["dispatch"]))
    if args.apply and not errors:
        if not (Path(root) / ".worktrees").is_dir() and root == ROOT:
            print("ERR 非仓库根，拒绝 apply")
            return 1
        for c in cmds:
            r = subprocess.run(c["devtask"], cwd=str(root), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            print("devtask rc=%s %s" % (r.returncode, (r.stdout or r.stderr).strip()[:120]))
            if r.returncode != 0:
                return 1
        for c in cmds:
            r = subprocess.run(c["dispatch"], cwd=str(root), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            print("dispatch rc=%s %s" % (r.returncode, (r.stdout or r.stderr).strip()[:120]))
        return 0
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
