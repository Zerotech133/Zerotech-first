# -*- coding: utf-8 -*-
"""单席位能力学习计划（2026-10-28 WO-LEARNING-DEV7）。

计划真源：`agent_platform/config/seat_learning/<SEAT>.json`
字段：目标能力 / 学习模块（材料+证据）/ 实践工单 / 评估方法。
用途：capability enrichment & learning 的**可检查**计划，不虚报完成。

用法：
  python -X utf8 tools/seat_learning.py --check DEV7
  python -X utf8 tools/seat_learning.py --status DEV7
  python -X utf8 tools/seat_learning.py --json DEV7
  python -X utf8 tools/seat_learning.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "agent_platform" / "config" / "seat_learning"
MODULE_STATUS = ("planned", "learning", "done")
ASSESS_STATUS = ("planned", "passed", "failed")


def plan_path(seat):
    return PLANS / ("%s.json" % str(seat).strip().upper())


def load(seat, file=None):
    p = Path(file) if file else plan_path(seat)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data, p, None
    except Exception as e:  # noqa: BLE001
        return None, p, str(e)


def validate(plan, root=ROOT):
    errors = []
    if not isinstance(plan, dict):
        return ["计划不是 JSON 对象"]
    for k in ("schema", "seat", "mentor", "role_title", "grade",
              "target_capabilities", "modules", "practice", "assessment"):
        if k not in plan:
            errors.append("缺字段 %s" % k)
    if plan.get("schema") != "seat_learning/v1":
        errors.append("schema 应为 seat_learning/v1")
    caps = plan.get("target_capabilities")
    if not isinstance(caps, list) or not [c for c in caps if str(c).strip()]:
        errors.append("target_capabilities 不能为空")
    modules = plan.get("modules")
    if not isinstance(modules, list) or not modules:
        errors.append("modules 不能为空")
    else:
        for i, m in enumerate(modules):
            if not isinstance(m, dict):
                errors.append("modules[%d] 不是对象" % i)
                continue
            mid = m.get("id") or "#%d" % i
            for f in ("id", "title", "source", "status"):
                if not str(m.get(f) or "").strip():
                    errors.append("模块 %s 缺 %s" % (mid, f))
            if m.get("status") not in MODULE_STATUS:
                errors.append("模块 %s status 非法：%r" % (mid, m.get("status")))
            if m.get("status") == "done" and not str(m.get("evidence") or "").strip():
                errors.append("模块 %s 标 done 但无 evidence" % mid)
            src = str(m.get("source") or "").strip()
            if src and "://" not in src:
                if not (Path(root) / src).exists():
                    errors.append("模块 %s 材料路径不存在：%s" % (mid, src))
    practice = plan.get("practice")
    if not isinstance(practice, list) or not practice:
        errors.append("practice 至少 1 个实践工单")
    else:
        for i, p in enumerate(practice):
            if not isinstance(p, dict) or not str(p.get("tid") or "").strip():
                errors.append("practice[%d] 缺 tid" % i)
    assess = plan.get("assessment")
    if not isinstance(assess, dict):
        errors.append("assessment 不是对象")
    else:
        if not str(assess.get("method") or "").strip():
            errors.append("assessment 缺 method")
        if assess.get("status") not in ASSESS_STATUS:
            errors.append("assessment.status 非法：%r" % assess.get("status"))
        if assess.get("status") == "passed" and not str(assess.get("evidence") or "").strip():
            errors.append("assessment 标 passed 但无 evidence")
    return errors


def summary(plan):
    modules = plan.get("modules") or []
    done = sum(1 for m in modules if m.get("status") == "done")
    return {
        "seat": plan.get("seat"),
        "mentor": plan.get("mentor"),
        "role": plan.get("role_title"),
        "grade": plan.get("grade"),
        "targets": len(plan.get("target_capabilities") or []),
        "modules": "%d/%d done" % (done, len(modules)),
        "practice": len(plan.get("practice") or []),
        "assessment": (plan.get("assessment") or {}).get("status"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="单席位能力学习计划（只读+校验）")
    ap.add_argument("seat", nargs="?", default="", help="席位码，如 DEV7")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--file", default="", help="覆盖计划路径（测试用）")
    args = ap.parse_args(argv)
    if args.list:
        rows = []
        for p in sorted(PLANS.glob("*.json")):
            data, _, err = load(p.stem)
            rows.append({"seat": p.stem, "error": err,
                         "summary": summary(data) if data else None})
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0 if all(not r["error"] for r in rows) else 1
    if not args.seat:
        print("需要席位码，如 DEV7", file=sys.stderr)
        return 2
    plan, path, err = load(args.seat, args.file or None)
    errors = [err] if err else validate(plan)
    if args.check:
        ok = not errors
        print(("PASS" if ok else "FAIL") + "：" + str(path) +
              ("" if ok else "；" + "；".join(errors)))
        return 0 if ok else 1
    if args.json:
        print(json.dumps({"path": str(path), "errors": errors,
                          "plan": plan}, ensure_ascii=False, indent=1))
        return 0 if not errors else 1
    if args.status:
        if errors:
            print("FAIL：" + "；".join(errors))
            return 1
        print(json.dumps(summary(plan), ensure_ascii=False, indent=1))
        return 0
    print(json.dumps({"path": str(path), "errors": errors,
                      "summary": summary(plan) if plan else None},
                     ensure_ascii=False, indent=1))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
