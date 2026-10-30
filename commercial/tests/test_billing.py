# -*- coding: utf-8 -*-
"""P2.3 计费运营 测试：账单/导出/续费提醒/漏斗。"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commercial import audit, billing, license as lic, plans, tenants  # noqa: E402

SECRET = "billing-test-secret-32chars-min"


class BillingBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._orig = (tenants.BASE, tenants.DB_FILE, audit.AUDIT_FILE)
        tenants.BASE = self.td.name
        tenants.DB_FILE = os.path.join(self.td.name, "commercial.db")
        audit.AUDIT_FILE = os.path.join(self.td.name, "audit", "a.jsonl")
        os.environ["COMMERCIAL_HOME"] = self.td.name
        os.environ["COMMERCIAL_LICENSE_SECRET"] = SECRET

    def tearDown(self):
        tenants.BASE, tenants.DB_FILE, audit.AUDIT_FILE = self._orig
        os.environ.pop("COMMERCIAL_LICENSE_SECRET", None)
        self.td.cleanup()

    def _tenant(self, tid="acme", plan="pro", days=365, seats=2):
        key = lic.issue(tid, plan=plan, expires=time.time() + days * 86400, seats=seats, secret=SECRET)
        tenants.add_tenant(tid, plan=plan, license_key=key)

    def test_price_plans(self):
        self.assertGreater(plans.PLANS["pro"]["price_month"], 0)
        self.assertEqual(plans.PLANS["free"]["price_month"], 0)


class TestBilling(BillingBase):
    def test_invoice_plan_fee(self):
        self._tenant(seats=2)
        inv = billing.invoice("acme", month="202610")
        self.assertEqual(inv["plan"], "pro")
        self.assertEqual(inv["seats"], 2)
        self.assertEqual(inv["total"], plans.PLANS["pro"]["price_month"] * 2)
        md = billing.render_md(inv)
        self.assertIn("账单", md)
        self.assertIn("合计", md)

    def test_invoice_overage(self):
        self._tenant(plan="pro", seats=1)
        # pro 含 200 份/月；写入 210 份用量（模拟套餐降档后的历史超额）
        c = tenants._db()
        c.execute("INSERT OR REPLACE INTO usage(tenant,period,metric,n) VALUES(?,?,?,?)",
                  ("acme", "202610", "reports_month", 210))
        c.commit()
        inv = billing.invoice("acme", month="202610")
        ent = plans.PLANS["pro"]
        self.assertEqual(inv["used"], 210)
        self.assertEqual(inv["total"], ent["price_month"] * 1 + 10 * ent["price_per_report"])

    def test_export(self):
        self._tenant()
        out = os.path.join(self.td.name, "bills")
        r = billing.export_invoices(out, month="202610")
        self.assertEqual(r["count"], 1)
        self.assertTrue(os.path.exists(r["csv"]))
        self.assertTrue(os.path.exists(r["json"]))

    def test_missing_tenant(self):
        with self.assertRaises(KeyError):
            billing.invoice("nobody")


class TestRenewals(BillingBase):
    def test_expiring_and_expired(self):
        self._tenant("soon", days=10)
        self._tenant("gone", days=-1)
        self._tenant("far", days=300)
        rows = billing.renewals(within_days=30)
        ids = {r["tenant"] for r in rows}
        self.assertIn("soon", ids)
        self.assertIn("gone", ids)
        self.assertNotIn("far", ids)


class TestFunnel(BillingBase):
    def test_funnel_counts(self):
        audit.append("trial_started", tenant="t1", path=audit.AUDIT_FILE)
        audit.append("trial_started", tenant="t2", path=audit.AUDIT_FILE)
        audit.append("license_gen", tenant="t1", path=audit.AUDIT_FILE)
        audit.append("report_generated", tenant="t1", path=audit.AUDIT_FILE)
        f = billing.funnel(path=audit.AUDIT_FILE)
        self.assertEqual(f["trials"], 2)
        self.assertEqual(f["activated"], 1)
        self.assertEqual(f["active_users"], 1)
        self.assertEqual(f["trial_to_paid"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
