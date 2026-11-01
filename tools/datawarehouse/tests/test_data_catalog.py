# -*- coding: utf-8 -*-
"""数据册校验（v2 契约起，当前 v6）：YAML 合法 / 条目数 / id 唯一 / 字段齐全 / 修正项落盘。

纪律（数据册 v2，2026-10-27 DATA1 首轮治理）：
- 数据册 = agent_platform/catalogs/data_catalog.yaml，登记纪律=新源不登记即黑户；
- 本测试只做结构校验（不连任何数据源、不读凭据），CI 可全量跑。
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

# <repo>/tools/datawarehouse/tests/test_data_catalog.py → parents[3] = repo 根
REPO = Path(__file__).resolve().parents[3]
CATALOG = REPO / "agent_platform" / "catalogs" / "data_catalog.yaml"

# 所有条目必备字段（最保守的最小集）
REQUIRED = ("id", "kind", "uri", "what", "owner", "grants", "verified")
# v2 新增主力源必备字段（登记官要求：来源/责任/备份/验证/失效模式说清楚）
REQUIRED_V2 = ("layer", "steward", "freshness", "backup", "verify_period", "fail_mode")
# v2 明确要求增补的主力源
V2_NEW_IDS = {
    "tushare_raw", "tdx_raw", "tushare_curated", "tushare_staging", "tushare_v2",
    "xueqiu", "anns_pdf", "guba", "cnopendata", "quant_meta_pg", "zhice_os_v2_pg",
}


def load() -> dict:
    with open(CATALOG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class DataCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load()
        cls.resources = [r for r in (cls.doc.get("resources") or []) if isinstance(r, dict)]
        cls.by_id = {r["id"]: r for r in cls.resources if r.get("id")}

    def test_01_yaml_parses_and_has_resources(self):
        self.assertIsInstance(self.doc, dict)
        self.assertIn("resources", self.doc)
        self.assertGreaterEqual(len(self.resources), 18, "数据册 v2 条目数应 ≥18")

    def test_02_ids_unique(self):
        ids = [r.get("id") for r in self.resources]
        self.assertEqual(len(ids), len(set(ids)), "数据册存在重复 id：%s" % ids)

    def test_03_required_fields_present(self):
        for r in self.resources:
            for field in REQUIRED:
                self.assertIn(field, r, "条目 %s 缺少字段 %s" % (r.get("id"), field))
                self.assertIsNotNone(r.get(field), "条目 %s 字段 %s 为空" % (r.get("id"), field))

    def test_04_v2_new_entries_have_full_fields(self):
        missing = V2_NEW_IDS - set(self.by_id)
        self.assertFalse(missing, "v2 增补条目缺失：%s" % sorted(missing))
        for rid in sorted(V2_NEW_IDS):
            entry = self.by_id[rid]
            for field in REQUIRED_V2:
                self.assertIn(field, entry, "v2 条目 %s 缺少字段 %s" % (rid, field))
                self.assertTrue(entry.get(field), "v2 条目 %s 字段 %s 为空" % (rid, field))

    def test_05_kg_dataroot_deprecated(self):
        entry = self.by_id["kg_dataroot"]
        self.assertEqual(entry.get("status"), "deprecated")
        self.assertEqual(entry.get("superseded_by"), "quant_data_root")
        self.assertIn("quant_data_root", self.by_id, "注销条目必须有替代者登记")
        self.assertNotEqual(self.by_id["quant_data_root"].get("status"), "deprecated")

    def test_06_paper_db_uri_fixed(self):
        self.assertEqual(self.by_id["paper_db"].get("uri"), "k_stock/data/paper.db")

    def test_07_weak_state_uri_fixed(self):
        uri = self.by_id["weak_state"].get("uri", "")
        self.assertIn("quant_backtest/reports/weak_scan_", uri)
        self.assertIn("weak_scan_last.json", uri)
        self.assertNotIn("weak_track_", uri)

    def test_08_v2_header(self):
        # 数据册已演进到 v6（2026-11-01 增补 strategy_classics_kb）；保留 v2 契约：version 为整数且 ≥2
        self.assertIsInstance(self.doc.get("version"), int)
        self.assertGreaterEqual(self.doc.get("version"), 2)
        # updated 随 catalog 演进（v2 起）；断言格式即可，不硬编码日期
        self.assertRegex(str(self.doc.get("updated")), r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("DATA1", str(self.doc.get("registrar")))

    def test_09_router_flat_parse_compatible(self):
        """router.load_seed 逐行解析要求每个条目行首为 '- id:'，这里守格式不破约定。"""
        text = CATALOG.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n  - id: "), len(self.resources),
                         "条目块数应与 '- id:' 行数一致（router.load_seed 依赖）")


if __name__ == "__main__":
    unittest.main()
