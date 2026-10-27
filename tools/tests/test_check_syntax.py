# -*- coding: utf-8 -*-
"""check_syntax 单测（临时 repo 夹具，禁用缓存写入）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import check_syntax as m  # noqa: E402


class TestCheckSyntax(unittest.TestCase):
    def setUp(self):
        self._cache = m.CACHE
        self._td = tempfile.TemporaryDirectory()
        m.CACHE = Path(self._td.name) / "cache.json"

    def tearDown(self):
        m.CACHE = self._cache
        self._td.cleanup()

    def test_good_file(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "good.py").write_text("x = 1\n", encoding="utf-8")
            res = m.check(["good.py"], base=Path(td))
            self.assertEqual(res["code"], 0)
            self.assertEqual(res["error_count"], 0)

    def test_syntax_error(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")
            res = m.check(["bad.py"], base=Path(td))
            self.assertEqual(res["code"], 1)
            self.assertEqual(res["error_count"], 1)

    def test_warning_fail_on_warning(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "warn.py").write_text('x = "\\d"\n', encoding="utf-8")
            res_ok = m.check(["warn.py"], base=Path(td), fail_on_warning=False)
            self.assertEqual(res_ok["code"], 0)
            self.assertGreaterEqual(res_ok["warning_count"], 1)
            res = m.check(["warn.py"], base=Path(td), fail_on_warning=True)
            self.assertEqual(res["code"], 1)


if __name__ == "__main__":
    unittest.main()
