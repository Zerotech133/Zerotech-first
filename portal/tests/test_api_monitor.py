# -*- coding: utf-8 -*-
"""批量监控门户测试（/api/monitor/*）。"""
import io
import json
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import api_monitor as mod  # noqa: E402


class FakeMonitor:
    def __init__(self):
        self.calls = []

    def list_pools(self):
        return {"watchlist": ["600337.SH", "688023.SH"], "custom": ["603667.SH"]}

    def summary(self):
        return {"pools": {"watchlist": 2}, "alerts_total": 3, "by_severity": {"high": 1},
                "last_run": {"run_id": "r1", "ts": "2026-11-04 03:00:00"}}

    def list_alerts(self, pool=None, limit=50, severity=None):
        self.calls.append(("list", pool, limit, severity))
        return [{"code": "600337.SH", "rule": "new_event", "severity": "high", "title": "重整"}]

    def run_monitor(self, pool="watchlist", as_of=None, dry_run=False):
        self.calls.append(("run", pool, as_of, dry_run))
        return {"ok": True, "run_id": "r2", "scanned": 2, "new_alerts": 1}

    def set_pool(self, name, codes):
        self.calls.append(("set", name, tuple(codes)))

    def add_codes(self, name, codes):
        self.calls.append(("add", name, tuple(codes)))

    def remove_codes(self, name, codes):
        self.calls.append(("remove", name, tuple(codes)))


class FakeHandler:
    def __init__(self, headers=None, body=None):
        self.client_address = ("127.0.0.1", 1234)
        self.headers = dict(headers or {})
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers.setdefault("Content-Length", str(len(raw)))
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.obj = None

    def _json(self, obj, status=200):
        self.status = status
        self.obj = obj

    def _check_portal_token(self):
        return self.headers.get("X-Portal-Token") == "tok"


class TestMonitorApi(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMonitor()
        self._orig = mod._monitor
        mod._monitor = lambda: self.fm

    def tearDown(self):
        mod._monitor = self._orig

    def _h(self, body=None, token=True):
        h = FakeHandler(body=body)
        if token:
            h.headers["X-Portal-Token"] = "tok"
        return h

    def test_pools(self):
        h = self._h(token=False)
        mod.handle_get(h, "/api/monitor/pools", "")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertIn("watchlist", h.obj["pools"])

    def test_alerts(self):
        h = self._h(token=False)
        mod.handle_get(h, "/api/monitor/alerts", "limit=5&severity=high")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.obj["count"], 1)
        self.assertEqual(self.fm.calls[-1], ("list", None, 5, "high"))

    def test_bad_severity(self):
        h = self._h(token=False)
        mod.handle_get(h, "/api/monitor/alerts", "severity=critical")
        self.assertEqual(h.status, 400)

    def test_qs_unquotes_pool_name(self):
        """页面用 encodeURIComponent(pool)，中文池名必须先 URL 解码再交给引擎（P2）。"""
        h = self._h(token=False)
        mod.handle_get(h, "/api/monitor/alerts",
                       "pool=%E6%88%91%E7%9A%84%E8%87%AA%E9%80%89&limit=5")
        self.assertEqual(h.status, 200)
        self.assertEqual(self.fm.calls[-1], ("list", "我的自选", 5, None))

    def test_engine_missing(self):
        mod._monitor = lambda: None
        h = self._h(token=False)
        mod.handle_get(h, "/api/monitor/pools", "")
        self.assertEqual(h.status, 503)

    def test_post_requires_token(self):
        h = self._h(body={"pool": "watchlist"}, token=False)
        mod.handle_post(h, "/api/monitor/run", "")
        self.assertEqual(h.status, 403)

    def test_run(self):
        h = self._h(body={"pool": "watchlist", "dry_run": True})
        mod.handle_post(h, "/api/monitor/run", "")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(self.fm.calls[-1][0], "run")

    def test_pool_add(self):
        h = self._h(body={"action": "add", "name": "custom", "codes": ["603667.sh"]})
        mod.handle_post(h, "/api/monitor/pool", "")
        self.assertEqual(h.status, 200)
        self.assertEqual(self.fm.calls[-1][0], "add")
        self.assertEqual(self.fm.calls[-1][2], ("603667.SH",))

    def test_pool_bad_code(self):
        h = self._h(body={"action": "add", "name": "custom", "codes": ["bad"]})
        mod.handle_post(h, "/api/monitor/pool", "")
        self.assertEqual(h.status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
