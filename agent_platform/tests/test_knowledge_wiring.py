"""M2-P2 统一记忆入口测试。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclass 字符串注解判例
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "agent_platform"))

kw = _load("kw", "agent_platform/domains/knowledge_wiring.py")
from engine.approvals import ApprovalStore  # noqa: E402
from engine.memory import MemoryStore       # noqa: E402


class KnowledgeWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # 复制真源结构到 tmp（数据文件路径由模块常量派生，测试用
        # monkeypatch 方式注入 root：kw 的函数都接受 root 参数，
        # 但 PENDING_FILE/APPROVALS_DB 是模块级常量 → 逐个 patch）
        self._orig = (kw.PENDING_FILE, kw.MEMORY_BASE, kw.APPROVALS_DB)
        kw.PENDING_FILE = self.root / "data" / "knowledge_pending.jsonl"
        kw.MEMORY_BASE = self.root / "data" / "memory"
        kw.APPROVALS_DB = self.root / "data" / "approvals" / "approvals.db"
        kw.PENDING_FILE.parent.mkdir(parents=True)
        self.entry = {"kind": "lesson", "dept": "invest",
                      "content": "回测滑点必须按流动性分层设置",
                      "src": "晨会.md", "ts": "2026-10-17"}
        self._append_pending(self.entry)
        self.store = ApprovalStore(kw.APPROVALS_DB)

    def tearDown(self):
        self.store.close()
        kw.PENDING_FILE, kw.MEMORY_BASE, kw.APPROVALS_DB = self._orig
        self.tmp.cleanup()

    def _append_pending(self, entry):
        with open(kw.PENDING_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _mem_count(self):
        ms = MemoryStore(kw.MEMORY_BASE, enforce=False)
        try:
            return len(ms.read("wiring", kw.GNS_KNOWLEDGE, limit=1000))
        finally:
            pass

    # ------------------------------------------------------------------
    def test_load_pending_with_hash(self):
        rows = kw.load_pending(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_hash"], kw.candidate_hash(self.entry))

    def test_promote_writes_memory_with_credential(self):
        h = kw.candidate_hash(self.entry)
        apv, rec, replayed = kw.promote(self.root, h, by="laotian",
                                        store=self.store)
        self.assertFalse(replayed)
        self.assertIn("candidate_hash", json.dumps(rec, ensure_ascii=False))
        self.assertEqual(self._mem_count(), 1)      # 生产记忆 1 条

    def test_promote_idempotent_replay(self):
        h = kw.candidate_hash(self.entry)
        kw.promote(self.root, h, by="laotian", store=self.store)
        apv, rec, replayed = kw.promote(self.root, h, by="laotian",
                                        store=self.store)
        self.assertTrue(replayed)
        self.assertEqual(rec, {})
        self.assertEqual(self._mem_count(), 1)      # 零副作用

    def test_catchup_missing_memory(self):
        """凭据在而记忆缺（崩溃窗口）→ 重提补写。"""
        h = kw.candidate_hash(self.entry)
        kw.promote(self.root, h, by="laotian", store=self.store)
        # 模拟记忆丢失：重建空 memory 目录
        shutil.rmtree(kw.MEMORY_BASE)
        apv, rec, replayed = kw.promote(self.root, h, by="laotian",
                                        store=self.store)
        self.assertFalse(replayed)
        self.assertEqual(self._mem_count(), 1)      # catch-up 补写

    def test_promote_unknown_hash_rejected(self):
        with self.assertRaises(kw.KnowledgeWiringError):
            kw.promote(self.root, "nonexistent", by="laotian",
                       store=self.store)

    def test_promote_kind_whitelist(self):
        bad = {"kind": "gossip", "dept": "x", "content": "八卦"}
        self._append_pending(bad)
        h = kw.candidate_hash(bad)
        with self.assertRaises(kw.KnowledgeWiringError):
            kw.promote(self.root, h, by="laotian", store=self.store)
        self.assertEqual(self._mem_count(), 0)

    def test_reject_no_memory_write(self):
        h = kw.candidate_hash(self.entry)
        apv, replayed = kw.reject(self.root, h, by="laotian", reason="不可证伪",
                                  store=self.store)
        self.assertEqual(self._mem_count(), 0)
        rep = kw.audit_report(self.root)
        states = [s["state"] for s in rep["states"]]
        self.assertEqual(states, ["rejected"])

    def test_audit_bypass_detection(self):
        """红线：直写生产记忆无凭据 → audit FAIL。"""
        ms = MemoryStore(kw.MEMORY_BASE, enforce=False)
        ms.write("rogue-agent", kw.GNS_KNOWLEDGE,
                 {"content": "绕门知识", "candidate_hash": "fake123"})
        rep = kw.audit_report(self.root)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(rep["bypass_count"], 1)

    def test_audit_empty_pool_pass(self):
        rep = kw.audit_report(self.root)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["pending_total"], 1)
        self.assertEqual(rep["states"][0]["state"], "pending_review")


if __name__ == "__main__":
    unittest.main()
