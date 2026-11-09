# -*- coding: utf-8 -*-
"""dw-etl-dryrun MCP 测试：只返回计划、不写文件。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "agent_platform" / "tools" / "dw_etl_dryrun_mcp.py"


def _call(msgs):
    inp = "\n".join(json.dumps(m) for m in msgs) + "\n"
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT)], input=inp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return [json.loads(x) for x in r.stdout.splitlines() if x.strip()]


def test_dryrun_tools():
    lines = _call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    assert lines[0]["result"]["serverInfo"]["name"] == "dw-etl-dryrun"
    names = [t["name"] for t in lines[1]["result"]["tools"]]
    assert names == ["etl_actions", "etl_plan"]


def test_plan_is_dry_run_and_no_write(tmp_path):
    target = REPO / "research" / "reports" / "dataset_inventory_20261102.json"
    before = target.stat().st_mtime_ns if target.exists() else None
    lines = _call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "etl_plan", "arguments": {"dataset_id": "yt_learning_db", "action": "staging_clean"}}},
    ])
    body = json.loads(lines[1]["result"]["content"][0]["text"])
    assert body["dry_run_only"] is True and body["executed"] is False
    assert body["approval"]["required"] is True
    assert "planned_writes" in body
    after = target.stat().st_mtime_ns if target.exists() else None
    assert before == after  # 计划不写任何文件


def test_unknown_dataset_is_error():
    lines = _call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "etl_plan", "arguments": {"dataset_id": "no_such_ds", "action": "refresh_sample"}}},
    ])
    assert lines[1]["result"]["isError"] is True
