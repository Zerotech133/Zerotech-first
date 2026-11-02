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

    def test_apply_skips_unknown_rows(self):
        """task-77：rows=None（footer 扫描截断/失败）不得写成 done=true。"""
        with tempfile.TemporaryDirectory() as td:
            mf = Path(td) / "_manifest.json"
            mf.write_text(json.dumps({"old": {"done": True}}), encoding="utf-8")
            rows = [
                {"table": "ok_t", "manifest": str(mf), "files": 2, "rows": 6},
                {"table": "unknown_t", "manifest": str(mf), "files": 2, "rows": None},
            ]
            res = mr.apply(rows, kg_log=False)
            self.assertTrue(res["ok"])
            self.assertEqual(res["changed_tables"], ["ok_t"])
            self.assertEqual([x["table"] for x in res["skipped_rows_unknown"]],
                             ["unknown_t"])
            data = json.loads(mf.read_text(encoding="utf-8"))
            self.assertIn("ok_t", data)
            self.assertNotIn("unknown_t", data)  # 不写未知行数
            self.assertIn("old", data)

    def test_apply_partial_failure_reports_and_continues(self):
        """task-77：某 manifest 读失败不再提前返回丢弃已完成写入；errors 可见。"""
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good_manifest.json"
            good.write_text(json.dumps({"t1": {}}), encoding="utf-8")
            bad = Path(td) / "bad_manifest.json"
            bad.write_text("{not json", encoding="utf-8")
            rows = [
                {"table": "t1", "manifest": str(good), "files": 1, "rows": 3},
                {"table": "t2", "manifest": str(bad), "files": 1, "rows": 4},
            ]
            res = mr.apply(rows, kg_log=False)
            self.assertFalse(res["ok"])
            self.assertIn("t1", res["changed_tables"])  # 好文件已写
            self.assertTrue(res["errors"])
            self.assertEqual(res["errors"][0]["stage"], "read")
            self.assertIn("t1", json.loads(good.read_text(encoding="utf-8")))

    def test_apply_write_error_fail_visible(self):
        """task-77：os.replace 失败 → ok=False + stage=write（不抛未捕获异常）。"""
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            mf = Path(td) / "_manifest.json"
            mf.write_text(json.dumps({"t1": {}}), encoding="utf-8")
            rows = [{"table": "t1", "manifest": str(mf), "files": 1, "rows": 3}]
            with mock.patch.object(mr.os, "replace", side_effect=OSError("disk full")):
                res = mr.apply(rows, kg_log=False)
            self.assertFalse(res["ok"])
            self.assertEqual(res["errors"][0]["stage"], "write")
            self.assertIn("disk full", res["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
