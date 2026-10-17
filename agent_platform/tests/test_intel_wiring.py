# -*- coding: utf-8 -*-
"""intel_wiring（情报裁决域）测试。

判例纪律：
- adopt/reject/audit 全传 tmp 独立 ApprovalStore + tmp root（不传落真库
  跨用例污染，M1 判例手册）；
- MemoryStore 同样落 tmp root data/memory；
- 空转状态（池空）必须 PASS——骨架上线契约。
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TEST_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _TEST_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

spec = importlib.util.spec_from_file_location(
    "intel_wiring_under_test", _AGENT_PLATFORM / "domains" / "intel_wiring.py")
IW = importlib.util.module_from_spec(spec)
sys.modules["intel_wiring_under_test"] = IW
spec.loader.exec_module(IW)

from engine.approvals import ApprovalStore    # noqa: E402
from engine.memory import MemoryStore         # noqa: E402


def _entry(vid="parquet_tdx_ts_daily:jr-001#r1", **kw):
    base = {
        "verdict_id": vid,
        "table_urn": "parquet:tdx/ts_daily",
        "job_run_id": "jr-001",
        "break_kind": "broken",
        "decision": "degrade",
        "impact": ["选股中心 3 策略", "晨报板块 2 项"],
        "rationale": "下游 5 项情报依赖此表，建议降级展示",
        "ts": "2026-10-17T16:00:00",
    }
    base.update(kw)
    return base


class IntelWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.store = ApprovalStore(
            self.root / "data" / "approvals" / "approvals.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ── 起草（建议权，无凭据）──
    def test_draft_appends_and_validates(self):
        rec = IW.draft(self.root, _entry(), by="agent-hermes")
        self.assertEqual(rec["verdict_id"], _entry()["verdict_id"])
        self.assertEqual(len(IW.load_pool(self.root)), 1)
        with self.assertRaises(IW.IntelWiringError):
            IW.draft(self.root, _entry(), by="agent-hermes")  # 重复 vid
        with self.assertRaises(IW.IntelWiringError):
            IW.draft(self.root, _entry(vid="x#r2", decision="delete"),
                     by="a")
        with self.assertRaises(IW.IntelWiringError):
            IW.draft(self.root, _entry(vid="y#r2", rationale=""),
                     by="a")  # 必填缺失

    def test_verdict_id_for_normalizes_urn(self):
        self.assertEqual(
            IW.verdict_id_for("parquet:tdx/ts_daily", "jr-9"),
            "parquet_tdx_ts_daily:jr-9#r1")
        self.assertEqual(
            IW.verdict_id_for("a:b", "j", rnd=2), "a_b:j#r2")

    # ── adopt：凭据先落 + 幂等重放 + catch-up ──
    def test_adopt_roundtrip_and_replay(self):
        IW.draft(self.root, _entry(), by="agent-hermes")
        ap1, rec, replayed = IW.adopt(self.root, _entry()["verdict_id"],
                                      by="老田", reason="同意降级",
                                      store=self.store)
        self.assertFalse(replayed)
        self.assertEqual(rec["payload"]["verdict_id"],
                         _entry()["verdict_id"])
        self.assertEqual(rec["payload"]["adopted_by"], "老田")
        ms = MemoryStore(self.root / "data" / "memory", enforce=False)
        self.assertEqual(len(ms.read("wiring", IW.GNS_INTEL)), 1)
        # 幂等重放：零副作用
        ap2, rec2, replayed2 = IW.adopt(self.root, _entry()["verdict_id"],
                                        by="老田", store=self.store)
        self.assertTrue(replayed2)
        self.assertEqual(rec2, {})
        self.assertEqual(len(ms.read("wiring", IW.GNS_INTEL)), 1)

    def test_adopt_unknown_verdict_rejected(self):
        with self.assertRaises(IW.IntelWiringError):
            IW.adopt(self.root, "ghost:JR-0#r1", by="老田",
                     store=self.store)

    def test_adopt_requires_by(self):
        IW.draft(self.root, _entry(), by="agent-hermes")
        with self.assertRaises(IW.IntelWiringError):
            IW.adopt(self.root, _entry()["verdict_id"], by="",
                     store=self.store)

    # ── reject：凭据在、生产记录不写 ──
    def test_reject_records_credential_no_memory(self):
        IW.draft(self.root, _entry(), by="agent-hermes")
        ap, replayed = IW.reject(self.root, _entry()["verdict_id"],
                                 by="老田", reason="影响面低估",
                                 store=self.store)
        self.assertFalse(replayed)
        ms = MemoryStore(self.root / "data" / "memory", enforce=False)
        self.assertEqual(len(ms.read("wiring", IW.GNS_INTEL)), 0)

    # ── audit：空转 PASS / 绕门 FAIL / 一致 PASS ──
    def test_audit_empty_pool_idle_pass(self):
        rep = IW.audit_report(self.root)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["verdicts_total"], 0)
        self.assertTrue(rep["read_only"])

    def test_audit_bypass_fail(self):
        ms = MemoryStore(self.root / "data" / "memory", enforce=False)
        ms.write("wiring", IW.GNS_INTEL,
                 {"verdict_id": "rogue:JR-0#r1"})
        rep = IW.audit_report(self.root)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(rep["bypass_count"], 1)

    def test_audit_adopted_consistent_pass(self):
        e = _entry()
        IW.draft(self.root, e, by="agent-hermes")
        IW.adopt(self.root, e["verdict_id"], by="老田", store=self.store)
        rep = IW.audit_report(self.root)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["bypass_count"], 0)
        self.assertEqual(rep["cross_check"], [])
        states = {s["verdict_id"]: s["state"] for s in rep["states"]}
        self.assertEqual(states[e["verdict_id"]], "adopted")

    def test_audit_reject_state(self):
        e = _entry(vid="parquet_tdx_ts_daily:JR-2#r1")
        IW.draft(self.root, e, by="agent-hermes")
        IW.reject(self.root, e["verdict_id"], by="老田", store=self.store)
        rep = IW.audit_report(self.root)
        self.assertEqual(rep["verdict"], "PASS")
        states = {s["verdict_id"]: s["state"] for s in rep["states"]}
        self.assertEqual(states[e["verdict_id"]], "rejected")


class AutoDraftTests(unittest.TestCase):
    """断链自动起草：mock _fetch_json 全隔离（不打 8091）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _breaks_payload(self, status="fail", table="parquet:tdx/ts_daily"):
        row = {"output_table": table, "job": "cl1_sync", "step": "s1",
               "status": status, "started_at": "2026-10-17T07:45:00",
               "ended_at": None, "rows_written": 0}
        return {"ok": True, "view": "breaks",
                "data": {"broken": [row], "healthy_count": 19,
                         "tracked_tables": 20}}

    def test_auto_draft_creates_verdict(self):
        def fake(url, timeout=15.0):
            if "view=breaks" in url:
                return self._breaks_payload()
            return {"ok": True, "data": [
                {"output_table": "neo4j:TRADED_ON", "input_table": "x"}]}
        with mock.patch.object(IW, "_fetch_json", side_effect=fake):
            out = IW.auto_draft_from_lineage(self.root)
        self.assertTrue(out["ok"])
        self.assertEqual(out["drafted_total"] if "drafted_total" in out
                         else len(out["drafted"]), 1)
        e = out["drafted"][0]
        self.assertEqual(e["table_urn"], "parquet:tdx/ts_daily")
        self.assertEqual(e["break_kind"], "broken")
        self.assertEqual(e["decision"], "degrade")
        self.assertIn("neo4j:TRADED_ON", e["impact"])
        self.assertEqual(len(IW.load_pool(self.root)), 1)

    def test_auto_draft_stale_maps_to_watch(self):
        def fake(url, timeout=15.0):
            if "view=breaks" in url:
                return self._breaks_payload(status="stale")
            return {"ok": True, "data": []}
        with mock.patch.object(IW, "_fetch_json", side_effect=fake):
            out = IW.auto_draft_from_lineage(self.root)
        self.assertEqual(out["drafted"][0]["decision"], "watch")
        self.assertEqual(out["drafted"][0]["break_kind"], "stale")

    def test_auto_draft_idempotent_same_run(self):
        calls = {"n": 0}
        def fake(url, timeout=15.0):
            calls["n"] += 1
            if "view=breaks" in url:
                return self._breaks_payload()
            return {"ok": True, "data": []}
        with mock.patch.object(IW, "_fetch_json", side_effect=fake):
            out1 = IW.auto_draft_from_lineage(self.root)
            out2 = IW.auto_draft_from_lineage(self.root)
        self.assertEqual(len(out1["drafted"]), 1)
        self.assertEqual(out2["drafted"], [])
        self.assertEqual(out2["skipped"], 1)
        self.assertEqual(len(IW.load_pool(self.root)), 1)

    def test_auto_draft_network_fail_visible(self):
        def fake(url, timeout=15.0):
            raise OSError("connection refused")
        with mock.patch.object(IW, "_fetch_json", side_effect=fake):
            out = IW.auto_draft_from_lineage(self.root)
        self.assertFalse(out["ok"])
        self.assertIn("不可达", out["error"])
        self.assertEqual(len(IW.load_pool(self.root)), 0)


if __name__ == "__main__":
    unittest.main()
