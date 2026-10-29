# -*- coding: utf-8 -*-
"""staging_health 周检单测（临时目录 + 注入体积，避免真造大文件）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import staging_health as m  # noqa: E402


class TestStagingHealth(unittest.TestCase):
    def test_healthy_small(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "history").mkdir()
            (root / "history" / "a.parquet").write_bytes(b"x" * 1024)
            res = m.collect(root)
            self.assertEqual(res["tool"], "staging_health")
            self.assertEqual(res["code"], 0)
            self.assertEqual(res["files"], 1)
            self.assertTrue(any(r["dir"] == "history" for r in res["dirs"]))

    def _collect_with_size(self, root: Path, size: int):
        orig = m.dir_size

        def fake(p: Path):
            n = sum(1 for _ in p.rglob("*") if _.is_file()) if p.is_dir() else 0
            return size, n, 0.0
        m.dir_size = fake
        try:
            return m.collect(root)
        finally:
            m.dir_size = orig

    def test_warn_over_2gb(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "history_3y").mkdir()
            (root / "history_3y" / "a.parquet").write_bytes(b"x")
            res = self._collect_with_size(root, 3 * 1024 ** 3)
            self.assertEqual(res["code"], 1)
            self.assertTrue(any("gt_2GB" in i for i in res["issues"]))
            self.assertTrue(any(r["history_like"] for r in res["dirs"]))

    def test_fail_over_5gb(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "history_3y").mkdir()
            (root / "history_3y" / "a.parquet").write_bytes(b"x")
            res = self._collect_with_size(root, 6 * 1024 ** 3)
            self.assertEqual(res["code"], 2)

    def test_prev_gen_tag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tail_fill_prev_r38").mkdir()
            (root / "tail_fill_prev_r38" / "a.parquet").write_bytes(b"x")
            res = m.collect(root)
            self.assertTrue(any(r["prev_gen"] for r in res["dirs"]))


if __name__ == "__main__":
    unittest.main()
