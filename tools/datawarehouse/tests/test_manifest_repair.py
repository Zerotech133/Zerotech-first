# -*- coding: utf-8 -*-
"""manifest_repair 纯函数单测：扫描/清单路径/合并写入（临时目录夹具）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import manifest_repair as mr  # noqa: E402


def _write_parquet(path: Path, n: int = 3) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    tbl = pa.table({"trade_date": ["20261001"] * n, "x": list(range(n))})
    pq.write_table(tbl, path)


class TestManifestRepair(unittest.TestCase):
    def test_scan_store(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "daily"
            store.mkdir()
            for i in range(2):
                _write_parquet(store / ("part_%04d.parquet" % i), 3)
            info = mr.scan_store(store)
            self.assertEqual(info["files"], 2)
            self.assertEqual(info["rows"], 6)

    def test_find_manifest_path(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "daily"
            store.mkdir()
            mf = Path(td) / "_manifest.json"
            mf.write_text("{}", encoding="utf-8")
            self.assertEqual(mr.find_manifest_path({"manifest": str(mf)}, store), mf)
            self.assertEqual(mr.find_manifest_path({}, store), mf)
            mf.unlink()
            self.assertIsNone(mr.find_manifest_path({}, store))

    def test_apply_merges_entries(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "daily"
            store.mkdir()
            mf = Path(td) / "_manifest.json"
            mf.write_text(json.dumps({"old_table": {"done": True, "rows": 1}}), encoding="utf-8")
            rows = [{"table": "new_table", "manifest": str(mf), "files": 2, "rows": 6}]
            res = mr.apply(rows, kg_log=False)
            self.assertTrue(res["ok"])
            self.assertEqual(res["changed_tables"], ["new_table"])
            data = json.loads(mf.read_text(encoding="utf-8"))
            self.assertTrue(data["new_table"]["done"])
            self.assertEqual(data["new_table"]["rows"], 6)
            self.assertIn("old_table", data)  # 不丢旧键
            self.assertTrue(list(Path(td).glob("_manifest.json.bak-*")))

    def test_apply_double_shell(self):
        with tempfile.TemporaryDirectory() as td:
            mf = Path(td) / "_manifest.json"
            mf.write_text(json.dumps({"version": 1, "tables": {}}), encoding="utf-8")
            rows = [{"table": "t1", "manifest": str(mf), "files": 1, "rows": 2}]
            mr.apply(rows, kg_log=False)
            data = json.loads(mf.read_text(encoding="utf-8"))
            self.assertIn("t1", data["tables"])


if __name__ == "__main__":
    unittest.main()
