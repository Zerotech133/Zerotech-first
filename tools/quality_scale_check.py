# -*- coding: utf-8 -*-
"""O2 质量分级检查器：席位/工具/集成三层 × Bronze/Silver（只读）。

用法：
  python -X utf8 tools/quality_scale_check.py
  python -X utf8 tools/quality_scale_check.py --json
  python -X utf8 tools/quality_scale_check.py --strict   # 任一对象未达 Bronze → rc=1
规则见 docs/standards/2026-10-27_质量分级_v1.md。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(REPO, "agent_platform", "config", "quality_scale.json")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

BRONZE = ("owner", "docs", "tests", "risk", "port", "token", "rollback", "events", "fail_visible")
SILVER = ("health", "acceptance", "version", "diagnostics", "backup_owner", "docs_depth")


def _registry():
    try:
        path = os.path.join(REPO, "tools", "check_ports.py")
        spec = importlib.util.spec_from_file_location("check_ports_qs", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        reg = getattr(mod, "REGISTRY", {}) or {}
        ports = set()
        for k, v in reg.items():
            if isinstance(k, int):
                ports.add(k)
            elif isinstance(v, int):
                ports.add(v)
            elif isinstance(v, dict) and isinstance(v.get("port"), int):
                ports.add(v["port"])
        return ports
    except Exception:  # noqa: BLE001
        return set()


def _exists(rel):
    if not rel:
        return False
    p = rel if os.path.isabs(rel) else os.path.join(REPO, rel)
    return os.path.exists(p)


def _check(obj, ctx):
    checks = {}
    o = obj if isinstance(obj, dict) else {}
    checks["owner"] = bool(str(o.get("owner") or "").strip())
    docs = o.get("docs") or []
    checks["docs"] = bool(docs) and all(_exists(d) for d in docs)
    tests = o.get("tests") or []
    checks["tests"] = bool(tests) and all(_exists(t) for t in tests)
    risk = str(o.get("risk") or "").lower()
    checks["risk"] = risk == "read-only" or (risk == "write" and bool(o.get("l2_approved")))
    port = o.get("port")
    if o.get("type") == "integration" and port:
        checks["port"] = int(port) in ctx["registry"]
    else:
        checks["port"] = True
    tok = o.get("token_env")
    transport = str(o.get("transport") or "").lower()
    if tok:
        checks["token"] = bool(ENV_NAME_RE.match(str(tok)))
    elif transport == "http":
        checks["token"] = False
    else:
        checks["token"] = True
    checks["rollback"] = bool(str(o.get("rollback") or "").strip())
    checks["events"] = bool(o.get("events"))
    checks["fail_visible"] = bool(o.get("fail_visible"))
    for k in ("health", "acceptance", "version", "diagnostics", "backup_owner"):
        checks[k] = bool(str(o.get(k) or "").strip())
    checks["docs_depth"] = len(o.get("docs") or []) >= 2
    bronze_ok = all(checks.get(k) for k in BRONZE)
    silver_ok = bronze_ok and all(checks.get(k) for k in SILVER)
    tier = "silver" if silver_ok else ("bronze" if bronze_ok else "none")
    return {"tier": tier, "checks": checks}


def evaluate(config, ctx=None):
    ctx = ctx or {"registry": _registry()}
    objects = (config or {}).get("objects") or {}
    out = {}
    for name, obj in objects.items():
        out[name] = _check(obj, ctx)
    return out


def summarize(results):
    counts = {"silver": 0, "bronze": 0, "none": 0}
    for r in results.values():
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    return {"ok": counts["none"] == 0, "counts": counts, "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser(description="质量分级检查（只读）")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="任一对象未达 Bronze 时 rc=1")
    args = ap.parse_args(argv)
    if not os.path.exists(args.config):
        print("config 不存在：%s" % args.config, file=sys.stderr)
        return 2
    try:
        with io.open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        print("config 解析失败：%s" % e, file=sys.stderr)
        return 2
    result = summarize(evaluate(config))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        c = result["counts"]
        print("quality scale: silver=%d bronze=%d none=%d" % (c["silver"], c["bronze"], c["none"]))
        for name, r in sorted(result["results"].items()):
            missing = [k for k, v in r["checks"].items() if not v]
            mark = {"silver": "S", "bronze": "B", "none": "x"}[r["tier"]]
            print("  [%s] %-32s %s" % (mark, name, ("missing: " + ",".join(missing)) if missing else "ok"))
    if args.strict and not result["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
