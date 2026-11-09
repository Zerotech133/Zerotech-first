# -*- coding: utf-8 -*-
"""门户入口 api_analysis 冒烟测试（不启服务，stub handler）。"""
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(WT, "portal"))
os.environ.setdefault("COMMERCIAL_ADMIN_TOKEN", "test-token")

import api_analysis  # noqa: E402


class Stub:
    def __init__(self, path="", body=None):
        self.path = path
        self.body = body or b""
        self.status = None
        self.obj = None
        self.headers = {"Content-Length": str(len(self.body)),
                        "X-Commercial-Token": "test-token"}
        self.rfile = io.BytesIO(self.body)
        self.wfile = io.BytesIO()

    def _check_portal_token(self):
        return True

    def _json(self, obj, status=200):
        self.obj = obj
        self.status = status


class TestAnalysisApi(unittest.TestCase):
    def test_health(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/health", "")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj.get("ok"))
        self.assertIn("queue", h.obj)

    def test_list(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/list", "limit=5")
        self.assertEqual(h.status, 200)
        self.assertIn("runs", h.obj)

    def test_latest_bad_code(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/latest", "code=abc")
        self.assertEqual(h.status, 400)

    def test_submit_bad_code(self):
        h = Stub(body=json.dumps({"code": "abc"}).encode())
        api_analysis.handle_post(h, "/api/analysis/submit", "")
        self.assertEqual(h.status, 400)

    def test_watchlist(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/watchlist", "")
        self.assertEqual(h.status, 200)
        self.assertIn("codes", h.obj)

    def test_commercial_plans(self):
        import api_commercial
        h = Stub()
        api_commercial.handle_get(h, "/api/commercial/plans", "")
        self.assertEqual(h.status, 200)
        self.assertIn("plans", h.obj)
        self.assertIn("pro", h.obj["plans"])

    def test_commercial_health(self):
        import api_commercial
        h = Stub()
        api_commercial.handle_get(h, "/api/commercial/health", "")
        self.assertEqual(h.status, 200)
        self.assertIn("mode", h.obj)

    def test_unknown(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/nope", "")
        self.assertEqual(h.status, 404)

    def test_events_list(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/events", "limit=5&status=pending")
        self.assertEqual(h.status, 200)
        self.assertIn("events", h.obj)
        self.assertIn("pending_total", h.obj)

    def test_events_bad_code(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/events", "code=abc")
        self.assertEqual(h.status, 400)

    def test_review_bad_input(self):
        h = Stub(body=json.dumps({"event_id": "", "action": "approve"}).encode())
        api_analysis.handle_post(h, "/api/analysis/review", "")
        self.assertEqual(h.status, 400)

    def test_review_missing_event(self):
        h = Stub(body=json.dumps({"event_id": "__nope__", "action": "approve"}).encode())
        api_analysis.handle_post(h, "/api/analysis/review", "")
        self.assertEqual(h.status, 404)

    def test_h5_bad_run(self):
        h = Stub()
        api_analysis.handle_get(h, "/api/analysis/h5", "run_id=../../etc")
        self.assertEqual(h.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
