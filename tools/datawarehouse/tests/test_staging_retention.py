# -*- coding: utf-8 -*-
"""staging_health 保留窗口/陈旧锁单测（2026-10-28 增强）。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import staging_health as m  # noqa: E402


class TestStagingRetention(unittest.TestCase):
    def test_old_prev_gen_reclaim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "tail_fill_prev_r38"
            d.mkdir()
            old = time.time() - (m.PREV_KEEP_DAYS + 10) * 86400
            (d / "a.parquet").write_bytes(b"x")
            os.utime(d / "a.parquet", (old, old))
            os.utime(d, (old, old))
            res = m.collect(root)
            self.assertTrue(any("prev_gen_reclaim" in i for i in res["issues"]), res["issues"])
            self.assertEqual(res["retention"]["prev_gen_keep_days"], m.PREV_KEEP_DAYS)

    def test_stale_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = root / ".warehouse-publication.lock"
            lock.write_bytes(b"")
            old = time.time() - (m.LOCK_STALE_DAYS + 3) * 86400
            os.utime(lock, (old, old))
            res = m.collect(root)
            self.assertTrue(any("stale_publication_lock" in i for i in res["issues"]))

    def test_fresh_gen_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "history"
            d.mkdir()
            (d / "a.parquet").write_bytes(b"x")
            res = m.collect(root)
            self.assertEqual(res["code"], 0, res["issues"])

    def test_unpublished_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "history_3y"
            d.mkdir()
            (d / "run_manifest.json").write_text(
                '{"production_write": false, "global_activation": false}', encoding="utf-8")
            old = time.time() - (m.HISTORY_KEEP_DAYS + 5) * 86400
            os.utime(d / "run_manifest.json", (old, old))
            os.utime(d, (old, old))
            res = m.collect(root)
            self.assertTrue(any("unpublished_candidates_stale" in i or
                                "history_dir_older_than" in i for i in res["issues"]))


if __name__ == "__main__":
    unittest.main()
