# -*- coding: utf-8 -*-
"""datawarehouse 工具层单测（纯本地，不依赖网络/真实数据仓/凭据）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import backup_receipt_check  # noqa: E402
import catalog_snapshot  # noqa: E402
import common  # noqa: E402
import freshness_report  # noqa: E402
import manifest_check  # noqa: E402
import quality_test  # noqa: E402


class TestCommon(unittest.TestCase):
    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(len(common.sha256_file(p)), 64)
            self.assertIsNone(common.sha256_file(Path(d) / "nope.txt"))

    def test_rel(self):
        self.assertIsInstance(common.rel(common.CATALOG), str)


class TestCatalogSnapshot(unittest.TestCase):
    def test_compare_sha_match_and_mismatch(self):
        self.assertTrue(catalog_snapshot.compare_sha("abc", {"catalog_sha256": "abc"})["match"])
        self.assertFalse(catalog_snapshot.compare_sha("abc", {"catalog_sha256": "def"})["match"])
        self.assertFalse(catalog_snapshot.compare_sha(None, {})["match"])

    def test_drift_sample_missing_store(self):
        cat = {"tables": {"t1": {"store": "Z:/definitely/not/exist", "files": 1}}}
        rows = catalog_snapshot.drift_sample(cat)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["store_exists"])


class TestManifestCheck(unittest.TestCase):
    def test_flat_and_nested(self):
        flat = {"daily": {}, "adj_factor": {}}
        self.assertEqual(manifest_check.manifest_keys("tushare", flat), {"daily", "adj_factor"})
        nested = {"version": 1, "tables": {"a": {}, "b": {}}}
        self.assertEqual(manifest_check.manifest_keys("cnopendata", nested), {"a", "b"})

    def test_coverage(self):
        cov = manifest_check.coverage({"a", "b", "x"}, {"a", "b", "c"})
        self.assertEqual(cov["keys"], 3)
        self.assertEqual(cov["covered"], 2)
        self.assertEqual(cov["missing_count"], 1)
        self.assertIn("c", cov["missing_sample"])
        self.assertIn("x", cov["extra_names"])


class TestFreshness(unittest.TestCase):
    def test_date_from_name(self):
        self.assertEqual(freshness_report.date_from_name("d_20261026.parquet"), "20261026")
        self.assertIsNone(freshness_report.date_from_name("part_0001.parquet"))

    def test_norm_date(self):
        self.assertEqual(freshness_report._norm_date("2026-10-26 15:00:00"), "20261026")
        self.assertEqual(freshness_report._norm_date(20261026), "20261026")
        self.assertIsNone(freshness_report._norm_date("abc"))

    def test_parse_day(self):
        self.assertEqual(str(freshness_report._parse_day("20261026")), "2026-10-26")
        self.assertIsNone(freshness_report._parse_day("bad"))


class TestQuality(unittest.TestCase):
    def test_missing_table_is_failed_not_raised(self):
        r = quality_test.check_table("__no_such_table__")
        self.assertFalse(r["ok"])
        self.assertFalse(r["checks"][0]["ok"])


class TestBackup(unittest.TestCase):
    def test_age_hours(self):
        now = 1_700_000_000.0
        self.assertAlmostEqual(backup_receipt_check.age_hours(now - 3600, now=now), 1.0, places=2)

    def test_clean_receipt_bom_and_non_ascii(self):
        raw = "\ufefffiles=6 ok=6 mismatch=0\r\n中文".encode("utf-8")
        out = backup_receipt_check.clean_receipt(raw)
        self.assertFalse(out.startswith("\ufeff"))
        self.assertIn("files=6 ok=6 mismatch=0", out)
        self.assertTrue(all(ord(ch) < 127 for ch in out))

    def test_latest_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for n in ("2026-10-25_0300", "2026-10-26_0300", "2026-10-26_1545"):
                (root / n).mkdir()
            (root / "notageneration").mkdir()
            self.assertEqual(backup_receipt_check.latest_generation(root).name, "2026-10-26_1545")
            self.assertIsNone(backup_receipt_check.latest_generation(root / "nope"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
