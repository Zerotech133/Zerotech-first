# -*- coding: utf-8 -*-
"""Hermes MCP 底座探针：真实启动 dw-* MCP servers，列出工具并执行一次 dw_query 证据。

用法：python tools/datawarehouse/hermes_mcp_probe.py [--json]
输出：research/reports/hermes_mcp_probe_<YYYYMMDD>.json
边界：只读（MCP server 只读封装）；不写数据仓；超时/失败 fail-visible。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "agent_platform"))
sys.path.insert(0, str(REPO / "agent_platform" / "legion_mcp"))
REPORTS = REPO / "research" / "reports"
OUT = REPORTS / ("hermes_mcp_probe_%s.json" % datetime.now().strftime("%Y%m%d"))

SERVERS = [
    ("dw-governance", "数据查询/治理", r"C:\Program Files\Python311\python.exe",
     [str(REPO / "agent_platform" / "tools" / "dw_governance_mcp.py")], 30),
    ("dw-quality", "数据质量/新鲜度", r"C:\项目\py312\python.exe",
     [str(REPO / "agent_platform" / "tools" / "dw_quality_mcp.py")], 30),
    ("dw-catalog", "数据目录/资产", r"C:\项目\py312\python.exe",
     [str(REPO / "agent_platform" / "tools" / "dw_catalog_mcp.py")], 30),
    ("factor-tools", "因子工具", r"C:\项目\py312\python.exe",
     [str(REPO / "agent_platform" / "tools" / "factor_tools_mcp.py")], 30),
]


def probe():
    try:
        from client import MCPClient  # legion_mcp/client.py
    except ImportError:
        from agent_platform.legion_mcp.client import MCPClient  # type: ignore
    result = {"tool": "hermes_mcp_probe", "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
              "servers": [], "summary": {}}
    ok = 0
    tools_total = 0
    evidence = None
    for name, duty, py, args, tmo in SERVERS:
        t0 = time.time()
        rec = {"name": name, "duty": duty, "ok": False, "tools": [], "tool_count": 0,
               "duration_s": None, "error": None, "exec_evidence": None}
        c = None
        try:
            c = MCPClient([py] + args, cwd=str(REPO), timeout=tmo)
            c.start()
            tools = c.list_tools()
            rec["tools"] = [{"name": t.get("name"), "description": str(t.get("description") or "")[:120]}
                            for t in tools]
            rec["tool_count"] = len(tools)
            rec["ok"] = True
            ok += 1
            tools_total += len(tools)
            # 真实执行证据：dw-governance.dw_query / dw_stats
            if name == "dw-governance":
                tn = {t["name"] for t in rec["tools"]}
                if "dw_query" in tn:
                    r = c.call_tool("dw_query", {"id": "ts_daily", "limit": 1, "count": True})
                    rec["exec_evidence"] = {"tool": "dw_query",
                                            "args": {"id": "ts_daily", "limit": 1, "count": True},
                                            "result": str(r)[:500]}
                    evidence = rec["exec_evidence"]
                elif "dw_stats" in tn:
                    r = c.call_tool("dw_stats", {})
                    rec["exec_evidence"] = {"tool": "dw_stats", "result": str(r)[:500]}
                    evidence = rec["exec_evidence"]
        except Exception as exc:  # noqa: BLE001
            rec["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            rec["duration_s"] = round(time.time() - t0, 1)
        result["servers"].append(rec)
    result["summary"] = {"servers": len(SERVERS), "ok": ok, "tools_total": tools_total,
                         "exec_evidence": evidence}
    result["all_ok"] = (ok == len(SERVERS)) and bool(evidence)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hermes MCP 底座探针")
    ap.add_argument("--json", action="store_true")
    ap.parse_args(argv)
    res = probe()
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"out": str(OUT), "summary": res["summary"], "all_ok": res["all_ok"]}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if res["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
