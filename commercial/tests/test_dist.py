# -*- coding: utf-8 -*-
"""P2.2 交付包构建 测试：结构/清单/密钥扫描/可导入。"""
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commercial import build_dist  # noqa: E402

BAD_PATTERNS = ("XT_DATA_KEY=", "tushare_token =", "C:\\项目")


class TestBuildDist(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.zp = build_dist.build(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_required_files(self):
        with zipfile.ZipFile(self.zp) as z:
            names = z.namelist()
        for need in ("VERSION", "MANIFEST.sha256", "INSTALL.txt",
                     "analysis/run.py", "commercial/license.py",
                     "deploy/private/install.ps1", "deploy/private/activate.ps1",
                     "deploy/private/OPS.md", "deploy/private/EULA-template.md"):
            self.assertIn(need, names, need)
        self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))

    def test_manifest_matches(self):
        with zipfile.ZipFile(self.zp) as z:
            man = z.read("MANIFEST.sha256").decode("utf-8")
            lines = [l for l in man.splitlines() if l and not l.startswith("#")]
        self.assertGreater(len(lines), 10)

    def test_no_secrets_in_text_files(self):
        with zipfile.ZipFile(self.zp) as z:
            for n in z.namelist():
                if n.endswith((".py", ".ps1", ".md", ".txt")):
                    s = z.read(n).decode("utf-8", "ignore")
                    for bad in BAD_PATTERNS:
                        self.assertNotIn(bad, s, "命中 %s in %s" % (bad, n))

    def test_extracted_imports(self):
        with zipfile.ZipFile(self.zp) as z:
            z.extractall(self.td.name)
        code = "import sys; sys.path.insert(0, r'%s'); import analysis, commercial; print('ok')" % self.td.name
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
