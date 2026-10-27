# -*- coding: utf-8 -*-
"""contract_export 纯单测：类型映射/契约构建/check 语义（不读真实数据根）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import contract_export as m  # noqa: E402


class TestTypeMap(unittest.TestCase):
    def test_common_arrow_types(self):
        self.assertEqual(m._map_type("large_string"), "string")
        self.assertEqual(m._map_type("double"), "number")
        self.assertEqual(m._map_type("int64"), "integer")
        self.assertEqual(m._map_type("timestamp[ns]"), "timestamp")
        self.assertEqual(m._map_type("bool"), "boolean")
        self.assertEqual(m._map_type("something_weird"), "string")


class TestBuildContract(unittest.TestCase):
    def _entry(self):
        return {
            "source": "tushare", "store": "C:/x/quant_backtest/data/tushare/daily",
            "layout": "by_date", "schema": ["ts_code", "trade_date", "close"],
            "primary_key": ["ts_code", "trade_date"], "date_col": "trade_date",
            "code_col": "ts_code", "freq": "D", "desc": "日线",
        }

    def test_structure(self):
        c = m.build_contract("daily", self._entry())
        self.assertEqual(c["apiVersion"], "v3.0.2")
        self.assertEqual(c["kind"], "DataContract")
        self.assertEqual(c["id"], "zerobond.daily")
        self.assertEqual(c["owners"][0]["name"], "DATA1 张松")
        sch = c["schema"][0]
        self.assertEqual(sch["name"], "daily")
        self.assertEqual(sch["physicalName"], "tushare/daily")
        props = {p["name"]: p for p in sch["properties"]}
        self.assertTrue(props["ts_code"]["required"])
        self.assertTrue(props["trade_date"]["primaryKey"])
        self.assertFalse(props["close"]["required"])
        # store 读不到时回退为 string 类型（本测试环境 store 不存在）
        self.assertEqual(props["close"]["logicalType"], "string")

    def test_dump_yaml_roundtrip(self):
        c = m.build_contract("daily", self._entry())
        text = m.dump_yaml(c)
        self.assertIn("apiVersion: v3.0.2", text)
        self.assertIn("zerobond.daily", text)


if __name__ == "__main__":
    unittest.main()
