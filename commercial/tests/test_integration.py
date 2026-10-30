# -*- coding: utf-8 -*-
"""P2.1 商业接入 测试：open 模式零影响 / commercial 模式强校验 / 租户隔离目录。"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commercial import audit, integration as ci, license as lic, tenants  # noqa: E402

SECRET = "integration-test-secret-32chars-min"


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = {k: os.environ.pop(k, None) for k in
                     ("COMMERCIAL_MODE", "COMMERCIAL_TENANT", "COMMERCIAL_LICENSE_KEY",
                      "COMMERCIAL_HOME", "COMMERCIAL_DB", "COMMERCIAL_AUDIT",
                      "COMMERCIAL_LICENSE_SECRET")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        os.environ["COMMERCIAL_HOME"] = self.td.name
        self.td.cleanup()

    def _isolate(self):
        tenants.BASE = self.td.name
        tenants.DB_FILE = os.path.join(self.td.name, "commercial.db")
        audit.AUDIT_FILE = os.path.join(self.td.name, "audit", "a.jsonl")
        os.environ["COMMERCIAL_HOME"] = self.td.name

    def _commercial_env(self, tenant="acme", plan="pro", wrong_tenant=False):
        self._isolate()
        os.environ["COMMERCIAL_MODE"] = "commercial"
        os.environ["COMMERCIAL_HOME"] = self.td.name
        os.environ["COMMERCIAL_AUDIT"] = os.path.join(self.td.name, "audit", "a.jsonl")
        os.environ["COMMERCIAL_LICENSE_SECRET"] = SECRET
        key = lic.issue(tenant, plan=plan, expires=0, secret=SECRET)
        os.environ["COMMERCIAL_LICENSE_KEY"] = key
        os.environ["COMMERCIAL_TENANT"] = "other" if wrong_tenant else tenant
        return key

    def test_open_mode_no_effect(self):
        self.assertEqual(ci.mode(), "open")
        ctx = ci.ensure(metric="reports_month")
        self.assertEqual(ctx["mode"], "open")
        self.assertIsNone(ci.deliver_root())

    def test_commercial_requires_key(self):
        os.environ["COMMERCIAL_MODE"] = "commercial"
        with self.assertRaises(ci.CommercialBlocked):
            ci.ensure()

    def test_commercial_ok_and_quota_consumed(self):
        self._commercial_env()
        os.environ["COMMERCIAL_HOME"] = self.td.name
        ctx = ci.ensure(metric="reports_month")
        self.assertEqual(ctx["tenant"], "acme")
        self.assertEqual(ctx["plan"], "pro")
        root = ci.deliver_root()
        self.assertTrue(root)
        self.assertIn(os.path.join("tenants", "acme"), root)
        # 配额已扣 1
        from commercial import quota
        left = quota.remaining("acme", "reports_month", plan="pro")
        self.assertEqual(left, 199)

    def test_tenant_mismatch_blocked(self):
        self._commercial_env(wrong_tenant=True)
        with self.assertRaises(ci.CommercialBlocked):
            ci.ensure()


if __name__ == "__main__":
    unittest.main(verbosity=2)
