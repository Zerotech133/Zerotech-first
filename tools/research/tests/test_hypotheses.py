# -*- coding: utf-8 -*-
"""hypotheses 台账单测（临时 DB）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.hypotheses as h  # noqa: E402


class TestHypotheses(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "hyp.db"

    def tearDown(self):
        self._td.cleanup()

    def test_init_seed_and_get(self):
        res = h.init(seed=True, db=self.db)
        self.assertTrue(res["ok"])
        self.assertEqual(res["total"], 10)
        rec = h.get("H1", db=self.db)
        self.assertIsNotNone(rec)
        self.assertIn("公告", rec["title"])
        self.assertEqual(rec["status"], "idea")
        self.assertIn("pit", rec["gates"])

    def test_add_duplicate(self):
        h.init(seed=False, db=self.db)
        self.assertTrue(h.add("H99", "测试", db=self.db)["ok"])
        self.assertFalse(h.add("H99", "重复", db=self.db)["ok"])

    def test_update_and_list(self):
        h.init(seed=True, db=self.db)
        r = h.update("H1", db=self.db, status="in_progress",
                     conclusion="初步证据", evidence_path="research/reports/x.md")
        self.assertTrue(r["ok"])
        rows = h.rows("in_progress", db=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conclusion"], "初步证据")

    def test_update_unknown_id(self):
        h.init(seed=False, db=self.db)
        self.assertFalse(h.update("H404", db=self.db, status="rejected")["ok"])


if __name__ == "__main__":
    unittest.main()
