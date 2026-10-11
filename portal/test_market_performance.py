"""/market 只读绩效账本的契约测试。"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PORTAL_DIR = Path(__file__).resolve().parent
if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))
try:
    market_performance = importlib.import_module("market_performance")
except ModuleNotFoundError:
    market_performance = None


class MarketPerformanceTests(unittest.TestCase):
    def test_project_returns_readonly_ledger_projection(self):
        self.assertIsNotNone(market_performance, "缺少 market_performance 数据层")
        if market_performance is None:
            return
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps({
                "generated_at": "2026-10-11 00:51:07",
                "asof": "20261009",
                "caliber": {"id": "exec-d2-open-20261010"},
                "totals": {"picks": 2, "1": {"n": 0, "hit": None, "avg": None}},
                "per_tier": {"T1": {"n": 2}},
                "per_strategy": {"chanlun": {"n": 2}},
            }), encoding="utf-8")
            result = market_performance.project(ledger)
        self.assertTrue(result["ok"])
        self.assertEqual(result["asof"], "20261009")
        self.assertEqual(result["totals"]["picks"], 2)
        self.assertEqual(result["per_strategy"]["chanlun"]["n"], 2)
        self.assertFalse(result["financial_execution_enabled"])

    def test_project_rejects_missing_and_invalid_ledgers(self):
        self.assertIsNotNone(market_performance, "缺少 market_performance 数据层")
        if market_performance is None:
            return
        with self.assertRaises(FileNotFoundError):
            market_performance.project(Path("missing-performance-ledger.json"))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                market_performance.project(bad)

    def test_page_and_routes_are_wired_read_only(self):
        root = Path(__file__).resolve().parent
        page = root / "market.html"
        nginx = (root / "nginx.conf").read_text(encoding="utf-8")
        api = (root / "portal_api.py").read_text(encoding="utf-8-sig")
        nav = (root / "static" / "nav.js").read_text(encoding="utf-8")
        self.assertTrue(page.is_file(), "缺少 /market 静态页面")
        if not page.is_file():
            return
        html = page.read_text(encoding="utf-8")
        self.assertIn('/static/tokens.css', html)
        self.assertIn('/static/nav.js', html)
        self.assertIn('/static/shortscreen.js', html)
        self.assertIn('/api/market_performance', html)
        self.assertIn('location = /market', nginx)
        self.assertIn('path == "/api/market_performance"', api)
        self.assertIn('"/market"', nav)
        self.assertNotIn('/api/portfolio_subscribe', html)
        self.assertNotIn('/api/paper_buy', html)


if __name__ == "__main__":
    unittest.main()
