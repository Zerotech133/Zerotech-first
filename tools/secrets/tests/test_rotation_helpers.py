# -*- coding: utf-8 -*-
"""mcp_preflight / rotate_token 纯函数单测（不联网、不碰真实保险库）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SECRETS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SECRETS))

import mcp_preflight  # noqa: E402
import rotate_token  # noqa: E402


class TestTokenShape(unittest.TestCase):
    def test_too_short(self):
        ok, _ = rotate_token.token_shape_ok("abc")
        self.assertFalse(ok)

    def test_ok(self):
        ok, why = rotate_token.token_shape_ok("a" * 64)
        self.assertTrue(ok)
        self.assertEqual(why, "ok")

    def test_too_long(self):
        ok, _ = rotate_token.token_shape_ok("a" * 200)
        self.assertFalse(ok)


class TestInspectConfig(unittest.TestCase):
    def _write(self, d: Path, payload) -> Path:
        p = d / ".mcp.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write(Path(td), {"mcpServers": {"tushareMcp": {
                "url": "https://api.tushare.pro/mcp/?token=${TUSHARE_MCP_TOKEN}"}}})
            st = mcp_preflight.inspect_config(p)
            self.assertTrue(st["valid_json"])
            self.assertTrue(st["placeholder"])
            self.assertFalse(st["token_present"])
            self.assertEqual(st["state"], "placeholder")

    def test_resolved(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write(Path(td), {"mcpServers": {"tushareMcp": {
                "url": "https://api.tushare.pro/mcp/?token=deadbeef"}}})
            st = mcp_preflight.inspect_config(p)
            self.assertEqual(st["state"], "resolved")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as td:
            st = mcp_preflight.inspect_config(Path(td) / "nope.json")
            self.assertFalse(st["valid_json"])
            self.assertEqual(st["state"], "missing_or_bad")

    def test_bad_json(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".mcp.json"
            p.write_text("{not json", encoding="utf-8")
            st = mcp_preflight.inspect_config(p)
            self.assertFalse(st["valid_json"])

    def test_no_tushare(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write(Path(td), {"mcpServers": {}})
            st = mcp_preflight.inspect_config(p)
            self.assertEqual(st["state"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
