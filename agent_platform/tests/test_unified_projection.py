# -*- coding: utf-8 -*-
"""M1 统一投影聚合器测试（离线 fixture）。

覆盖：空仓库 fail-visible（各节显式报错但不崩）/ 只读标记 /
summary 结构 / 真实仓库只读冒烟（存在时）。
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


up = _load(
    "unified_projection_under_test", "agent_platform/adapters/unified_projection.py"
)


class UnifiedProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_root_fail_visible_not_fatal(self):
        rep = up.build_unified_report(self.root)
        self.assertTrue(rep["read_only"])
        # 域 wiring 缺源显式报错，不拖垮整体
        self.assertEqual(rep["gzh"]["status"], "error")
        self.assertEqual(rep["academy"]["status"], "error")
        self.assertTrue(rep["findings"])  # fail-visible
        # 结构完整：六节齐全
        for key in (
            "tasks",
            "events",
            "projects",
            "academy",
            "gzh",
            "approvals",
            "summary",
        ):
            self.assertIn(key, rep)

    def test_summary_shape(self):
        rep = up.build_unified_report(self.root)
        # 域列表随接线进度扩展（gzh/academy/invest/...），做包含性断言
        self.assertIn("gzh", rep["summary"]["domains_wired"])
        self.assertIn("academy", rep["summary"]["domains_wired"])
        self.assertIn("domain_verdicts", rep["summary"])
        self.assertIn("approval_total", rep["summary"])

    def test_real_repo_read_only_smoke(self):
        if not (ROOT / "gzh" / "drafts" / "index.json").exists():
            self.skipTest("真实仓库数据不在位")
        rep = up.build_unified_report(ROOT)
        self.assertTrue(rep["read_only"])
        self.assertGreaterEqual(rep["gzh"]["workitem_count"], 1)
        self.assertGreaterEqual(rep["academy"]["workitem_count"], 0)
        self.assertIsInstance(rep["approvals"], dict)

    def test_ten_domains_wired(self):
        """M2-P3 扩容：intel 入投影，十域清单 + verdict 齐全。"""
        rep = up.build_unified_report(ROOT)
        for dom in (
            "gzh",
            "academy",
            "invest",
            "dev",
            "pitch",
            "personal",
            "devtask",
            "knowledge",
            "skill",
            "intel",
        ):
            self.assertIn(dom, rep["summary"]["domains_wired"])
            self.assertIn(dom, rep["summary"]["domain_verdicts"])
        # knowledge/skill/intel 节结构与只读纪律
        for sect in ("knowledge", "skill", "intel"):
            self.assertIsInstance(rep[sect], dict)
            self.assertIn("verdict", rep[sect])

    def test_real_repo_intel_smoke(self):
        """真实仓库：intel 域空转 PASS（池空、零绕门、交叉校验零违规）。"""
        rep = up.build_unified_report(ROOT)
        it = rep["intel"]
        self.assertEqual(it.get("verdict"), "PASS")
        self.assertEqual(it.get("bypass_count"), 0)
        self.assertEqual(it.get("cross_check"), [])

    def test_real_repo_knowledge_skill_smoke(self):
        """真实仓库：knowledge/skill 两节可投影且无绕门。"""
        if not (ROOT / "gzh" / "drafts" / "index.json").exists():
            self.skipTest("真实仓库数据不在位")
        rep = up.build_unified_report(ROOT)
        kn = rep["knowledge"]
        self.assertEqual(kn.get("bypass_count"), 0)
        self.assertEqual(kn.get("verdict"), "PASS")
        self.assertEqual(rep["skill"].get("verdict"), "PASS")


if __name__ == "__main__":
    unittest.main()
