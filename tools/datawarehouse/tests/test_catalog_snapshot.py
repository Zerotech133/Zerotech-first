# -*- coding: utf-8 -*-
"""catalog_snapshot 审查回归测试（2026-11-02 task-77）。

覆盖：相对 store（按主仓根解析，不依赖 cwd）、registry generated_at 为 None/非法不再崩溃、
文件数漂移分级。全部 tempfile 夹具，不读写生产数据根。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import common  # noqa: E402
import catalog_snapshot as cs  # noqa: E402


def _make_env(td: Path, generated_at="2026-11-01T00:00:00+08:00"):
    store = td / "store_rel"
    store.mkdir()
    (store / "part_0000.parquet").write_bytes(b"")
    (store / "part_0001.parquet").write_bytes(b"")
    cat = {"version": 28, "updated": "x", "tables": {"t1": {
        "store": "store_rel", "layout": "part", "files": 2, "source": "tdx"}}}
    cp = td / "catalog.json"
    cp.write_text(json.dumps(cat), encoding="utf-8")
    sha = __import__("hashlib").sha256(cp.read_bytes()).hexdigest()
    reg = {"generated_at": generated_at, "catalog_sha256": sha,
           "physical_count": 1, "display_count": 0, "display_assets": [],
           "physical_tables": [{"qualification_status": "unknown"}]}
    rp = td / "registry.json"
    rp.write_text(json.dumps(reg), encoding="utf-8")
    return cp, rp


class TestCatalogSnapshotReview(unittest.TestCase):
    def test_relative_store_resolved_from_repo_root(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp = _make_env(Path(td))
            with mock.patch.object(common, "CATALOG", cp), \
                    mock.patch.object(common, "REGISTRY", rp), \
                    mock.patch.object(common, "REPO", Path(td)):
                res = cs.collect(limit=10)
            self.assertEqual(res["code"], 0, res.get("issues"))
            self.assertEqual(res["drift_sample"]["missing_stores"], [])
            self.assertEqual(res["drift_sample"]["file_count_mismatch"], [])

    def test_generated_at_none_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp = _make_env(Path(td), generated_at=None)
            with mock.patch.object(common, "CATALOG", cp), \
                    mock.patch.object(common, "REGISTRY", rp), \
                    mock.patch.object(common, "REPO", Path(td)):
                res = cs.collect(limit=10)  # 不应抛 TypeError
            self.assertEqual(res["code"], 1)
            self.assertTrue(any("generated_at" in i for i in res["issues"]))

    def test_missing_store_reported(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp = _make_env(Path(td))
            (Path(td) / "store_rel").rename(Path(td) / "gone")
            with mock.patch.object(common, "CATALOG", cp), \
                    mock.patch.object(common, "REGISTRY", rp), \
                    mock.patch.object(common, "REPO", Path(td)):
                res = cs.collect(limit=10)
            self.assertEqual(res["code"], 1)
            self.assertIn("t1", res["drift_sample"]["missing_stores"])


if __name__ == "__main__":
    unittest.main()
