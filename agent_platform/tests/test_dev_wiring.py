# -*- coding: utf-8 -*-
"""M1 软件开发域接线测试（TaskStore done 验收门）。"""

import importlib.util
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


class DevWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dw = _load("dev_wiring", "agent_platform/domains/dev_wiring.py")
        self.dw.TASKS_BASE = Path(self.tmp.name) / "org" / "tasks"
        self.db = Path(self.tmp.name) / "approvals.db"
        self.dw._db_path = lambda: self.db
        self.store = self.dw.ApprovalStore(self.db)

    def tearDown(self):
        self.store.close()

    def _mk(self, task_id="K-DEV-1", status="doing"):
        """直接经引擎开单并推到 doing（A0 全权）。返回 task_id。"""
        tid = self.dw.open_task("dev", "测试单", "内容", severity="P1", opened_by="A0")
        if status != "open":
            self.dw._store().move(tid, "assigned", "A0")
            if status != "assigned":
                self.dw._store().move(tid, "doing", "A0")
        return tid

    # ---- 自由转移直通 ----

    def test_move_free_transitions_passthrough(self):
        tid = self._mk(status="open")
        t = self.dw.move_task(tid, "assigned", "A0")
        self.assertEqual(t["status"], "assigned")
        t = self.dw.move_task(tid, "doing", "A0")
        self.assertEqual(t["status"], "doing")

    def test_move_to_done_rejected(self):
        tid = self._mk()
        with self.assertRaises(self.dw.DevWiringError):
            self.dw.move_task(tid, "done", "A0", note="x")

    def test_move_engine_rules_still_enforced(self):
        tid = self._mk(status="open")
        # 引擎状态机仍生效：open→blocked 非法（blocked 仅 doing/assigned 可入）
        with self.assertRaises(self.dw.TaskError):
            self.dw.move_task(tid, "blocked", "A0", note="x")

    # ---- close_done（受门）----

    def test_close_done_full_flow(self):
        tid = self._mk()
        task, replayed = self.dw.close_done(
            tid, by="A0", closure_note="验收通过", store=self.store
        )
        self.assertFalse(replayed)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["closure_note"], "验收通过")
        creds = self.store.list()
        self.assertEqual(creds[0].idempotency_key, "dev:%s:done" % tid)
        self.assertEqual(creds[0].reason, "验收通过")

    def test_close_done_requires_note(self):
        tid = self._mk()
        with self.assertRaises(self.dw.DevWiringError):
            self.dw.close_done(tid, by="A0", closure_note="  ", store=self.store)
        with self.assertRaises(self.dw.DevWiringError):
            self.dw.close_done(tid, by="", closure_note="x", store=self.store)

    def test_close_done_unknown_task(self):
        with self.assertRaises(self.dw.DevWiringError):
            self.dw.close_done("K-DEV-404", by="A0", closure_note="x", store=self.store)

    def test_close_done_idempotent_replay(self):
        tid = self._mk()
        self.dw.close_done(tid, by="A0", closure_note="验收通过", store=self.store)
        task, replayed = self.dw.close_done(
            tid, by="A0", closure_note="验收通过", store=self.store
        )
        self.assertTrue(replayed)
        self.assertEqual(task["status"], "done")
        # 凭据仍只有一条（幂等键唯一）
        self.assertEqual(len(self.store.list()), 1)

    def test_close_done_catchup_after_crash(self):
        """崩溃窗口：凭据已落、move 未执行 → 补执行。"""
        tid = self._mk()
        # 模拟：直接落凭据（等价于上次执行在 move 前崩溃）
        from contracts.objects import Approval

        self.store.record(
            Approval(
                target_type="task",
                target_id="dev:%s" % tid,
                from_status="doing",
                to_status="done",
                decided_by="A0",
                reason="验收通过",
                decided_at="2026-10-17T14:00:00+08:00",
                idempotency_key="dev:%s:done" % tid,
            )
        )
        task, replayed = self.dw.close_done(
            tid, by="A0", closure_note="验收通过", store=self.store
        )
        self.assertTrue(replayed)
        self.assertEqual(task["status"], "done")

    def test_done_without_credential_rejected(self):
        """已 done 但无凭据（绕门）→ 拒绝背书，不伪造。"""
        tid = self._mk()
        self.dw._store().move(tid, "done", "A0", note="绕门关闭")
        with self.assertRaises(self.dw.DevWiringError):
            self.dw.close_done(tid, by="A0", closure_note="x", store=self.store)

    # ---- projection / audit ----

    def test_projection_has_credential_flag(self):
        tid = self._mk()
        rep = self.dw.projection()
        self.assertEqual(rep["count"], 1)
        self.assertFalse(rep["tasks"][0]["has_done_credential"])
        self.dw.close_done(tid, by="A0", closure_note="ok", store=self.store)
        rep = self.dw.projection()
        self.assertTrue(rep["tasks"][0]["has_done_credential"])

    def test_audit_pass_and_bypass_detection(self):
        # 场景 A：受门关闭 → PASS
        tid = self._mk()
        self.dw.close_done(tid, by="A0", closure_note="ok", store=self.store)
        rep = self.dw.audit_report()
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["credentials"][0]["state"], "consistent")
        # 场景 B：绕门 done 另一单 → bypass_done FAIL
        tid2 = self._mk()
        self.dw._store().move(tid2, "done", "A0", note="绕门")
        rep = self.dw.audit_report()
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("bypass_done", [v["kind"] for v in rep["violations"]])

    def test_audit_grandfathered_legacy_done(self):
        """存量收编：接线日前关闭的 done 任务不算 bypass_done。"""
        import json as _json

        f = self.dw.TASKS_BASE / "tasks_202610.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        legacy = {
            "task_id": "T-20261001-001",
            "opened_by": "system",
            "assigned_dept": "dev",
            "assigned_agent": None,
            "title": "历史单",
            "body": "",
            "severity": "P2",
            "status": "done",
            "created_at": "2026-10-01T09:00:00",
            "due": None,
            "closure_note": "存量",
            "history": [
                {
                    "at": "2026-10-01T09:00:00",
                    "by": "A0",
                    "action": "open",
                    "note": None,
                },
                {
                    "at": "2026-10-01T10:00:00",
                    "by": "A0",
                    "action": "done",
                    "note": "存量关闭",
                },
            ],
        }
        f.write_text(_json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
        rep = self.dw.audit_report()
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["violations"], [])

    def test_audit_bypass_after_wiring_date(self):
        """接线日后绕门 done（引擎直关）→ FAIL。"""
        import json as _json

        f = self.dw.TASKS_BASE / "tasks_202610.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        fresh = {
            "task_id": "T-20261017-001",
            "opened_by": "system",
            "assigned_dept": "dev",
            "assigned_agent": None,
            "title": "新单",
            "body": "",
            "severity": "P2",
            "status": "done",
            "created_at": "2026-10-17T14:00:00",
            "due": None,
            "closure_note": "绕门",
            "history": [
                {
                    "at": "2026-10-17T14:00:00",
                    "by": "A0",
                    "action": "open",
                    "note": None,
                },
                {
                    "at": "2026-10-17T14:05:00",
                    "by": "A0",
                    "action": "done",
                    "note": "绕门关闭",
                },
            ],
        }
        f.write_text(_json.dumps(fresh, ensure_ascii=False) + "\n", encoding="utf-8")
        rep = self.dw.audit_report()
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(rep["violations"][0]["kind"], "bypass_done")


# ── M1 审查修复回归：凭据先行与引擎 move 校验同构 ──
class DevGateCongruenceTests(unittest.TestCase):
    def test_blocked_task_done_endorsement_rejected_zero_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dw = _load("dev_wiring_cg", "agent_platform/domains/dev_wiring.py")
        dw.TASKS_BASE = Path(tmp.name) / "org" / "tasks"
        db = Path(tmp.name) / "approvals.db"
        dw._db_path = lambda: db
        store = dw.ApprovalStore(db)
        self.addCleanup(store.close)
        tid = dw.open_task("dev", "阻塞单", "内容", severity="P1", opened_by="A0")
        dw._store().move(tid, "assigned", "A0")
        dw._store().move(tid, "doing", "A0")
        dw._store().move(tid, "blocked", "A0", note="等依赖")
        with self.assertRaises(dw.DevWiringError):
            dw.close_done(tid, "A0", "强行验收", store=store)
        self.assertEqual(len(store.list()), 0)  # 零凭据写入（防死凭据）


if __name__ == "__main__":
    unittest.main()
