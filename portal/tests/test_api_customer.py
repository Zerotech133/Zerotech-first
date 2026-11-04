# -*- coding: utf-8 -*-
"""P2.4 客户门户 API 测试（token/校验/路径防护，不跑真实分析）。"""
import io
import json
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = WT
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import api_customer  # noqa: E402
from commercial import audit as audit_mod  # noqa: E402

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
        # H1 审查修复：审计账本 hermetic 化——避免测试写入生产账本
        # （生产账本已迁移为哈希链后，legacy 尾行会让 append fail-closed）。
        self._audit_file = audit_mod.AUDIT_FILE
        audit_mod.AUDIT_FILE = os.path.join(self.td.name, "audit", "audit.jsonl")
        from commercial import license as lic
        self.key = lic.issue("acme", plan="pro", expires=0, secret=SECRET)

    def tearDown(self):
        audit_mod.AUDIT_FILE = self._audit_file
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

    def test_trial_flow(self):
        h = Stub({"name": "测试研究员", "contact": "test@example.com", "company": "Demo基金"})
        api_customer.handle_post(h, "/api/customer/trial")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertTrue(h.obj["tenant"].startswith("trial-"))
        # license 可用同一 secret 校验
        from commercial import license as lic
        l = lic.verify(h.obj["license"], secret=SECRET)
        self.assertEqual(l.tenant, h.obj["tenant"])
        self.assertEqual(l.plan, "pro")

    def test_trial_requires_contact(self):
        h = Stub({"name": "x"})
        api_customer.handle_post(h, "/api/customer/trial")
        self.assertEqual(h.status, 400)

    def test_track(self):
        h = Stub({"event": "view", "ref": "landing"})
        api_customer.handle_post(h, "/api/customer/track")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])

    def test_async_job_flow(self):
        from unittest import mock
        token = api_customer.issue_token("acme", self.key)
        with mock.patch.object(api_customer, "_run_job", lambda *a: None):
            h = Stub({"token": token, "code": "600337.SH"})
            api_customer.handle_post(h, "/api/customer/analyze_async")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        job_id = h.obj["job_id"]
        j = Stub()
        api_customer.handle_get(j, "/api/customer/job", "id=%s&token=%s" % (job_id, token))
        self.assertEqual(j.status, 200)
        self.assertIn(j.obj["job"]["status"], ("queued", "running"))

    def test_report_path_traversal(self):
        self.assertIsNone(api_customer._report_path("acme", "../x", "md"))
        self.assertIsNone(api_customer._report_path("acme", "notexist_123", "md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
