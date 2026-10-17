# -*- coding: utf-8 -*-
"""_audit_credential_trace 交叉校验测试（tmp 独立仓，不碰真库）。

覆盖 M1 审查收官判例「凭据轮次 vs 痕迹」：
- 全对账 PASS：真源 last_wiring_key 与账本凭据一一对应；
- trace_key_missing：真源引用账本不存在的键；
- credential_ahead：最新凭据未被真源消费且状态错位（崩溃窗口）；
- credential_without_entry：真源有标记但账本无凭据；
- source_unreadable：真源 JSON 损坏如实报告。
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ida = _load("idempotency_audit_under_test",
            "agent_platform/adapters/idempotency_audit.py")
apv = _load("approvals_under_test_ct", "agent_platform/engine/approvals.py")


def _seed(root: Path, gzh=None, invest=None, devtask=None):
    (root / "gzh" / "drafts").mkdir(parents=True, exist_ok=True)
    (root / "gzh" / "drafts" / "index.json").write_text(
        json.dumps(gzh or [], ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "agent_platform" / "data" / "invest").mkdir(
        parents=True, exist_ok=True)
    (root / "agent_platform" / "data" / "invest" / "runs_index.json"
     ).write_text(json.dumps(invest or [], ensure_ascii=False, indent=1),
                  encoding="utf-8")
    (root / ".worktrees").mkdir(parents=True, exist_ok=True)
    (root / ".worktrees" / "tasks.json").write_text(
        json.dumps(devtask or {}, ensure_ascii=False, indent=1),
        encoding="utf-8")


def _credential(root: Path, target_id: str, to_status: str, key: str):
    db = (root / "agent_platform" / "data" / "approvals" / "approvals.db")
    db.parent.mkdir(parents=True, exist_ok=True)
    store = apv.ApprovalStore(db)
    try:
        store.record(apv.Approval(
            target_type="workitem", target_id=target_id,
            from_status="draft", to_status=to_status,
            decision="approved", decided_by="测试", reason="",
            decided_at="2026-10-17T12:00:00+08:00",
            idempotency_key=key))
    finally:
        store.close()


class CredentialTraceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _trace(self):
        return ida._audit_credential_trace(self.root)

    def test_all_aligned_pass(self):
        _seed(self.root,
              gzh=[{"id": "d1", "status": "reviewed",
                    "last_wiring_key": "gzh:d1:draft>reviewed"}],
              invest=[{"run_id": "r1", "status": "reviewed",
                       "last_wiring_key": "invest:r1:drafted>reviewed"}],
              devtask={"K-T1": {"status": "done",
                                "last_wiring_key":
                                    "devtask:K-T1:review>done"}})
        _credential(self.root, "gzh:d1", "reviewed",
                    "gzh:d1:draft>reviewed")
        _credential(self.root, "invest:r1", "reviewed",
                    "invest:r1:drafted>reviewed")
        _credential(self.root, "devtask:K-T1", "done",
                    "devtask:K-T1:review>done")
        tr = self._trace()
        self.assertEqual(tr["status"], "ok")
        self.assertEqual(tr["checks"], 3)
        self.assertEqual(tr["violations"], [])
        self.assertEqual(tr["warnings"], [])

    def test_trace_key_missing(self):
        """真源引用账本不存在的键 → FAIL。"""
        _seed(self.root,
              gzh=[{"id": "d1", "status": "reviewed",
                    "last_wiring_key": "gzh:d1:draft>reviewed"}])
        tr = self._trace()
        self.assertEqual(tr["status"], "violations_found")
        self.assertTrue(any(v["kind"] == "trace_key_missing"
                            for v in tr["violations"]))

    def test_credential_ahead_warn(self):
        """账本最新凭据未被消费且状态错位 → WARN（崩溃窗口/外部改动）。"""
        _seed(self.root,
              gzh=[{"id": "d1", "status": "draft",
                    "last_wiring_key": "gzh:d1:draft>draft"}])
        _credential(self.root, "gzh:d1", "draft",
                    "gzh:d1:draft>draft")      # 被消费的旧凭据（rowid 靠前）
        _credential(self.root, "gzh:d1", "reviewed",
                    "gzh:d1:draft>reviewed")   # 凭据领先真源（rowid 靠后=最新）
        tr = self._trace()
        self.assertTrue(any(w["kind"] == "credential_ahead"
                            for w in tr["warnings"]))

    def test_credential_without_entry(self):
        """真源有消费标记但账本无该 target 任何凭据 → FAIL。"""
        _seed(self.root,
              invest=[{"run_id": "r1", "status": "reviewed",
                       "last_wiring_key": "invest:r1:drafted>reviewed"}])
        tr = self._trace()
        self.assertEqual(tr["status"], "violations_found")
        kinds = [v["kind"] for v in tr["violations"]]
        self.assertIn("credential_without_entry", kinds)
        self.assertIn("trace_key_missing", kinds)

    def test_source_unreadable(self):
        """真源 JSON 损坏 → source_error + source_unreadable。"""
        _seed(self.root, gzh=None, invest=None, devtask=None)
        p = self.root / "gzh" / "drafts" / "index.json"
        p.write_text("{broken json", encoding="utf-8")
        tr = self._trace()
        self.assertEqual(tr["status"], "source_error")
        self.assertTrue(any(v["kind"] == "source_unreadable"
                            for v in tr["violations"]))

    def test_build_report_integrates_trace(self):
        _seed(self.root, devtask={"K-T1": {"status": "done"}})
        rep = ida.build_report(self.root)
        self.assertIn("credential_trace", rep["sources"])
        self.assertEqual(rep["sources"]["credential_trace"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
