# -*- coding: utf-8 -*-
"""H1 stock-analysis MCP 测试（协议/工具注册/请求构造/fail-visible）。"""
import importlib.util
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

MCP_PATH = os.path.join(WT, "agent_platform", "tools", "stock_analysis_mcp.py")
spec = importlib.util.spec_from_file_location("stock_analysis_mcp", MCP_PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TestMcpProtocol(unittest.TestCase):
    def test_selftest(self):
        self.assertEqual(m.main(["--selftest"]), 0)

    def test_initialize(self):
        r = m.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["id"], 1)
        self.assertEqual(r["result"]["protocolVersion"], m.PROTOCOL_VERSION)
        self.assertEqual(r["result"]["serverInfo"]["name"], "stock-analysis")

    def test_tools_list(self):
        r = m.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        for n in ("sa_health", "sa_overview", "sa_watchlist", "sa_data_status",
                  "sa_report", "sa_events", "sa_analyze", "sa_batch", "sa_job_status"):
            self.assertIn(n, names)

    def test_unknown_tool(self):
        r = m.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        self.assertIn("error", r)

    def test_unknown_method(self):
        r = m.handle_message({"jsonrpc": "2.0", "id": 4, "method": "nope"})
        self.assertIn("error", r)

    def test_notification_no_response(self):
        self.assertIsNone(m.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_call_analyze_builds_post(self):
        calls = []

        def fake_http(path, method="GET", body=None):
            calls.append((path, method, body))
            return {"ok": True, "job_id": "j1"}

        orig = m._http
        m._http = fake_http
        try:
            r = m.handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                  "params": {"name": "sa_analyze",
                                             "arguments": {"code": "600337.SH"}}})
        finally:
            m._http = orig
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(calls[0][0], "/api/analysis/internal/analyze")
        self.assertEqual(calls[0][1], "POST")
        self.assertEqual(calls[0][2]["code"], "600337.SH")

    def test_call_error_flagged(self):
        calls = []

        def fake_http(path, method="GET", body=None):
            calls.append(path)
            return {"ok": False, "error": "missing code"}

        orig = m._http
        m._http = fake_http
        try:
            r = m.handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                  "params": {"name": "sa_data_status", "arguments": {"code": ""}}})
        finally:
            m._http = orig
        # 空 code 不发起网络请求，直接返回错误
        self.assertTrue(r["result"]["isError"])

    def test_unreachable_gateway_fail_visible(self):
        orig_url, orig_timeout = m.BASE_URL, m.TIMEOUT
        m.BASE_URL = "http://127.0.0.1:1"
        m.TIMEOUT = 1
        try:
            r = m._http("/api/analysis/internal/health")
        finally:
            m.BASE_URL, m.TIMEOUT = orig_url, orig_timeout
        self.assertFalse(r["ok"])
        self.assertIn("unreachable", r["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
