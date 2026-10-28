# -*- coding: utf-8 -*-
"""trace_link shadow-only 补链（replay_trace 锚点修复）契约。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from adapters.trace_link import (  # noqa: E402
    LinkStore,
    replay_trace,
    record_shadow_anchor,
    sync_shadow_links,
)


class TraceLinkShadowSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ledger = self.tmp / "shadow_ledger.jsonl"
        self.links = self.tmp / "trace" / "trace_links.jsonl"

    def _row(self, run_id="66a22a8f6901", status="done", symbol="600036"):
        return {
            "date": "2026-10-27",
            "symbol": symbol,
            "status": status,
            "run_id": run_id,
        }

    def test_record_shadow_anchor_and_replay(self):
        r = record_shadow_anchor(self._row(), path=self.links, source="test")
        self.assertEqual(r["status"], "recorded")
        rep = replay_trace(LinkStore(self.links), "66a22a8f6901")
        self.assertEqual(rep["verdict"], "OK")
        self.assertEqual(len(rep["matches"]), 1)
        self.assertEqual(rep["matches"][0]["chain"]["shadow"]["symbol"], "600036")

    def test_failed_row_skipped(self):
        r = record_shadow_anchor(self._row(status="failed"), path=self.links)
        self.assertEqual(r["status"], "skipped")
        self.assertFalse(self.links.exists())

    def test_sync_shadow_links_idempotent(self):
        self.ledger.write_text(
            json.dumps(self._row("aaaa11112222")) + "\n"
            + json.dumps(self._row("bbbb33334444", symbol="300750")) + "\n"
            + json.dumps(self._row("cccc55556666", status="failed")) + "\n",
            encoding="utf-8",
        )
        r1 = sync_shadow_links(ledger=self.ledger, path=self.links)
        self.assertEqual(r1["recorded"], 2)
        self.assertEqual(r1["skipped"], 0)
        r2 = sync_shadow_links(ledger=self.ledger, path=self.links)
        self.assertEqual(r2["recorded"], 0)
        self.assertEqual(r2["replayed"], 2)
        self.assertTrue(replay_trace(LinkStore(self.links), "bbbb33334444")["matches"])


if __name__ == "__main__":
    unittest.main()
