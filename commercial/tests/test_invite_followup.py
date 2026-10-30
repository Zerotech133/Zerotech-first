# -*- coding: utf-8 -*-
"""P3.1 邀请码/跟进 测试。"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commercial import audit, followup, invite, tenants  # noqa: E402


class InviteBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._orig = (tenants.BASE, tenants.DB_FILE, audit.AUDIT_FILE)
        tenants.BASE = self.td.name
        tenants.DB_FILE = os.path.join(self.td.name, "commercial.db")
        audit.AUDIT_FILE = os.path.join(self.td.name, "audit", "a.jsonl")

    def tearDown(self):
        tenants.BASE, tenants.DB_FILE, audit.AUDIT_FILE = self._orig
        self.td.cleanup()


class TestInvite(InviteBase):
    def test_create_and_redeem(self):
        inv = invite.create("wechat", max_uses=2)
        ok, channel, err = invite.redeem(inv["code"], "trial-a")
        self.assertTrue(ok)
        self.assertEqual(channel, "wechat")
        ok2, _, _ = invite.redeem(inv["code"], "trial-b")
        self.assertTrue(ok2)
        ok3, _, err3 = invite.redeem(inv["code"], "trial-c")
        self.assertFalse(ok3)
        self.assertIn("用完", err3)
        self.assertEqual(len(invite.list_invites()), 1)

    def test_redeem_unknown(self):
        ok, _, err = invite.redeem("ZB-NOPE", "trial-x")
        self.assertFalse(ok)
        self.assertIn("不存在", err)

    def test_channels(self):
        invite.create("douyin", max_uses=5)
        invite.create("douyin", max_uses=1)
        invite.create("direct", max_uses=1)
        ch = {c["channel"]: c for c in invite.channels()}
        self.assertEqual(ch["douyin"]["codes"], 2)
        self.assertEqual(ch["direct"]["codes"], 1)


class TestFollowup(InviteBase):
    def test_plan_stages(self):
        # 一个 2 天前、一个 8 天前的试用租户
        c = tenants._db()
        now = time.time()
        c.execute("INSERT OR REPLACE INTO tenants(id,name,plan,license_key,active,created) VALUES(?,?,?,?,1,?)",
                  ("trial-new", "新试用", "pro", "", now - 2 * 86400))
        c.execute("INSERT OR REPLACE INTO tenants(id,name,plan,license_key,active,created) VALUES(?,?,?,?,1,?)",
                  ("trial-old", "老试用", "pro", "", now - 8 * 86400))
        c.commit()
        audit.append("trial_started", tenant="trial-new", path=audit.AUDIT_FILE,
                     detail={"channel": "wechat"})
        items = followup.plan(now=now)
        m = {i["tenant"]: i for i in items}
        self.assertEqual(m["trial-new"]["stage"], "首日回访")
        self.assertEqual(m["trial-new"]["channel"], "wechat")
        self.assertEqual(m["trial-old"]["stage"], "到期转化")
        self.assertIn("跟进", followup.render_md(items))

    def test_export(self):
        p = os.path.join(self.td.name, "followup.md")
        r = followup.export(p)
        self.assertTrue(os.path.exists(p))
        self.assertIn("count", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
