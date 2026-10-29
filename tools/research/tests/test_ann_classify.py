# -*- coding: utf-8 -*-
"""ann_classify 单测：columns_txt 优先、标题兜底、覆盖率口径。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.ann_classify as ac  # noqa: E402


class TestAnnClassify(unittest.TestCase):
    def test_columns_first(self):
        self.assertEqual(ac.classify("股东/实际控制人股份减持", "关于减持的公告"), "增减持")
        self.assertEqual(ac.classify("回购进展情况", ""), "回购")
        self.assertEqual(ac.classify("业绩预告", ""), "业绩预告")
        self.assertEqual(ac.classify("保荐/核查意见", ""), "治理合规")

    def test_title_fallback(self):
        self.assertEqual(ac.classify("", "某公司:关于对外投资的公告"), "对外投资关联")
        self.assertEqual(ac.classify("", "某公司:中标公告"), "重大合同中标")

    def test_other_fallback(self):
        self.assertEqual(ac.classify("", "普通日常公告标题"), "其他")
        self.assertEqual(ac.classify("", ""), "其他")

    def test_priority(self):
        # columns_txt 含多类时按优先级（业绩预告 > 治理合规）
        self.assertEqual(ac.classify("业绩预告;董事会决议公告", ""), "业绩预告")
        # 减持优先于治理合规
        self.assertEqual(ac.classify("董事会决议公告;股东减持", ""), "增减持")


if __name__ == "__main__":
    unittest.main()
