# -*- coding: utf-8 -*-
"""导入边界联动检查单测（临时模块/测试夹具）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import check_import_boundaries as m  # noqa: E402


class TestImportBoundaries(unittest.TestCase):
    def _mk(self, td, imports, allowed):
        root = Path(td)
        (root / "pkg").mkdir(parents=True, exist_ok=True)
        mod = root / "pkg" / "consumer.py"
        mod.write_text("import %s\n" % "\nimport ".join(imports), encoding="utf-8")
        test = root / "pkg" / "tests" / "test_consumer.py"
        test.parent.mkdir(parents=True, exist_ok=True)
        allowed_src = ", ".join('"%s"' % a for a in allowed)
        test.write_text(
            "def test_whitelist():\n"
            "    source = Path(consumer.__file__)\n"
            "    roots = set()\n"
            "    assert roots <= {%s}\n" % allowed_src,
            encoding="utf-8")
        return {"module": "pkg/consumer.py", "test": "pkg/tests/test_consumer.py"}

    def test_ok_subset(self):
        with tempfile.TemporaryDirectory() as td:
            spec = self._mk(td, ["os", "json"], ["os", "json", "sys"])
            res = m.check([spec], base=Path(td))
            self.assertEqual(res["code"], 0, res)
            self.assertEqual(res["checked"][0]["extra"], [])

    def test_extra_import_fails(self):
        with tempfile.TemporaryDirectory() as td:
            spec = self._mk(td, ["os", "logging"], ["os", "json"])
            res = m.check([spec], base=Path(td))
            self.assertEqual(res["code"], 1)
            self.assertIn("logging", ";".join(res["issues"]))

    def test_missing_files_warn(self):
        with tempfile.TemporaryDirectory() as td:
            res = m.check([{"module": "nope.py", "test": "nope_test.py"}], base=Path(td))
            self.assertEqual(res["code"], 0)
            self.assertTrue(any("boundary_files_missing" in w for w in res["warnings"]))


if __name__ == "__main__":
    unittest.main()
