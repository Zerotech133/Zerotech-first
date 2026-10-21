# -*- coding: utf-8 -*-
"""日报建议信号仅转换注入事实，不访问数据源或触发投递。"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import daily_report


class DailySuggestionTest(unittest.TestCase):
    def test_export_injected_blocks_without_io_or_delivery(self):
        blocks = ["📰 **信息采集**\n24h 入库 12 条", "🔥 **板块异动**（暂无）"]
        with mock.patch.object(daily_report, "_j", side_effect=AssertionError("read")), \
             mock.patch.object(daily_report, "build_blocks", side_effect=AssertionError("build")), \
             mock.patch.object(daily_report.card_build, "build_card3", side_effect=AssertionError("card")):
            result = daily_report.export_daily_suggestion(blocks)
        self.assertEqual(result["source"], "news_collector.daily_report")
        self.assertEqual(result["category"], "ai_finance_daily")
        self.assertEqual(result["basis"], blocks)
        self.assertIsNot(result["basis"], blocks)
        self.assertEqual(result["suggested_time"], "unknown")
        self.assertEqual(result["target"], "unknown")
        self.assertEqual(result["provenance"], {
            "producer": "news_collector.daily_report.build_blocks",
            "evidence": "caller_supplied_blocks",
        })

    def test_empty_facts_do_not_invent_schedule_or_target(self):
        result = daily_report.export_daily_suggestion([])
        self.assertEqual(result["basis"], [])
        self.assertEqual(result["suggested_time"], "unknown")
        self.assertEqual(result["target"], "unknown")


if __name__ == "__main__":
    unittest.main()
