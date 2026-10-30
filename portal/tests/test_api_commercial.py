# -*- coding: utf-8 -*-
"""P2.5: 商业化接口网关 token 测试。"""
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import api_commercial  # noqa: E402


class Stub:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.status = None
        self.obj = None

    def _json(self, obj, status=200):
        self.obj = obj
        self.status = status

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    def wfile(self):
        return None


class TestCommercialGateway(unittest.TestCase):
    def setUp(self):
        os.environ["COMMERCIAL_ADMIN_TOKEN"] = "test-token-123"

    def tearDown(self):
        os.environ.pop("COMMERCIAL_ADMIN_TOKEN", None)

    def test_requires_token(self):
        h = Stub({})
        api_commercial.handle_get(h, "/api/commercial/plans", "")
        self.assertEqual(h.status, 401)
        self.assertIn("X-Commercial-Token", h.obj["error"])

    def test_wrong_token(self):
        h = Stub({"X-Commercial-Token": "bad"})
        api_commercial.handle_get(h, "/api/commercial/plans", "")
        self.assertEqual(h.status, 401)

    def test_correct_token(self):
        h = Stub({"X-Commercial-Token": "test-token-123"})
        api_commercial.handle_get(h, "/api/commercial/plans", "")
        self.assertEqual(h.status, 200)
        self.assertIn("plans", h.obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)
