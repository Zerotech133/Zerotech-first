# -*- coding: utf-8 -*-
"""REG-W-20261108 单测：唯一真源升格结构断言。"""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
B = os.path.normpath(os.path.join(HERE, "..", "..", "intel_collector", "registry", "sources.json"))
A = os.path.normpath(os.path.join(HERE, "..", "..", "agent_platform", "data", "intel", "sources.json"))


class TestRegW(unittest.TestCase):
    def test_b_governance_block(self):
        """B 账本：_governance 结构与 authoritative=true。"""
        d = json.load(open(B, encoding="utf-8"))
        g = d.get("_governance")
        self.assertIsNotNone(g)
        self.assertTrue(g["authoritative"])
        self.assertIn("INTEL0", g["owner"])
        self.assertIn("田峰", g["signed"])
        self.assertIn("agent_platform/data/intel/sources.json", g["supersedes"])

    def test_b_seven_sources_intact(self):
        """B 账本：7 源 keys 与基线一致（数据未被触碰）。"""
        d = json.load(open(B, encoding="utf-8"))
        keys = sorted(k for k in d if not k.startswith("_"))
        base = json.load(open(os.path.join(HERE, "..", "..", "reports", "backups", "regw_20261108", "baseline_keys.json"), encoding="utf-8"))
        self.assertEqual(keys, sorted(base))
        self.assertEqual(len(keys), 7)

    def test_a_pointer(self):
        """A 账本：superseded_by 指针就位。"""
        a = json.load(open(A, encoding="utf-8"))
        self.assertEqual(a.get("superseded_by"), "intel_collector/registry/sources.json")

    def test_rights_untouched(self):
        """B 账本 7 源 rights 结构与备份逐一致（21 格 true 不变）。"""
        d = json.load(open(B, encoding="utf-8"))
        bak = json.load(open(os.path.join(HERE, "..", "..", "reports", "backups", "regw_20261108", "sources.json.pre_regw.bak"), encoding="utf-8"))
        for k in bak:
            self.assertEqual(d[k], bak[k], k)


if __name__ == "__main__":
    unittest.main()
