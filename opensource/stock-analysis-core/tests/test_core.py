# -*- coding: utf-8 -*-
"""端到端测试：合成数据 → 流水线 → 报告；并做"无密钥/无硬编码路径/无网络"自检。"""
import csv
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stock_analysis_core import sample  # noqa: E402
from stock_analysis_core.pipeline import Pipeline  # noqa: E402


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        sample.write_sample(os.path.join(self.td.name, "data"))

    def tearDown(self):
        self.td.cleanup()

    def test_end_to_end(self):
        out = os.path.join(self.td.name, "out")
        r = Pipeline().run("SAMPLE001", os.path.join(self.td.name, "data"), out)
        self.assertTrue(os.path.exists(os.path.join(out, "SAMPLE001", "report.md")))
        self.assertIn(r["verdict"]["rating"], ("买入", "观察偏多", "观察", "事件观察", "回避"))
        self.assertGreaterEqual(len(r["checks"]), 4)
        self.assertTrue(all("passed" in c for c in r["checks"]))
        md = open(os.path.join(out, "SAMPLE001", "report.md"), encoding="utf-8").read()
        for k in ("结论摘要", "财务", "估值", "风险", "不构成投资建议", "质量门"):
            self.assertIn(k, md)

    def test_sample_csv_fields(self):
        p = os.path.join(self.td.name, "data", "kline.csv")
        with open(p, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows and {"code", "date", "close"} <= set(rows[0].keys()))


class TestHygiene(unittest.TestCase):
    def test_no_secrets_or_hardcoded_paths(self):
        bad = re.compile(r"(C:\\\\项目|XT_DATA_KEY|tushare_token|BEGIN RSA|api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9]{16,})")
        hits = []
        for root, _dirs, files in os.walk(os.path.join(ROOT, "stock_analysis_core")):
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(root, f)
                s = open(p, encoding="utf-8", errors="ignore").read()
                if bad.search(s):
                    hits.append(p)
        self.assertEqual(hits, [], "发现敏感/硬编码内容：%s" % hits)

    def test_no_network_imports(self):
        banned = ("requests", "urllib.request", "http.client", "aiohttp")
        hits = []
        for root, _dirs, files in os.walk(os.path.join(ROOT, "stock_analysis_core")):
            for f in files:
                if not f.endswith(".py"):
                    continue
                s = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                for b in banned:
                    if ("import " + b) in s:
                        hits.append((f, b))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
