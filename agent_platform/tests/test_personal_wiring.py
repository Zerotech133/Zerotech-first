# -*- coding: utf-8 -*-
"""M1 个人协同域接线测试（schedules 受控写路径）。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解判例⑤
    spec.loader.exec_module(module)
    return module


class PersonalWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pw = _load("personal_wiring",
                        "agent_platform/domains/personal_wiring.py")
        self.sched = Path(self.tmp.name) / "schedules.json"
        self.db = Path(self.tmp.name) / "approvals.db"
        self.pw.SCHED_FILE = self.sched
        self.pw._db_path = lambda: self.db
        self.store = self.pw.ApprovalStore(self.db)

    def tearDown(self):
        self.store.close()

    def _mk(self, sid="sch-1", enabled=True):
        self.sched.write_text(json.dumps([
            {"id": sid, "agent": "T1", "desc": "测试排程",
             "cron": "daily 09:30", "weekdays_only": True,
             "enabled": enabled, "created": "2026-10-17 13:00"},
        ], ensure_ascii=False), encoding="utf-8")

    # ---- projection ----

    def test_projection_missing_file_fail_visible(self):
        rep = self.pw.projection()
        self.assertEqual(rep["count"], 0)
        self.assertEqual(rep["domain"], "personal")

    def test_projection_fields(self):
        self._mk()
        rep = self.pw.projection()
        self.assertEqual(rep["count"], 1)
        it = rep["schedules"][0]
        self.assertEqual(it["id"], "sch-1")
        self.assertTrue(it["enabled"])
        self.assertEqual(it["history_len"], 0)

    # ---- set_enabled（pause/resume，无凭据工作流）----

    def test_pause_resume_with_history(self):
        self._mk()
        item = self.pw.set_enabled("sch-1", False, by="laotian", note="观察期")
        self.assertFalse(item["enabled"])
        item = self.pw.set_enabled("sch-1", True, by="laotian")
        self.assertTrue(item["enabled"])
        rows = json.loads(self.sched.read_text(encoding="utf-8"))
        hist = rows[0]["history"]
        self.assertEqual([h["action"] for h in hist], ["pause", "resume"])
        self.assertEqual(hist[0]["by"], "laotian")

    def test_set_enabled_same_state_rejected(self):
        self._mk()
        with self.assertRaises(self.pw.PersonalWiringError):
            self.pw.set_enabled("sch-1", True, by="laotian")

    def test_set_enabled_unknown_sid(self):
        self._mk()
        with self.assertRaises(self.pw.PersonalWiringError):
            self.pw.set_enabled("nope", False, by="laotian")
        # 零写入
        rows = json.loads(self.sched.read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["enabled"], True)

    def test_set_enabled_requires_by(self):
        self._mk()
        with self.assertRaises(self.pw.PersonalWiringError):
            self.pw.set_enabled("sch-1", False, by="")

    # ---- delete_schedule（受门，不可逆）----

    def test_delete_first_time(self):
        self._mk()
        deleted, replayed = self.pw.delete_schedule(
            "sch-1", by="laotian", reason="不再需要", store=self.store)
        self.assertFalse(replayed)
        self.assertEqual(deleted["id"], "sch-1")
        rows = json.loads(self.sched.read_text(encoding="utf-8"))
        self.assertEqual(rows, [])
        creds = self.store.list(target_type="schedule")
        self.assertEqual(len(creds), 1)
        self.assertEqual(creds[0].idempotency_key, "sched:sch-1:delete")
        self.assertEqual(creds[0].to_status, "deleted")

    def test_delete_idempotent_replay_catchup(self):
        """崩溃窗口：凭据已落而条目还在 → 补执行。"""
        self._mk()
        self.pw.delete_schedule("sch-1", by="laotian", store=self.store)
        # 重放场景 A：条目已不在 → 账本凭据确认
        _, replayed = self.pw.delete_schedule(
            "sch-1", by="laotian", store=self.store)
        self.assertTrue(replayed)
        # 重放场景 B：条目被旧链重新加回（同 id）→ catch-up 补删
        self._mk()
        _, replayed = self.pw.delete_schedule(
            "sch-1", by="laotian", store=self.store)
        self.assertTrue(replayed)
        rows = json.loads(self.sched.read_text(encoding="utf-8"))
        self.assertEqual(rows, [])

    def test_delete_replay_without_store_cli_path(self):
        """CLI 默认路径（不传 store）：已删条目重试须幂等重放。

        2026-10-17 判例：原实现 store=None 时探查跳过，重试误报
        "不存在（无凭据）"，幂等重放失效——修复后须返回 replayed=True。
        """
        self._mk()
        self.pw.delete_schedule("sch-1", by="laotian")   # 首次，自建 store
        _, replayed = self.pw.delete_schedule("sch-1", by="laotian")
        self.assertTrue(replayed)
        # 无凭据的未知 sid 仍拒绝（不伪造语义不受影响）
        with self.assertRaises(self.pw.PersonalWiringError):
            self.pw.delete_schedule("ghost", by="laotian")

    def test_delete_unknown_sid_no_forgery(self):
        self._mk()
        with self.assertRaises(self.pw.PersonalWiringError):
            self.pw.delete_schedule("ghost", by="laotian", store=self.store)
        # 无凭据落库
        self.assertEqual(len(self.store.list(target_type="schedule")), 0)

    def test_delete_requires_by(self):
        self._mk()
        with self.assertRaises(self.pw.PersonalWiringError):
            self.pw.delete_schedule("sch-1", by="", store=self.store)

    # ---- audit_report ----

    def test_audit_executed_vs_pending(self):
        self._mk()
        # 凭据：目标存在 → pending_execution
        self.pw.delete_schedule("sch-1", by="laotian", store=self.store)
        # 重新加回同 id → 变 pending；再删 → executed
        self._mk()
        rep = self.pw.audit_report()
        self.assertEqual(rep["verdict"], "PASS")
        states = [c["state"] for c in rep["delete_credentials"]]
        self.assertEqual(states, ["pending_execution"])
        self.assertIn("limitation", rep)

    def test_audit_executed_after_removal(self):
        self._mk()
        self.pw.delete_schedule("sch-1", by="laotian", store=self.store)
        rep = self.pw.audit_report()
        self.assertEqual([c["state"] for c in rep["delete_credentials"]],
                         ["executed"])


if __name__ == "__main__":
    unittest.main()
