# -*- coding: utf-8 -*-
"""H1 审计哈希链测试（commercial/audit.py）。"""
import json
import os
import subprocess
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

    # ---- P1（2026-11-04 代码审查）：verify 全量收集错误 ----
    def test_verify_collects_all_errors(self):
        audit.append("a1", tenant="t1", path=self.path)
        audit.append("a2", tenant="t1", path=self.path)
        lines = open(self.path, encoding="utf-8").read().splitlines()
        for i in range(2):
            rec = json.loads(lines[i])
            rec["tenant"] = "hacker%d" % i
            lines[i] = json.dumps(rec, ensure_ascii=False)
        open(self.path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        v = audit.verify(self.path)
        self.assertFalse(v["ok"])
        self.assertEqual(v["total"], 2)
        self.assertEqual(v["checked"], 2)
        self.assertGreaterEqual(len(v["errors"]), 2)

    # ---- P1：并发 append 不再分叉 ----
    def test_concurrent_append_no_fork(self):
        root = os.path.dirname(WT)
        script = (
            "import sys\n"
            "sys.path.insert(0, r'%s')\n"
            "from commercial import audit\n"
            "for i in range(10):\n"
            "    audit.append('c', detail={'i': i}, path=r'%s')\n"
        ) % (root, self.path)
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(4)]
        errs = []
        for p in procs:
            _, err = p.communicate(timeout=120)
            if p.returncode != 0:
                errs.append(err.decode("utf-8", "replace"))
        self.assertEqual(errs, [], errs[:1])
        v = audit.verify(self.path)
        self.assertTrue(v["ok"], v.get("error"))
        self.assertEqual(v["total"], 40)

    # ---- P1：migrate 修复混合账本；混合尾行拒绝追加 ----
    def test_append_refuses_legacy_after_hash(self):
        audit.append("a1", path=self.path)
        legacy = {"ts": "2026-11-04 22:00:00", "tenant": "old", "actor": "x",
                  "action": "legacy", "resource": "", "result": "ok", "detail": {}}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(legacy, ensure_ascii=False) + "\n")
        with self.assertRaises(RuntimeError):
            audit.append("a2", path=self.path)

    def test_migrate_mixed_ledger_then_verify_ok(self):
        legacy = {"ts": "2026-11-02 10:00:00", "tenant": "t0", "actor": "old",
                  "action": "legacy", "resource": "", "result": "ok", "detail": {}}
        open(self.path, "w", encoding="utf-8").write(json.dumps(legacy, ensure_ascii=False) + "\n")
        audit.append("a1", path=self.path)
        audit.append("a2", path=self.path)
        legacy2 = {"ts": "2026-11-04 22:00:00", "tenant": "old2", "actor": "x",
                   "action": "legacy2", "resource": "", "result": "ok", "detail": {}}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(legacy2, ensure_ascii=False) + "\n")
        self.assertFalse(audit.verify(self.path)["ok"])
        r = audit.migrate(self.path)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["migrated"], 4)
        self.assertTrue(os.path.exists(r["backup"]))
        v = audit.verify(self.path)
        self.assertTrue(v["ok"], v.get("error"))
        self.assertEqual(v["hashed"], 4)
        self.assertEqual(v["legacy"], 0)
        audit.append("a3", path=self.path)
        self.assertTrue(audit.verify(self.path)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
