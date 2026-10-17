# -*- coding: utf-8 -*-
"""ApprovalStore 幂等契约测试（M0 契约补齐：同键异参拒绝）。

2026-10-17 项目审计发现：record() 对同键异参曾静默按重放处理，
违反 M0 IdempotencyRecord 契约「同键同参数复用结果，同键不同参数
拒绝」。本文件锁定修复后的语义。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclass 字符串注解判例
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "agent_platform"))

engine = _load("approvals_engine", "agent_platform/engine/approvals.py")
objects = _load("approvals_objects", "agent_platform/contracts/objects.py")
Approval = objects.Approval
ApprovalStore = engine.ApprovalStore


def _ap(key="dev:K1:done", to="done", frm="doing",
        target="dev:K1", decision="approved"):
    return Approval(target_type="workitem", target_id=target,
                    from_status=frm, to_status=to, decision=decision,
                    decided_by="老田", reason="audit",
                    idempotency_key=key)


class ApprovalStoreContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "approvals.db"
        self.store = ApprovalStore(self.db)   # keeper 连接判例

    def tearDown(self):
        self.store.close()

    def test_same_key_same_args_replay(self):
        a1, rp1 = self.store.record(_ap())
        a2, rp2 = self.store.record(_ap())
        self.assertFalse(rp1)
        self.assertTrue(rp2)
        self.assertEqual(a2.approval_id, a1.approval_id)

    def test_same_key_diff_args_rejected(self):
        self.store.record(_ap())
        # 同键但 to_status 不同 → 必须拒绝（不得静默吞成重放）
        with self.assertRaises(ValueError) as cm:
            self.store.record(_ap(to="closed"))
        self.assertIn("同键异参", str(cm.exception))

    def test_same_key_diff_target_rejected(self):
        self.store.record(_ap())
        with self.assertRaises(ValueError):
            self.store.record(_ap(target="dev:K2"))

    def test_same_key_diff_decision_rejected(self):
        self.store.record(_ap())
        with self.assertRaises(ValueError):
            self.store.record(_ap(decision="rejected"))

    def test_key_required(self):
        with self.assertRaises(ValueError):
            self.store.record(_ap(key=""))

    def test_decision_enum(self):
        with self.assertRaises(ValueError):
            self.store.record(_ap(decision="maybe"))


if __name__ == "__main__":
    unittest.main()
