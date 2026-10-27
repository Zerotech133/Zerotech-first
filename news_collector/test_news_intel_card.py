# -*- coding: utf-8 -*-
"""K-ORG-207 B3：速报卡片 top3 摘要构建单测。

纯内存级（不发网络、不推送、不进库），沿 test_news_time.py 惯例。
覆盖：①自选股命中条目优先排序 + top3 截断 ②JEV 长标记压成短标
（↗利多/↘利空/→中性 + 冲击档位，百分比/范围串不出现）③标题截 40 字
④尾部新闻情报台链接行（K43-c 判例 markdown 超链接）。
"""

import os
import sys
import unittest

# pytest.ini 是 importlib 模式：测试模块按 rootdir 相对点名
# （news_collector.test_xxx），父目录名 news_collector 会被 pytest 预注册成
# namespace package 占住 sys.modules——先清缓存再显式锚目录，保证拿到
# 真模块 news_collector.py（portal 侧测试同款 sys.path.insert 惯例）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules.pop("news_collector", None)

import news_collector as collector


def item(iid, ts, title, stocks=None):
    return {"id": iid, "ts": ts, "title": title, "stocks": stocks or []}


JUDGE_BULL = {
    "bias": "bull",
    "bias_cn": "利多",
    "conf": 0.72,
    "scope_cn": "个股",
    "impact_cn": "强",
}
JUDGE_BEAR = {
    "bias": "bear",
    "bias_cn": "利空",
    "conf": 0.55,
    "scope_cn": "板块",
    "impact_cn": "中",
}
URL = "http://127.0.0.1:8734/news_intel.html"


class CardDigestTest(unittest.TestCase):
    def test_stocks_first_and_top3_cap(self):
        shown = [
            item("a", "2026-10-19 09:00", "普条一"),
            item("b", "2026-10-19 09:05", "普条二"),
            item("c", "2026-10-19 09:10", "票条一", ["义翘神州"]),
            item("d", "2026-10-19 09:15", "票条二", ["中石科技"]),
            item("e", "2026-10-19 09:20", "普条三"),
        ]
        lines = collector.build_card_digest(shown, {}, 5, URL)
        self.assertEqual(len(lines), 4)  # top3 摘要 + 1 链接行
        self.assertIn("【义翘神州】", lines[0])  # 有 stocks 的排最前（stable）
        self.assertIn("【中石科技】", lines[1])
        self.assertIn("普条一", lines[2])  # 无 stocks 保持采集顺序
        self.assertNotIn("【", lines[2])

    def test_jev_short_mark_no_long_tail(self):
        shown = [
            item("a", "2026-10-19 09:00", "标题甲", ["义翘神州"]),
            item("b", "2026-10-19 09:05", "标题乙"),
            item("c", "2026-10-19 09:10", "标题丙"),
        ]
        judges = {"a": JUDGE_BULL, "b": JUDGE_BEAR}
        lines = collector.build_card_digest(shown, judges, 3, URL)
        self.assertIn("↗利多·冲击强", lines[0])
        self.assertIn("↘利空·冲击中", lines[1])
        # 长串（百分比/范围）不得出现：只剩短标
        joined = "\n".join(lines)
        self.assertNotIn("%", joined)
        self.assertNotIn("JEV:", joined)
        self.assertNotIn("个股·", joined)  # scope 串被去掉
        self.assertNotIn("0.72", joined)

    def test_title_truncated_to_40(self):
        long_title = "超长标题" * 20  # 80 字
        lines = collector.build_card_digest(
            [item("a", "2026-10-19 09:00", long_title)], {}, 1, URL
        )
        body = lines[0]
        self.assertIn(long_title[:40], body)
        self.assertNotIn(long_title[:41] + "", body)  # 第 41 字起被截
        self.assertFalse(body.rstrip().endswith(long_title[41]))

    def test_intel_link_line_k43c_markdown(self):
        lines = collector.build_card_digest([], {}, 0, URL)
        self.assertEqual(lines, ["完整 0 条 · 政策 · 舆情 → [新闻情报台](%s)" % URL])

    def test_neutral_and_missing_judge(self):
        shown = [
            item("a", "2026-10-19 09:00", "甲"),
            item("b", "2026-10-19 09:05", "乙"),
        ]
        judges = {"a": {"bias": "neutral"}}  # 无 impact_cn 兜底「未知」
        lines = collector.build_card_digest(shown, judges, 2, URL)
        self.assertIn("→中性·冲击未知", lines[0])
        self.assertNotIn("·冲击", lines[1])  # 无判定无短标
        # 短时间戳形态：ts 尾 5 位 HH:MM
        self.assertIn("09:00", lines[0])

    def test_stocks_priority_beats_recency(self):
        # stocks 命中条目即使在列表末尾也排到 top3 首位
        shown = [
            item("a", "2026-10-19 11:00", "新普条"),
            item("b", "2026-10-19 10:00", "旧票条", ["艾艾精工"]),
        ]
        lines = collector.build_card_digest(shown, {}, 2, URL)
        self.assertIn("旧票条", lines[0])
        self.assertIn("【艾艾精工】", lines[0])


if __name__ == "__main__":
    unittest.main()
