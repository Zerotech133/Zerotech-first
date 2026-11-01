# -*- coding: utf-8 -*-
"""⑤ 委托收敛守卫：XqAdapter 解析核心必须委托 xq_pump（防双写回潮）。

背景（2026-11-01 归档决策 ⑤）：intel_collector 与 xq_pump 曾各写一份雪球
归一化逻辑，构成双写漂移风险。收敛后 XqAdapter._normalize 委托
xq_pump.extractors._parse.parse_status，适配器仅做字段增强。

本测试钉死该委托关系：若有人恢复适配器内私有解析实现（双写回潮），
此测试立即变红。
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.xueqiu_adapter import XqAdapter, _xq_parse


class TestDelegationToXqPump(unittest.TestCase):
    def test_normalize_delegates_to_xq_pump(self):
        """_normalize 的解析结果必须与 xq_pump.parse_status 一致（同一实现）。"""
        _unwrap, parse_status = _xq_parse()
        import xq_pump.extractors._parse as xp
        self.assertIs(parse_status, xp.parse_status,
                      "XqAdapter 必须委托 xq_pump.parse_status，不得私有实现")

    def test_nested_data_row_matches_xq_pump_core_fields(self):
        """嵌套 data 结构：核心字段与 xq_pump 直调结果一致（status_id/created_at/desc）。"""
        item = {"data": {"id": "g-1", "created_at": 1700000000000,
                         "title": "", "description": "正文",
                         "user": {"id": "u1"}, "reply_count": 3}}
        ad = XqAdapter("xueqiu_public_v1")
        row = ad._normalize(item, "timeline")
        core = _xq_parse()[1](item, 0)
        self.assertEqual(row["status_id"], core["status_id"])
        self.assertEqual(row["created_at"], core["created_at"])
        self.assertEqual(row["desc"], core["desc"])

    def test_adapter_enhancements_preserved(self):
        """适配器增强字段不丢：user_screen/target dict/reply_count 别名/comments text+parent。"""
        ad = XqAdapter("xueqiu_public_v1")
        cmt = {"data": {"id": "c9", "text": "评", "user": {"id": "u2", "screen_name": "乙"},
                        "target": {"id": "p1"}, "created_at": 1700000000000,
                        "comment_count": 7}}
        r = ad._normalize(cmt, "comments")
        self.assertEqual(r["user_screen"], "乙")
        self.assertEqual(r["target"], {"id": "p1"})
        self.assertEqual(r["reply_count"], 7)
        self.assertEqual(r["text"], "评")
        self.assertEqual(r["parent"], "p1")

    def test_malformed_falls_back_raw(self):
        """畸形项兜底为 {'_raw': item}，不抛异常。"""
        ad = XqAdapter("xueqiu_public_v1")
        self.assertEqual(ad._normalize("junk", "timeline"), {"_raw": "junk"})


if __name__ == "__main__":
    unittest.main()
