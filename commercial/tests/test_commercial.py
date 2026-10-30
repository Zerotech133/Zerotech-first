# -*- coding: utf-8 -*-
"""P2 商业化底座 测试：license / 租户 / 配额 / 审计（全部用临时路径）。"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commercial import audit, license as lic, quota, tenants  # noqa: E402


class TestLicense(unittest.TestCase):
    SECRET = "unit-test-secret-32chars-minimum"

    def test_roundtrip(self):
        key = lic.issue("acme", plan="enterprise", expires=time.time() + 3600, seats=5, secret=self.SECRET)
        l = lic.verify(key, secret=self.SECRET)
        self.assertEqual(l.tenant, "acme")
        self.assertEqual(l.plan, "enterprise")
        self.assertEqual(l.seats, 5)
        self.assertIn("...", lic.mask(key))

    def test_tamper(self):
        key = lic.issue("acme", secret=self.SECRET)
        bad = key[:-4] + "AAAA"
        with self.assertRaises(lic.LicenseError):
            lic.verify(bad, secret=self.SECRET)

    def test_wrong_secret(self):
        key = lic.issue("acme", secret=self.SECRET)
        with self.assertRaises(lic.LicenseError):
            lic.verify(key, secret="another-secret")

    def test_expired(self):
        key = lic.issue("acme", expires=time.time() - 10, secret=self.SECRET)
        with self.assertRaises(lic.LicenseError):
            lic.verify(key, secret=self.SECRET)

    def test_bad_format(self):
        with self.assertRaises(lic.LicenseError):
            lic.verify("not-a-license")


class TestTenants(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.td.name, "c.db")

    def tearDown(self):
        self.td.cleanup()

    def test_add_get_list(self):
        t = tenants.add_tenant("acme", "ACME", "enterprise", db=self.db)
        self.assertEqual(t["id"], "acme")
        self.assertTrue(tenants.get_tenant("acme", db=self.db)["active"])
        self.assertEqual(len(tenants.list_tenants(db=self.db)), 1)

    def test_tenant_dir_and_traversal(self):
        d = tenants.tenant_dir("acme", base=self.td.name)
        self.assertTrue(d.endswith(os.path.join("tenants", "acme")))
        for bad in ("../evil", "a/b", "a\\b", ".."):
            with self.assertRaises(ValueError):
                tenants.tenant_dir(bad, base=self.td.name)


class TestQuota(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.td.name, "c.db")
        tenants.add_tenant("acme", plan="free", db=self.db)

    def tearDown(self):
        self.td.cleanup()

    def test_consume_and_limit(self):
        for i in range(5):
            used, left = quota.consume("acme", "reports_month", 1, plan="free", db=self.db)
        self.assertEqual(used, 5)
        self.assertEqual(left, 0)
        with self.assertRaises(quota.QuotaExceeded):
            quota.consume("acme", "reports_month", 1, plan="free", db=self.db)

    def test_usage_report(self):
        r = quota.usage_report("acme", plan="free", db=self.db)
        self.assertEqual(r["reports_month"]["limit"], 5)
        self.assertEqual(r["reports_month"]["remaining"], 5)


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = os.path.join(self.td.name, "audit.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def test_append_and_tail(self):
        audit.append("license_gen", tenant="acme", path=self.p, detail={"plan": "pro"})
        audit.append("quota_consume", tenant="acme", path=self.p, detail={"n": 1})
        recs = audit.tail(10, path=self.p)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["action"], "license_gen")
        self.assertEqual(len(audit.tail(10, path=self.p, tenant="other")), 0)

    def test_export(self):
        audit.append("x", tenant="a", path=self.p)
        dest = os.path.join(self.td.name, "export.jsonl")
        self.assertEqual(audit.export(dest, path=self.p), 1)
        self.assertTrue(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main(verbosity=2)
