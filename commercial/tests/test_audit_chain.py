# -*- coding: utf-8 -*-
"""H1 审计哈希链测试（commercial/audit.py）。"""
import json
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from commercial import audit  # noqa: E402


class TestAuditChain(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.td.name, "audit.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def test_append_and_verify(self):
        r1 = audit.append("a1", tenant="t1", path=self.path)
        r2 = audit.append("a2", tenant="t1", path=self.path)
        self.assertTrue(r1["hash"])
        self.assertEqual(r2["prev_hash"], r1["hash"])
        v = audit.verify(self.path)
        self.assertTrue(v["ok"], v.get("error"))
        self.assertEqual(v["hashed"], 2)
        self.assertEqual(v["last_hash"], r2["hash"])

    def test_tamper_detected(self):
        audit.append("a1", tenant="t1", path=self.path)
        audit.append("a2", tenant="t1", path=self.path)
        lines = open(self.path, encoding="utf-8").read().splitlines()
        rec = json.loads(lines[0])
        rec["tenant"] = "hacker"
        lines[0] = json.dumps(rec, ensure_ascii=False)
        open(self.path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        v = audit.verify(self.path)
        self.assertFalse(v["ok"])
        self.assertIn("hash", (v.get("error") or ""))

    def test_legacy_prefix_allowed(self):
        legacy = {"ts": "2026-11-02 10:00:00", "tenant": "t0", "actor": "old",
                  "action": "legacy", "resource": "", "result": "ok", "detail": {}}
        open(self.path, "w", encoding="utf-8").write(json.dumps(legacy, ensure_ascii=False) + "\n")
        audit.append("new", tenant="t1", path=self.path)
        v = audit.verify(self.path)
        self.assertTrue(v["ok"], v.get("error"))
        self.assertEqual(v["legacy"], 1)
        self.assertEqual(v["hashed"], 1)

    def test_append_after_legacy_chains_ok(self):
        audit.append("a1", path=self.path)
        audit.append("a2", path=self.path)
        v1 = audit.verify(self.path)
        self.assertTrue(v1["ok"])
        audit.append("a3", path=self.path)
        v2 = audit.verify(self.path)
        self.assertTrue(v2["ok"], v2.get("error"))
        self.assertEqual(v2["hashed"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
