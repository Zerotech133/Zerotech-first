"""M2-P3 统一 Skill 目录测试。"""

from __future__ import annotations

import importlib.util
import json
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

sw = _load("sw", "agent_platform/domains/skill_wiring.py")
from engine.approvals import ApprovalStore  # noqa: E402

FULL_ELEMENTS = {k: "占位-%s" % k for k in sw.ELEMENTS}


class SkillWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ApprovalStore(self.root / "data" / "approvals"
                                   / "approvals.db")  # keeper 判例

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _add(self, slug="fact-check", **overrides):
        kw = dict(FULL_ELEMENTS)
        kw.update(overrides)
        return sw.add_skill(self.root, slug, "事实核验", by="academy-pm",
                            domains=["gzh", "invest", "pitch"], **kw)

    def _to_reviewed(self, slug="fact-check"):
        return sw.move_stage(self.root, slug, "reviewed", by="academy-pm")

    # ------------------------------------------------------------------
    def test_add_and_eight_elements(self):
        s = self._add()
        self.assertEqual(s["status"], "draft")
        self.assertEqual(sw._missing_elements(s), [])
        self.assertTrue((self.root / "data" / "skills"
                         / "skills.json").exists())

    def test_duplicate_slug_rejected(self):
        self._add()
        with self.assertRaises(sw.SkillWiringError):
            self._add()

    def test_move_gated_stage_rejected(self):
        self._add()
        with self.assertRaises(sw.SkillWiringError):
            sw.move_stage(self.root, "fact-check", "approved",
                          by="academy-pm")

    def test_move_jump_rejected(self):
        self._add()
        with self.assertRaises(sw.SkillWiringError):
            sw.move_stage(self.root, "fact-check", "draft",
                          by="academy-pm")   # draft→draft 非法

    def test_review_then_approve_full_flow(self):
        self._add()
        self._to_reviewed()
        apv, s, replayed = sw.approve(self.root, "fact-check", "approved",
                                      by="laotian", reason="可跨域复用",
                                      store=self.store)
        self.assertFalse(replayed)
        self.assertEqual(s["status"], "approved")
        self.assertIn("approval_id", s["history"][-1])
        rep = sw.audit_report(self.root)
        self.assertEqual(rep["verdict"], "PASS")

    def test_approve_missing_elements_rejected(self):
        self._add(samples="")                  # 缺样例
        self._to_reviewed()
        with self.assertRaises(sw.SkillWiringError) as ctx:
            sw.approve(self.root, "fact-check", "approved", by="laotian",
                       store=self.store)
        self.assertIn("samples", str(ctx.exception))
        # 真源状态不变
        s = sw._find(sw._load(self.root), "fact-check")
        self.assertEqual(s["status"], "reviewed")

    def test_approve_idempotent_replay(self):
        self._add()
        self._to_reviewed()
        sw.approve(self.root, "fact-check", "approved", by="laotian",
                   store=self.store)
        apv, s, replayed = sw.approve(self.root, "fact-check", "approved",
                                      by="laotian", store=self.store)
        self.assertTrue(replayed)
        self.assertEqual(s["status"], "approved")
        n_hist = len(s["history"])
        apv, s, replayed = sw.approve(self.root, "fact-check", "approved",
                                      by="laotian", store=self.store)
        self.assertEqual(len(s["history"]), n_hist)   # 零副作用

    def test_catchup_after_ledger_reset(self):
        """凭据在而真源未达（崩溃窗口）→ catch-up 补转移。"""
        self._add()
        self._to_reviewed()
        # 先落凭据（模拟崩溃前），再模拟真源回拨
        from contracts.objects import Approval
        sw.approve(self.root, "fact-check", "approved", by="laotian",
                   store=self.store)
        skills = sw._load(self.root)
        skills[0]["status"] = "reviewed"       # 模拟回拨
        skills[0]["history"] = skills[0]["history"][:-1]
        sw._save(self.root, skills)
        apv, s, replayed = sw.approve(self.root, "fact-check", "approved",
                                      by="laotian", store=self.store)
        self.assertFalse(replayed)
        self.assertEqual(s["status"], "approved")     # catch-up 补达

    def test_audit_bypass_approve(self):
        self._add()
        self._to_reviewed()
        skills = sw._load(self.root)
        skills[0]["status"] = "approved"       # 绕门直改
        sw._save(self.root, skills)
        rep = sw.audit_report(self.root)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(any("bypass_approve" in f
                            for f in rep["findings"]))

    def test_deprecate_requires_approval_and_records(self):
        self._add()
        self._to_reviewed()
        sw.approve(self.root, "fact-check", "approved", by="laotian",
                   store=self.store)
        with self.assertRaises(sw.SkillWiringError):
            sw.move_stage(self.root, "fact-check", "deprecated",
                          by="laotian")       # 受门段不可 move
        apv, s, _ = sw.approve(self.root, "fact-check", "deprecated",
                               by="laotian", reason="被 v2 取代",
                               store=self.store)
        self.assertEqual(s["status"], "deprecated")
        rep = sw.audit_report(self.root)
        self.assertEqual(rep["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
