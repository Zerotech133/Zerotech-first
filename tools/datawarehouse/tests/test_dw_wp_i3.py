# -*- coding: utf-8 -*-
"""WP-I3 测试：HTTP handler（直接调用）+ MCP stdio（子进程）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))


class _FakeHandler:
    def __init__(self):
        self.last = None
        self.status = 200

    def _json(self, obj, status=200):
        self.last = obj
        self.status = status


def test_api_stats():
    import api_dw_registry as api
    h = _FakeHandler()
    api.handle_get(h, "/api/dw/stats", "")
    assert h.last["ok"] is True
    assert h.last["total"] >= 300


def test_api_list_get_sample():
    import api_dw_registry as api
    h = _FakeHandler()
    api.handle_get(h, "/api/dw/list", "domain=%E5%AD%A6%E4%B9%A0%E6%B4%BE%E7%94%9F&limit=5")
    assert h.last["ok"] and h.last["count"] <= 5
    rows = h.last["datasets"]
    assert rows
    did = rows[0]["dataset_id"]
    api.handle_get(h, "/api/dw/get", "id=" + did)
    assert h.last["ok"] and h.last["dataset"]["dataset_id"] == did


def test_api_404_and_400():
    import api_dw_registry as api
    h = _FakeHandler()
    api.handle_get(h, "/api/dw/get", "id=definitely_missing")
    assert h.status == 404 and h.last["ok"] is False
    h2 = _FakeHandler()
    api.handle_get(h2, "/api/dw/sample", "")
    assert h2.status == 400


def test_mcp_stdio():
    script = REPO / "agent_platform" / "tools" / "dw_governance_mcp.py"
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "dw_stats", "arguments": {}}},
    ]
    inp = "\n".join(json.dumps(m) for m in msgs) + "\n"
    r = subprocess.run([sys.executable, "-X", "utf8", str(script)], input=inp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    lines = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    assert lines[0]["result"]["serverInfo"]["name"] == "dw-governance"
    tools = [t["name"] for t in lines[1]["result"]["tools"]]
    assert "dw_stats" in tools and "dw_sample" in tools
    body = json.loads(lines[2]["result"]["content"][0]["text"])
    assert body["total"] >= 300
