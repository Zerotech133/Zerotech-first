# -*- coding: utf-8 -*-
"""MCP-A dw-quality 测试：stdio JSON-RPC 协议 + 只读工具调用 + 错误路径。

对照：agent_platform/tools/dw_governance_mcp.py 的 test_mcp_stdio
（tools/datawarehouse/tests/test_dw_wp_i3.py）。全部用子进程走真实 stdio，
不起服务、不写数据；断言只看协议形态与工具判定字段（不断言业务数值）。
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "agent_platform" / "tools" / "dw_quality_mcp.py"

EXPECTED_TOOLS = {
    "quality_snapshot", "freshness", "quality_checks",
    "manifest", "backup", "lineage_impact",
}


def _run_mcp(msgs, timeout=180):
    """把 JSON-RPC 消息按行喂给 MCP 子进程，返回解析后的响应行列表。"""
    payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT)],
        input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, cwd=str(REPO))
    assert r.returncode == 0, "MCP 进程异常退出 rc=%s\nstderr=%s" % (r.returncode, r.stderr[-800:])
    lines = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    assert len(lines) == len(msgs), "响应条数不符：期望 %d 实得 %d\nstdout=%s" % (
        len(msgs), len(lines), r.stdout[-800:])
    return {resp.get("id"): resp for resp in lines}


def _tool_payload(resp):
    """tools/call 成功结果 → 解析 content[0].text 的 JSON 载荷。"""
    assert resp["result"]["isError"] is False, resp["result"]["content"]
    return json.loads(resp["result"]["content"][0]["text"])


def test_mcp_initialize_and_tools_list():
    """initialize 握手 + tools/list：服务名/协议/6 个只读工具/入参 schema 合法。"""
    out = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
    ])
    init = out[1]["result"]
    assert init["serverInfo"]["name"] == "dw-quality"
    assert init["protocolVersion"]
    assert init["capabilities"]["tools"] == {}
    assert out[3]["result"] == {}

    tools = out[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == EXPECTED_TOOLS, "工具集不符: %s" % sorted(names - EXPECTED_TOOLS)
    for t in tools:
        assert t["description"], t["name"]
        schema = t["inputSchema"]
        assert schema["type"] == "object", t["name"]
        assert "additionalProperties" in schema, t["name"]
    by_name = {t["name"]: t for t in tools}
    assert by_name["lineage_impact"]["inputSchema"]["required"] == ["table"]


def test_mcp_call_manifest_and_backup():
    """tools/call：manifest / backup 两个只读工具真实执行并返回判定载荷。"""
    out = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "manifest", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "backup", "arguments": {}}},
    ])
    man = _tool_payload(out[1])
    assert man["tool"] == "manifest_check"
    assert set(man["manifests"]) == {"tdx", "tushare", "xueqiu", "cnopendata"}
    assert man["code"] in (0, 1, 2)

    bak = _tool_payload(out[2])
    assert bak["tool"] == "backup_receipt_check"
    assert "latest_generation" in bak or "error" in bak
    assert bak["code"] in (0, 1, 2)


def test_mcp_call_quality_checks_single_table():
    """tools/call：quality_checks(table=daily) 透传 --table 并返回单表检查项。"""
    out = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "quality_checks", "arguments": {"table": "daily"}}},
    ])
    payload = _tool_payload(out[1])
    assert payload["tool"] == "quality_test"
    assert [t["table"] for t in payload["tables"]] == ["daily"]
    assert isinstance(payload["tables"][0]["checks"], list)


def test_mcp_error_path():
    """错误路径：未知工具 / lineage_impact 缺 table → isError=true；未知方法 → JSON-RPC error。"""
    out = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "no_such_tool", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "lineage_impact", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "no/such/method", "params": {}},
    ])
    for i in (1, 2):
        assert out[i]["result"]["isError"] is True, out[i]
        assert out[i]["result"]["content"][0]["type"] == "text"
    assert out[3]["error"]["code"] == -32601


def test_selftest_reports_tool_count():
    """--selftest：输出工具数与底层脚本存在性（验收口径）。"""
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "--selftest"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(REPO))
    assert r.returncode == 0, r.stderr[-800:]
    report = json.loads(r.stdout)
    assert report["server"] == "dw-quality"
    assert report["tools"] == len(EXPECTED_TOOLS) == 6
    assert report["missing_underlying"] == []
    assert report["read_only"] is True
