# -*- coding: utf-8 -*-
"""P2.4 客户门户 API 测试（token/校验/路径防护，不跑真实分析）。"""
import io
import json
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = r"C:\项目\老田的AI助手"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(WT, "portal"))

import api_customer  # noqa: E402

SECRET = "customer-portal-test-secret-32chars"


class Stub:
    def __init__(self, body=None):
        self.body = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(self.body))}
        self.rfile = io.BytesIO(self.body)
        self.wfile = io.BytesIO()
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


class TestCustomerApi(unittest.TestCase):
    def setUp(self):
        os.environ["COMMERCIAL_LICENSE_SECRET"] = SECRET
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._home = api_customer.HOME
        api_customer.HOME = self.td.name
        os.environ["COMMERCIAL_HOME"] = self.td.name
        from commercial import license as lic
        self.key = lic.issue("acme", plan="pro", expires=0, secret=SECRET)

    def tearDown(self):
        api_customer.HOME = self._home
        os.environ.pop("COMMERCIAL_LICENSE_SECRET", None)
        self.td.cleanup()

    def test_token_roundtrip(self):
        t = api_customer.issue_token("acme", self.key)
        d = api_customer.verify_token(t)
        self.assertEqual(d["t"], "acme")

    def test_token_tamper_and_expired(self):
        t = api_customer.issue_token("acme", self.key)
        self.assertIsNone(api_customer.verify_token(t[:-4] + "AAAA"))
        t2 = api_customer.issue_token("acme", self.key, ttl=-1)
        self.assertIsNone(api_customer.verify_token(t2))

    def test_login_ok(self):
        h = Stub({"tenant": "acme", "license": self.key})
        api_customer.handle_post(h, "/api/customer/login")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["plan"], "pro")
        self.assertIn("token", h.obj)

    def test_login_tenant_mismatch(self):
        h = Stub({"tenant": "other", "license": self.key})
        api_customer.handle_post(h, "/api/customer/login")
        self.assertEqual(h.status, 403)

    def test_login_bad_license(self):
        h = Stub({"tenant": "acme", "license": "SAC1.bad.bad"})
        api_customer.handle_post(h, "/api/customer/login")
        self.assertEqual(h.status, 401)

    def test_analyze_bad_code(self):
        token = api_customer.issue_token("acme", self.key)
        h = Stub({"token": token, "code": "../etc/passwd"})
        api_customer.handle_post(h, "/api/customer/analyze")
        self.assertEqual(h.status, 400)

    def test_analyze_no_token(self):
        h = Stub({"token": "bad", "code": "600337.SH"})
        api_customer.handle_post(h, "/api/customer/analyze")
        self.assertEqual(h.status, 401)

    def test_report_path_traversal(self):
        self.assertIsNone(api_customer._report_path("acme", "../x", "md"))
        self.assertIsNone(api_customer._report_path("acme", "notexist_123", "md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
