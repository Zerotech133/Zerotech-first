# -*- coding: utf-8 -*-
"""qualify_probe 单测：纯本地 fixture，不读真实数据仓/不联网/不写 registry。"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

try:
    import pyarrow as pa  # noqa: E402
    import pyarrow.parquet as pq  # noqa: E402
    HAS_PYARROW = True
except Exception:  # noqa: BLE001
    HAS_PYARROW = False

import qualify_probe  # noqa: E402

TODAY = dt.date(2026, 10, 27)
CHECKED = "2026-10-27T10:30:00+08:00"


def write_parquet(path: Path, dates, col: str = "trade_date", stats: bool = True) -> None:
    table = pa.table({
        col: pa.array(dates, type=pa.string()),
        "value": pa.array(list(range(len(dates))), type=pa.int64()),
    })
    pq.write_table(table, path, write_statistics=stats)


def entry(store: Path, date_col: str | None = "trade_date", layout: str = "by_date") -> dict:
    return {"store": str(store), "layout": layout, "date_col": date_col}


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestDateHelpers(unittest.TestCase):
    def test_norm_date(self):
        self.assertEqual(qualify_probe.norm_date("20261026"), "20261026")
        self.assertEqual(qualify_probe.norm_date("2026-10-26"), "20261026")
        self.assertEqual(qualify_probe.norm_date(20261026), "20261026")
        self.assertEqual(qualify_probe.norm_date(dt.date(2026, 10, 26)), "20261026")
        self.assertIsNone(qualify_probe.norm_date(None))
        self.assertIsNone(qualify_probe.norm_date("not-a-date"))

    def test_date_from_name(self):
        self.assertEqual(qualify_probe.date_from_name("d_20261026.parquet"),
                         ("20261026", "20261026"))
        self.assertEqual(qualify_probe.date_from_name("p_20240930.parquet"),
                         ("20240930", "20240930"))
        key, fallback = qualify_probe.date_from_name("000906_202610.parquet")
        self.assertEqual(key, "20261099")
        self.assertEqual(fallback, "20261031")
        self.assertEqual(qualify_probe.date_from_name("000001_SZ.parquet"), (None, None))

    def test_manifest_keys(self):
        self.assertEqual(qualify_probe.manifest_keys({"a": {}, "b": {}}), {"a", "b"})
        self.assertEqual(qualify_probe.manifest_keys({"tables": {"x": {}, "y": {}}}), {"x", "y"})
        self.assertEqual(qualify_probe.manifest_keys(None), set())


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestProbeVerdicts(unittest.TestCase):
    def test_evidence_ok(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261026.parquet", ["20261026", "20261026"])
            write_parquet(store / "d_20261025.parquet", ["20261025"])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_ok")
            self.assertEqual(card["rows_sampled"], 3)
            self.assertEqual(card["latest_business_date"], "20261026")
            self.assertEqual(card["latest_source"], "footer_statistics")
            self.assertEqual(card["lag_days"], 1)
            self.assertFalse(card["latest_degraded"])
            self.assertEqual(card["reasons"], [])

    def test_manifest_missing_is_partial(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261026.parquet", ["20261026"])
            card = qualify_probe.probe_table("t1", entry(store), set(), TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertEqual(card["manifest_state"], "missing")
            self.assertIn("manifest_missing", card["reasons"])

    def test_filename_fallback_is_degraded(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261020.parquet", ["20261020"], stats=False)
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertEqual(card["latest_business_date"], "20261020")
            self.assertEqual(card["latest_source"], "filename_degraded")
            self.assertTrue(card["latest_degraded"])
            self.assertIn("filename_fallback", card["reasons"])

    def test_store_missing_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            card = qualify_probe.probe_table(
                "t1", entry(Path(d) / "nope"), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("store_missing", card["reasons"])

    def test_empty_store_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("no_parquet", card["reasons"])

    def test_zero_rows_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261020.parquet", [])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertEqual(card["rows_sampled"], 0)
            self.assertIn("zero_rows_sampled", card["reasons"])

    def test_stale_lag_is_partial(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261019.parquet", ["20261019"])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["lag_days"], 8)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("stale_gt_7d", card["reasons"])

    def test_future_date_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261028.parquet", ["20261028"])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["lag_days"], -1)
            self.assertTrue(card["future_date"])
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("future_date", card["reasons"])

    def test_no_date_column_is_partial(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            pq.write_table(pa.table({"a": pa.array([1]), "b": pa.array(["x"])}),
                           str(store / "part_0000.parquet"))
            card = qualify_probe.probe_table("t1", entry(store, None), {"t1"}, TODAY, CHECKED)
            self.assertIsNone(card["latest_business_date"])
            self.assertEqual(card["latest_source"], "no_date_column")
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("no_date_column", card["reasons"])

    def test_sample_limit_and_latest(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            for day in range(1, 11):
                write_parquet(store / ("d_202610%02d.parquet" % day), ["202610%02d" % day])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertLessEqual(card["sample_count"], 5)
            self.assertGreater(card["rows_sampled"], 0)
            self.assertEqual(card["latest_business_date"], "20261010")

    def test_nested_files_counted_but_top_level_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            (store / "sub").mkdir(parents=True)
            write_parquet(store / "sub" / "d_20260105__prod.parquet", ["20260105"])
            card = qualify_probe.probe_table("q", entry(store, layout="part"), set(), TODAY, CHECKED)
            self.assertEqual(card["files_on_disk"], 1)
            self.assertEqual(card["files_top_level"], 0)
            self.assertEqual(card["nested_dirs"], ["sub"])
            self.assertEqual(card["rows_sampled"], 1)
            self.assertEqual(card["verdict"], "evidence_partial")

    def test_summary_length(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261026.parquet", ["20261026"])
            card = qualify_probe.probe_table("t1", entry(store), set(), TODAY, CHECKED)
            self.assertLessEqual(len(card["evidence_summary"]), 200)
            self.assertLessEqual(len(card["unsupported_reason"]), 200)


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestCollect(unittest.TestCase):
    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = Path(d) / "s1"
            s2 = Path(d) / "s2"
            s1.mkdir()
            s2.mkdir()
            write_parquet(s1 / "d_20261026.parquet", ["20261026"])
            write_parquet(s2 / "d_20261026.parquet", ["20261026"])
            catalog = {"version": 12, "updated": "x", "tables": {
                "a": entry(s1), "b": entry(s2)}}
            payload = qualify_probe.collect_evidence(
                ["a", "b"], catalog, {"a": {}}, TODAY, CHECKED, catalog_sha="fake-sha")
            self.assertEqual(payload["summary"], {"tables": 2, "ok": 1, "partial": 1, "bad": 0})
            self.assertEqual(payload["tables"][0]["verdict"], "evidence_ok")
            self.assertEqual(payload["tables"][1]["verdict"], "evidence_partial")


class TestRegistry(unittest.TestCase):
    def _registry(self):
        return {
            "generated_at": "2026-10-27T10:09:39+08:00",
            "catalog_sha256": "abc",
            "physical_count": 3,
            "display_count": 1,
            "physical_tables": [
                {"asset_id": "a", "qualification_status": "unknown", "other": 1},
                {"asset_id": "b", "qualification_status": "unknown", "other": 2},
                {"asset_id": "c", "qualification_status": "unknown", "other": 3},
            ],
        }

    def _cards(self):
        return [
            {"table": "a", "verdict": "evidence_partial", "evidence_summary": "s1",
             "unsupported_reason": "u1"},
            {"table": "b", "verdict": "evidence_ok", "evidence_summary": "s2",
             "unsupported_reason": "u2"},
        ]

    def test_update_and_validate(self):
        before = self._registry()
        after = qualify_probe.update_registry_entries(before, self._cards(), CHECKED)
        self.assertEqual(after["physical_tables"][0]["qualification_status"], "evidence_partial")
        self.assertEqual(after["physical_tables"][1]["qualification_status"], "evidence_ok")
        self.assertEqual(after["physical_tables"][2]["qualification_status"], "unknown")
        self.assertEqual(after["physical_tables"][2]["other"], 3)
        self.assertEqual(after["physical_count"], 3)
        val = qualify_probe.validate_registry(before, after, ["a", "b"], "abc",
                                              expected_physical=3, expected_display=1)
        self.assertTrue(val["ok"], val)
        self.assertTrue(val["diff"]["only_allowed_fields"])
        self.assertTrue(val["diff"]["other_entries_unchanged"])

    def test_validate_rejects_other_entry_change(self):
        before = self._registry()
        after = qualify_probe.update_registry_entries(before, self._cards(), CHECKED)
        after["physical_tables"][2]["other"] = 99
        val = qualify_probe.validate_registry(before, after, ["a", "b"], "abc",
                                              expected_physical=3, expected_display=1)
        self.assertFalse(val["ok"])
        self.assertFalse(val["checks"]["other_entries_unchanged"])

    def test_validate_rejects_sha_drift(self):
        before = self._registry()
        after = qualify_probe.update_registry_entries(before, self._cards(), CHECKED)
        after["catalog_sha256"] = "drift"
        val = qualify_probe.validate_registry(before, after, ["a", "b"], "abc",
                                              expected_physical=3, expected_display=1)
        self.assertFalse(val["ok"])
        self.assertFalse(val["checks"]["catalog_sha256_unchanged"])

    def test_write_registry_backup_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "registry.json"
            path.write_text(json.dumps(self._registry(), ensure_ascii=False), encoding="utf-8")
            reg = self._registry()
            reg["physical_tables"][0]["qualification_status"] = "evidence_partial"
            info = qualify_probe.write_registry(path, reg)
            self.assertTrue(Path(info["backup"]).is_file())
            reread = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(reread["physical_tables"][0]["qualification_status"],
                             "evidence_partial")
            self.assertEqual(reread["physical_count"], 3)


class TestKgLog(unittest.TestCase):
    def test_ok(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("kg_sync OK 2026-10-27T08:08:08\n", encoding="utf-8")
            self.assertTrue(qualify_probe.check_kg_log(p)["ok"])

    def test_in_progress_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("kg_sync start 2026-10-27T10:00:00\n", encoding="utf-8")
            r = qualify_probe.check_kg_log(p)
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "last_entry_in_progress")

    def test_error_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("kg_sync exit=1 2026-10-27T10:00:00\n", encoding="utf-8")
            r = qualify_probe.check_kg_log(p)
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "last_entry_error")

    def test_missing_refused(self):
        with tempfile.TemporaryDirectory() as d:
            r = qualify_probe.check_kg_log(Path(d) / "nope.log")
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "missing")

    def test_skips_previous_qual_probe_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("qual_probe date=x tables=26 ok=0 partial=26 bad=0 exit=0\n"
                         "registry_rebuild date=x tables=245 sha=x exit=0\n",
                         encoding="utf-8")
            self.assertTrue(qualify_probe.check_kg_log(p)["ok"])


class TestDefaults(unittest.TestCase):
    def test_default_tables(self):
        self.assertEqual(len(qualify_probe.DEFAULT_TABLES), 26)
        self.assertEqual(len(set(qualify_probe.DEFAULT_TABLES)), 26)
        self.assertIn("quarantine_prod_overwrite", qualify_probe.DEFAULT_TABLES)
        self.assertIn("us_fina_indicator", qualify_probe.DEFAULT_TABLES)


if __name__ == "__main__":
    unittest.main()
