# -*- coding: utf-8 -*-
"""metadata_consistency 单测（临时 catalog/registry/lifecycle 夹具）。"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import metadata_consistency as m  # noqa: E402


class FakeLifecycle:
    def __init__(self, mapping):
        self.mapping = mapping

    def status_for(self, t):
        return self.mapping.get(t, "active")

    def all_overrides(self):
        return dict(self.mapping)


class TestMetadataConsistency(unittest.TestCase):
    def _write(self, td, tables, physical, life, store_dirs=()):
        root = Path(td)
        for d in store_dirs:
            (root / d).mkdir(parents=True, exist_ok=True)
        cat = {"version": 13, "tables": tables}
        cp = root / "catalog.json"
        cp.write_text(json.dumps(cat), encoding="utf-8")
        sha = hashlib.sha256(cp.read_bytes()).hexdigest()
        reg = {"catalog_sha256": sha, "physical_count": len(physical),
               "display_count": 0, "display_assets": [], "physical_tables": physical}
        rp = root / "registry.json"
        rp.write_text(json.dumps(reg), encoding="utf-8")
        return cp, rp, FakeLifecycle(life)

    def test_ok(self):
        with tempfile.TemporaryDirectory() as td:
            store = str(Path(td) / "store")
            Path(store).mkdir()
            tables = {"daily": {"store": store, "manifest": "/x", "manifest_status": "closed"}}
            phys = [{"physical_tables": ["daily"], "qualification_status": "unknown"}]
            cp, rp, lc = self._write(td, tables, phys, {}, store_dirs=["store"])
            res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 0, res)
            self.assertEqual(res["issues"], [])

    def test_store_missing_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tables = {"daily": {"store": str(Path(td) / "nope"), "manifest": "/x",
                                "manifest_status": "closed"}}
            cp, rp, lc = self._write(td, tables, [{"physical_tables": ["daily"]}], {})
            res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 1)
            self.assertTrue(any("store_missing:daily" in i for i in res["issues"]))

    def test_planned_store_missing_ok(self):
        with tempfile.TemporaryDirectory() as td:
            tables = {"xq_kline_d": {"store": str(Path(td) / "nope"),
                                     "verification_status": "placeholder_empty",
                                     "manifest_status": "missing"}}
            phys = [{"physical_tables": ["xq_kline_d"], "qualification_status": "planned"}]
            cp, rp, lc = self._write(td, tables, phys, {"xq_kline_d": "planned"})
            res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 0, res)

    def test_placeholder_without_lifecycle_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tables = {"xq_x": {"store": "", "verification_status": "placeholder_empty",
                               "manifest_status": "not_applicable"}}
            cp, rp, lc = self._write(td, tables, [], {})
            res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 1)
            self.assertTrue(any("placeholder_without_lifecycle" in i for i in res["issues"]))

    def test_sha_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, lc = self._write(td, {}, [], {})
            rp.write_text(json.dumps({"catalog_sha256": "deadbeef", "physical_count": 0,
                                      "display_count": 0, "display_assets": [],
                                      "physical_tables": []}), encoding="utf-8")
            res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 1)
            self.assertIn("registry_catalog_sha_mismatch", res["issues"])

    def test_unparsable_counts_issue_not_crash(self):
        """task-77：physical_count/display_count 为坏值 → issue 而非抛异常。"""
        with tempfile.TemporaryDirectory() as td:
            cp, rp, lc = self._write(td, {}, [], {})
            reg = json.loads(rp.read_text(encoding="utf-8"))
            reg["physical_count"] = "not-a-number"
            reg["display_count"] = {"bad": 1}
            rp.write_text(json.dumps(reg), encoding="utf-8")
            res = m.check(cp, rp, lc)  # 不应抛 TypeError/ValueError
            self.assertEqual(res["code"], 1)
            self.assertTrue(any("physical_count_unparsable" in i for i in res["issues"]))
            self.assertTrue(any("display_count_unparsable" in i for i in res["issues"]))

    def test_relative_store_resolved_from_repo_root(self):
        """task-77：相对 store 按主仓根解析（不依赖调用 cwd）。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "store_rel").mkdir()
            tables = {"daily": {"store": "store_rel", "manifest": "/x",
                                "manifest_status": "closed"}}
            cp, rp, lc = self._write(td, tables, [{"physical_tables": ["daily"]}], {})
            from unittest import mock
            with mock.patch.object(m.common, "REPO", root):
                res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 0, res["issues"])

    def test_lifecycle_table_missing_in_registry_fails(self):
        """task-77：lifecycle 表在 registry 缺失也必须报 issue（不再静默跳过）。"""
        with tempfile.TemporaryDirectory() as td:
            tables = {"planned_t": {"store": str(Path(td) / "nope"),
                                    "manifest_status": "missing"}}
            cp, rp, lc = self._write(td, tables, [], {"planned_t": "planned"})
            res = m.check(cp, rp, lc)
            self.assertEqual(res["code"], 1)
            self.assertTrue(any("lifecycle_table_missing_in_registry:planned_t"
                                in i for i in res["issues"]))


if __name__ == "__main__":
    unittest.main()
