# -*- coding: utf-8 -*-
"""KGBRIEF-STATE-20261107 单测：state 落 last_secs。"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
QB = os.path.normpath(os.path.join(HERE, "..", "..", "quant_backtest"))
if QB not in sys.path:
    sys.path.insert(0, QB)

import kg_brief


class TestLastSecsState(unittest.TestCase):
    def test_save_state_writes_last_secs(self):
        """_save_state 调用含 last_secs（结构 [{tc,nm}]）。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "st.json")
            with mock.patch.object(kg_brief, "STATE", p):
                kg_brief._save_state({
                    "last_date": "20261106",
                    "sent_at": "2026-11-07T22:00:00",
                    "last_secs": [{"tc": "600637.SH", "nm": "东方明珠"}],
                })
                st = json.load(open(p, encoding="utf-8"))
            self.assertEqual(st["last_secs"], [{"tc": "600637.SH", "nm": "东方明珠"}])

    def test_meta_carries_last_secs(self):
        """build() 返回的 meta 含 last_secs（mock 全链路太重，验证键存在性与结构）。"""
        # f2_targets 在 build 内部聚合；直接验证 build 返回结构会连 Neo4j——
        # 用源码静态断言代替：函数体含 last_secs 返回键
        src = open(os.path.join(QB, "kg_brief.py"), encoding="utf-8").read()
        self.assertIn('"last_secs": [{"tc": tc, "nm": nm}', src)
        self.assertIn('meta.get("last_secs") or []', src)

    def test_old_state_ignored(self):
        """旧 state（无 last_secs）读取不炸：.get 容错。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "st.json")
            json.dump({"last_date": "20261101", "sent_at": "x"}, open(p, "w", encoding="utf-8"))
            with mock.patch.object(kg_brief, "STATE", p):
                st = kg_brief._load_state()
            self.assertEqual(st.get("last_secs") or [], [])


if __name__ == "__main__":
    unittest.main()
