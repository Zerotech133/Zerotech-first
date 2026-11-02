# -*- coding: utf-8 -*-
"""K10 验证：通过 MCP stdio 对数据仓做 10 问 E2E（只读），产出 research/reports/k10_e2e_<date>.json。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "agent_platform"))
sys.path.insert(0, str(REPO / "agent_platform" / "legion_mcp"))
REPORTS = REPO / "research" / "reports"
OUT = REPORTS / ("k10_e2e_%s.json" % datetime.now().strftime("%Y%m%d"))
PY = r"C:\Program Files\Python311\python.exe"
MCP = str(REPO / "agent_platform" / "tools" / "dw_governance_mcp.py")

CASES = [
    (1, "dw_stats", {}),
    (2, "dw_list", {"limit": 5}),
    (3, "dw_list", {"domain": "接口数据", "limit": 3}),
    (4, "dw_search", {"q": "daily", "limit": 3}),
    (5, "dw_get", {"id": "ts_daily"}),
    (6, "dw_sample", {"id": "ts_daily", "n": 2}),
    (7, "dw_query", {"id": "ts_daily", "limit": 2}),
    (8, "dw_query", {"id": "ts_moneyflow", "limit": 2}),
    (9, "dw_query", {"id": "ts_report_rc", "count": True, "limit": 1}),
    (10, "dw_query", {"id": "ts_daily_basic", "limit": 2}),
]


def is_ok(tool, payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("isError"):
        return False
    p = payload.get("payload") if "payload" in payload else payload
    if not isinstance(p, dict):
        return False
    if tool == "dw_stats":
        return "datasets" in p or "total" in p
    if tool in ("dw_list", "dw_search"):
        return isinstance(p.get("datasets"), list) and len(p["datasets"]) > 0
    if tool == "dw_get":
        return bool(p.get("dataset") or p.get("dataset_id") or p.get("id") or p.get("found"))
    if tool == "dw_sample":
        return bool(p.get("sample") or p.get("rows") or p.get("entries")
                    or p.get("top_entries") or p.get("newest_files"))
    if tool == "dw_query":
        return p.get("state") in ("ok", "no_data") and ("columns" in p or "rows" in p)
    return bool(p)


def main() -> int:
    try:
        from client import MCPClient  # type: ignore
    except ImportError:
        from agent_platform.legion_mcp.client import MCPClient  # type: ignore
    c = MCPClient([PY, MCP], cwd=str(REPO), timeout=30)
    c.start()
    answers = []
    passed = 0
    try:
        tools = c.list_tools()
        names = {t.get("name") for t in tools}
        for q, tool, args in CASES:
            rec = {"q": q, "tool": tool, "args": args, "expected": "ok", "got": "",
                   "passed": False, "text": ""}
            try:
                r = c.call_tool(tool, args)
                rec["got"] = "ok" if is_ok(tool, r) else "bad_payload"
                rec["passed"] = bool(is_ok(tool, r))
                rec["text"] = json.dumps(r, ensure_ascii=False)[:400]
            except Exception as exc:  # noqa: BLE001
                rec["got"] = "error: %s" % type(exc).__name__
                rec["text"] = str(exc)[:300]
            passed += 1 if rec["passed"] else 0
            answers.append(rec)
    finally:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "server": MCP, "transport": "stdio JSON-RPC",
           "tools_registered": len(tools), "answers": answers,
           "passed": passed, "total": len(CASES),
           "note": "K10 数据仓工具 10 问 E2E（只读 MCP 调用）；Hermes 运行时工具以 /api/mcp/tools 为准"}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "passed": passed, "total": len(CASES),
                      "answers": [{"q": a["q"], "tool": a["tool"], "passed": a["passed"]} for a in answers]},
                     ensure_ascii=False))
    return 0 if passed == len(CASES) else 2


if __name__ == "__main__":
    raise SystemExit(main())
