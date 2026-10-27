# -*- coding: utf-8 -*-
"""策略单源决策器（P0③ · WO-POLICY-CODE-20261027）。

设计：policy.json 是唯一策略声明（工具 execute 白名单 + 审批级别）；本工具只做
**只读决策 / 与既有多维矩阵对齐检查**，不改变现有 devtask/工具执行路径（强制点
仍由各自守卫按同一 spec 逐步接入，见报告 §4.2）。

用法：
  python -X utf8 agent_platform/tools/policy_check.py --check
  python -X utf8 agent_platform/tools/policy_check.py --agent ARCH1 --op execute --target codebase_intel
  python -X utf8 agent_platform/tools/policy_check.py --agent ARCH1 --op danger --target rm
退出码：0=allowed / 3=denied / 2=输入/配置错误。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
POLICY = REPO / "agent_platform" / "rules" / "policy.json"
MATRIX = REPO / "agent_platform" / "rules" / "tool_matrix.json"

ALLOWED, DENIED, ERROR = 0, 3, 2


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("policy_check: 无法读取 %s (%s)" % (path, exc))
        raise SystemExit(ERROR)


def load_policy() -> dict:
    return _read_json(POLICY)


def resolve(agent: str, op: str, target: str = "") -> dict:
    """返回 {allowed, approval, human_required, reason}；未知岗/未知操作一律 deny。"""
    pol = load_policy()
    ops = pol.get("operations", {})
    approval = pol.get("approval", {})
    if op not in ops:
        return {"allowed": False, "approval": None, "human_required": False,
                "reason": "unknown operation: %s" % op}
    op_spec = ops[op]
    level = op_spec.get("approval") or "L2"
    human = bool(approval.get(level, {}).get("human_required"))
    agents = pol.get("agents", {})
    if agent not in agents:
        return {"allowed": False, "approval": level, "human_required": human,
                "reason": "unknown agent: %s (default deny)" % agent}
    spec = agents[agent]
    if op == "danger":
        return {"allowed": False, "approval": level, "human_required": human,
                "reason": "danger is deny for all agents"}
    if op not in spec:
        return {"allowed": False, "approval": level, "human_required": human,
                "reason": "%s 未声明 %s（默认 deny）" % (agent, op)}
    rules = spec[op]
    if rules == "deny":
        return {"allowed": False, "approval": level, "human_required": human,
                "reason": "%s.%s=deny" % (agent, op)}
    allow = rules.get("allow") or []
    hit = any(target == pat or fnmatch.fnmatchcase(target, pat) for pat in allow)
    return {"allowed": bool(hit), "approval": level, "human_required": human,
            "reason": ("matched allow pattern" if hit else "target not in allowlist: %s" % target)}


def _norm(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return list(v)


def check_against_matrix() -> dict:
    """policy 已覆盖的岗，逐项与 tool_matrix 对齐；未覆盖岗只报覆盖率（迁移中）。"""
    pol = load_policy()
    matrix = _read_json(MATRIX)
    result = {"ok": True, "covered": [], "mismatches": [], "coverage": {}}
    matrix_agents = matrix.get("matrix", {})
    covered = list(pol.get("agents", {}).keys())
    result["coverage"] = {"policy_agents": len(covered), "matrix_agents": len(matrix_agents)}
    for agent in covered:
        pm = matrix_agents.get(agent)
        if pm is None:
            result["ok"] = False
            result["mismatches"].append({"agent": agent, "field": "missing_in_matrix"})
            continue
        pol_agent = pol["agents"][agent]
        for op in ("read", "write", "execute"):
            pol_allow = _norm((pol_agent.get(op) or {}).get("allow"))
            mat_allow = _norm(pm.get(op))
            if pol_allow is None or mat_allow is None:
                continue
            left = sorted([x for x in pol_allow if not str(x).startswith("*")])
            right = sorted([x for x in mat_allow if not str(x).startswith("*")])
            if left != right:
                result["ok"] = False
                result["mismatches"].append({"agent": agent, "field": op,
                                             "policy": pol_allow, "matrix": mat_allow})
        result["covered"].append(agent)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="策略单源决策器（policy.json）")
    ap.add_argument("--check", action="store_true", help="与 tool_matrix 对齐检查")
    ap.add_argument("--agent")
    ap.add_argument("--op")
    ap.add_argument("--target", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        r = check_against_matrix()
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return ALLOWED if r["ok"] else DENIED
    if not args.agent or not args.op:
        ap.error("需要 --agent/--op（或 --check）")
    d = resolve(args.agent, args.op, args.target)
    print(json.dumps({"agent": args.agent, "op": args.op, "target": args.target, **d},
                     ensure_ascii=False, indent=1))
    return ALLOWED if d["allowed"] else DENIED


if __name__ == "__main__":
    raise SystemExit(main())
