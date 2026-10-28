# -*- coding: utf-8 -*-
"""研发军团作战屏 v1 · 冒烟（LS-10，2026-10-28）。

只读检查（不启服务、不写数据）：
1) 契约 JSON：schema/readonly/endpoint/字段；
2) API payload：11 席/2 组/必需字段（运行时数据缺失只 WARN 不 FAIL）；
3) 前端页：tokens/nav/shortscreen/fetch/板块。

用法：python -X utf8 tools/dev_legion_smoke.py [--json]
rc=0 PASS；rc=1 FAIL。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "dev" / "legion_screen_contract.json"
PAGE = ROOT / "portal" / "dev_legion.html"
sys.path.insert(0, str(ROOT / "portal"))


def check_contract():
    issues = []
    try:
        c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ["contract_read: %s" % type(e).__name__], []
    warns = []
    if c.get("schema") != "legion_screen_contract/v1":
        issues.append("schema")
    if c.get("readonly") is not True:
        issues.append("readonly")
    if c.get("endpoint") != "/api/dev/legion":
        issues.append("endpoint")
    if c.get("status") not in ("frozen", "proposed"):
        issues.append("status")
    return issues, warns


def check_payload():
    issues, warns = [], []
    try:
        import api_dev_legion as A
        p = A.build_payload(A.ROOT)
    except Exception as e:  # noqa: BLE001
        return ["payload_import: %s" % type(e).__name__], warns
    if p.get("schema") != "legion_screen_contract/v1":
        issues.append("payload_schema")
    if len(p.get("seats") or []) != 11:
        issues.append("payload_seats=%d" % len(p.get("seats") or []))
    if len((p.get("department") or {}).get("groups") or []) != 2:
        issues.append("payload_groups")
    for k in ("work_in_progress", "metrics", "redlines", "sources", "errors"):
        if k not in p:
            issues.append("payload_missing:%s" % k)
    if p.get("errors"):
        warns.append("data_sources: " + "; ".join(p["errors"]))
    return issues, warns


def check_page():
    issues = []
    if not PAGE.is_file():
        return ["page_missing"], []
    txt = PAGE.read_text(encoding="utf-8")
    for marker in ("/static/tokens.css", "/static/nav.js", "shortscreen.js",
                   'fetch("/api/dev/legion"', "组织架构", "在制任务",
                   "C6 指标", "红线状态"):
        if marker not in txt:
            issues.append("page_marker:%s" % marker)
    return issues, []


def main(argv=None):
    ap = argparse.ArgumentParser(description="作战屏 v1 冒烟（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    issues, warns = [], []
    for fn in (check_contract, check_payload, check_page):
        i, w = fn()
        issues += i
        warns += w
    out = {"ok": not issues, "issues": issues, "warnings": warns}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(("PASS" if not issues else "FAIL") + "：作战屏 v1 冒烟")
        for w in warns:
            print("  WARN " + w)
        for i in issues:
            print("  FAIL " + i)
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
