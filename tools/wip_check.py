# -*- coding: utf-8 -*-
"""交付型席位 WIP 检查（只读 tasks.json + org.json + dev_routing_rules.json）。

规则（§6.2）：交付型席位 doing + review <= 3；超过告警。
P0-B 扩展（2026-11-06）：从 dev_routing_rules.json 的 wip_caps 读分席上限
（DEV1/DEV5<=3、组员<=2、ARCH1<=2），按 seat_owner（缺失回退 owner）统计；
超限的 parked_candidate 单仍计入在制（只标 park 不改 status，gate 不掩盖真实 WIP），
可用 --exclude-parked 看"执行中"口径。
R10 盲区修复（2026-11-06）：此前只统计交付席，`owner not in seats` 直接 continue，
导致 DSH-Lead 名下 27 单被静默丢弃、报 over=0 假绿。现默认按编排席口径一并统计
（上限读 dsh_lead_exception.max_keep，默认 5），并可用 --no-orchestrators 退回旧口径。
analyze() 函数默认仍不统计编排席（orchestrator_cap=None），历史单测依赖该默认。
用法：
  python -X utf8 tools/wip_check.py
  python -X utf8 tools/wip_check.py --strict   # 超限 rc=1
  python -X utf8 tools/wip_check.py --json
  python -X utf8 tools/wip_check.py --no-routing          # 退回全局 --limit 旧口径
  python -X utf8 tools/wip_check.py --exclude-parked      # 不计 parked_candidate
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
DEFAULT_ORG = os.path.join(REPO, "agent_platform", "registry", "org.json")
DEFAULT_ROUTING = os.path.join(REPO, "agent_platform", "config", "dev_routing_rules.json")
LIMIT = 3
# 交付型部门（name 或 dept 短名均可匹配）
DELIVERY_DEPTS = ("研发部", "质量工程部", "产品管理部", "回测开发部", "数据治理部",
                  "下版本设计推理部", "dev", "quality", "product",
                  "proj_backtest", "proj_data", "proj_next")


def load_seats(org_path):
    org = json.load(io.open(org_path, encoding="utf-8"))
    seats = set()
    dept_of = {}
    for d in org.get("departments") or []:
        name = str(d.get("name") or d.get("dept") or "")
        if not any(k in name for k in DELIVERY_DEPTS) and str(d.get("dept")) not in DELIVERY_DEPTS:
            continue
        for a in d.get("agents") or []:
            seats.add(a)
            dept_of[a] = name
    return seats, dept_of


def load_routing_caps(routing_path):
    """从 dev_routing_rules.json 读分席上限 → {seat: cap}；缺失/坏 JSON 返回 {}（兼容旧口径）。"""
    try:
        routing = json.load(io.open(routing_path, encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(routing, dict):
        return {}
    caps = routing.get("wip_caps") if isinstance(routing.get("wip_caps"), dict) else {}
    seats = routing.get("dev_seats") if isinstance(routing.get("dev_seats"), list) else []
    member_default = caps.get("member_default")
    out = {}
    for seat in seats:
        seat = str(seat)
        if isinstance(caps.get(seat), int):
            out[seat] = caps[seat]
        elif isinstance(member_default, int):
            out[seat] = member_default
    return out


ORCHESTRATOR_CAP_DEFAULT = 5
# 工具执行身份：非席位业务单，不计入 WIP（与 dev_bus_route 的排除单口径一致）
EXCLUDE_OWNERS_DEFAULT = frozenset({"exec-devtask"})


def load_orchestrator_cap(routing_path):
    """编排席上限：读 dsh_lead_exception.max_keep（=5），缺失/坏 JSON 用默认值。"""
    try:
        routing = json.load(io.open(routing_path, encoding="utf-8"))
    except Exception:
        return ORCHESTRATOR_CAP_DEFAULT
    if not isinstance(routing, dict):
        return ORCHESTRATOR_CAP_DEFAULT
    exc = routing.get("dsh_lead_exception")
    if isinstance(exc, dict) and isinstance(exc.get("max_keep"), int):
        return exc["max_keep"]
    return ORCHESTRATOR_CAP_DEFAULT


def load_exclude_owners(routing_path):
    """不计入 WIP 的身份集合：默认含工具执行身份，可由 wip_exclude_owners 追加。"""
    out = set(EXCLUDE_OWNERS_DEFAULT)
    try:
        routing = json.load(io.open(routing_path, encoding="utf-8"))
    except Exception:
        return out
    v = routing.get("wip_exclude_owners") if isinstance(routing, dict) else None
    if isinstance(v, list):
        out |= {str(x) for x in v}
    return out


def analyze(tasks, seats, limit=LIMIT, caps=None, exclude_parked=False,
            orchestrator_cap=None, exclude_owners=None):
    """统计 WIP。

    ``orchestrator_cap=None``（默认）→ 不统计非交付席，保持 2026-11-06 之前的旧口径
    （P0-B task-70 行为，历史单测依赖此默认）。传入整数则把「持有在制单但不在交付席
    名单里的 owner」按编排席口径一并统计——这是 2026-11-06 R10 补的盲区：
    此前 DSH-Lead 名下 27 单全部被 `owner not in seats` 静默丢弃，导致 over=0 假绿。
    """
    caps = caps or {}
    exclude_owners = set(exclude_owners or ())
    rows = []
    orch_rows = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict):
            continue
        owner = str(t.get("seat_owner") or t.get("owner") or "")
        if not owner or owner in exclude_owners:
            continue
        st = str(t.get("status") or "")
        if st not in ("doing", "review"):
            continue
        parked = bool(t.get("parked_candidate"))
        if parked and exclude_parked:
            continue
        row = {"task": tid, "owner": owner, "status": st,
               "parked_candidate": parked,
               "desc": str(t.get("desc") or "")[:80]}
        if owner in seats:
            rows.append(row)
        elif orchestrator_cap is not None:
            orch_rows.append(row)
    by_owner = {}
    for r in rows:
        by_owner.setdefault(r["owner"], []).append(r)
    orch_by_owner = {}
    for r in orch_rows:
        orch_by_owner.setdefault(r["owner"], []).append(r)

    def cap_of(owner):
        return caps.get(owner, limit)

    over = [{"owner": o, "wip": len(v), "cap": cap_of(o), "kind": "delivery",
             "tasks": [x["task"] for x in v]}
            for o, v in by_owner.items() if len(v) > cap_of(o)]
    if orchestrator_cap is not None:
        over += [{"owner": o, "wip": len(v), "cap": orchestrator_cap, "kind": "orchestrator",
                  "tasks": [x["task"] for x in v]}
                 for o, v in orch_by_owner.items() if len(v) > orchestrator_cap]
    over.sort(key=lambda x: -x["wip"])
    return {"limit": limit, "caps": dict(caps),
            "owners": {o: len(v) for o, v in by_owner.items()},
            "orchestrator_owners": {o: len(v) for o, v in orch_by_owner.items()},
            "orchestrator_cap": orchestrator_cap,
            "over": over, "ok": not over, "total_wip": len(rows),
            "total_wip_all": len(rows) + len(orch_rows),
            "parked": len([r for r in rows if r["parked_candidate"]])}


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付型席位 WIP 检查（只读）")
    ap.add_argument("--tasks", default=DEFAULT_TASKS)
    ap.add_argument("--org", default=DEFAULT_ORG)
    ap.add_argument("--routing", default=DEFAULT_ROUTING)
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--no-routing", action="store_true", help="不读 wip_caps，退回全局 --limit")
    ap.add_argument("--exclude-parked", action="store_true", help="不计 parked_candidate（执行中口径）")
    ap.add_argument("--no-orchestrators", action="store_true",
                    help="不统计编排/非交付席（2026-11-06 之前的旧口径，会掩盖 DSH-Lead 堆单）")
    ap.add_argument("--orchestrator-cap", type=int, default=None,
                    help="编排席在制上限；默认读 dsh_lead_exception.max_keep，缺失为 5")
    args = ap.parse_args(argv)
    if not os.path.exists(args.tasks) or not os.path.exists(args.org):
        print("tasks.json 或 org.json 不存在", file=sys.stderr)
        return 2
    tasks = json.load(io.open(args.tasks, encoding="utf-8"))
    seats, _ = load_seats(args.org)
    caps = None if args.no_routing else load_routing_caps(args.routing)
    if args.no_orchestrators:
        orch_cap = None
        exclude_owners = set()
    else:
        orch_cap = (args.orchestrator_cap if args.orchestrator_cap is not None
                    else load_orchestrator_cap(args.routing))
        exclude_owners = load_exclude_owners(args.routing)
    res = analyze(tasks, seats, args.limit, caps=caps, exclude_parked=args.exclude_parked,
                  orchestrator_cap=orch_cap, exclude_owners=exclude_owners)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("wip check: limit=%d seats_with_wip=%d total_wip=%d over=%d" % (
            res["limit"], len(res["owners"]), res["total_wip"], len(res["over"])))
        if res.get("caps"):
            print("  caps: %d seat(s) 分席上限生效%s" % (
                len(res["caps"]),
                "； parked 单已排除（执行中口径）" if args.exclude_parked else
                "； parked_candidate 仍计入在制"))
        if res.get("orchestrator_cap") is not None:
            orch = res.get("orchestrator_owners") or {}
            detail = "，".join("%s=%d" % (k, v) for k, v in sorted(orch.items())) or "无"
            print("  orchestrators: cap=%d 在制=%d 席（%s）" % (
                res["orchestrator_cap"], sum(orch.values()), detail))
        for o in res["over"]:
            print("  OVER %-10s wip=%d cap=%d kind=%s tasks=%s" % (
                o["owner"], o["wip"], o["cap"], o.get("kind", "delivery"),
                ",".join(o["tasks"])))
    if args.strict and not res["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
