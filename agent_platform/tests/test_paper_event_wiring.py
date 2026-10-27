# -*- coding: utf-8 -*-
"""M4-P2 Paper 事件入账测试。

覆盖：环境白名单严格拒绝（shadow/advisory/legacy/real 全拒）/
execution_allowed 红线（缺字段也拒）/ 必填字段校验 / 幂等重放与
漂移拒绝 / 绿地空账 / propose_real 只注册不批准（无 Approval 写入、
幂等提议）/ trace 链挂接（两环链+链失败留痕不阻断）/ 证据收集器
（paper 域 + 绿地空账零 findings）。
"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
_agent = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_agent / "quant_backtest"))

from domains import invest_wiring as iw  # noqa: E402
from adapters.evidence_collect import collect  # noqa: E402
from adapters.trace_link import LinkStore, replay_trace  # noqa: E402


def _event(**over):
    ev = {
        "schema_version": "1.0",
        "environment": "paper",
        "trace_id": "tr-p2-001",
        "decision_id": "dec-abc",
        "fact_hash": "fh-123",
        "idempotency_key": "ik-1",
        "code": "300750",
        "action": "candidate_buy",
        "as_of": "2026-10-17T15:00:00+08:00",
        "execution_allowed": False,
    }
    ev.update(over)
    return ev


class PaperEventTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="paper_p2_"))
        self.links = LinkStore(self.root / "agent_platform" / "data"
                               / "trace" / "links.jsonl")

    # ---- 红线三道闸 ----
    def test_environment_whitelist_strict(self):
        for bad in ("shadow", "advisory", "legacy", "real", "live", None):
            with self.assertRaises(iw.InvestWiringError):
                iw.record_paper_event(self.root, _event(environment=bad))

    def test_execution_allowed_must_be_explicit_false(self):
        with self.assertRaises(iw.InvestWiringError):
            iw.record_paper_event(self.root, _event(execution_allowed=True))
        with self.assertRaises(iw.InvestWiringError):   # 缺字段也拒
            ev = _event()
            del ev["execution_allowed"]
            iw.record_paper_event(self.root, ev)

    def test_required_fields(self):
        for f in iw.PAPER_EVENT_REQUIRED:
            ev = _event()
            del ev[f]
            with self.assertRaises(iw.InvestWiringError):
                iw.record_paper_event(self.root, ev)

    # ---- 幂等与漂移 ----
    def test_record_and_replay(self):
        e1, rep1, f1 = iw.record_paper_event(self.root, _event())
        self.assertFalse(rep1)
        e2, rep2, _ = iw.record_paper_event(self.root, _event())
        self.assertTrue(rep2)
        self.assertEqual(len(iw.load_paper_events(self.root)), 1)  # 不追加

    def test_drift_rejected(self):
        iw.record_paper_event(self.root, _event())
        with self.assertRaises(iw.InvestWiringError):
            iw.record_paper_event(self.root, _event(fact_hash="fh-CHANGED"))

    def test_green_field_empty(self):
        self.assertEqual(iw.load_paper_events(self.root), [])

    def test_find_unregistered_rejected(self):
        with self.assertRaises(iw.InvestWiringError):
            iw.find_paper_event(self.root, "no-such")

    # ---- propose_real：只注册不批准 ----
    def test_propose_real_registers_only(self):
        iw.record_paper_event(self.root, _event())
        e = iw.propose_real_transition(self.root, "tr-p2-001", by="老田",
                                       reason="模拟表现达标")
        self.assertIn("仅注册", e["proposed_real"]["note"])
        # 幂等提议
        e2 = iw.propose_real_transition(self.root, "tr-p2-001", by="老田")
        self.assertEqual(e2["proposed_real"]["by"], "老田")
        # 未提议的对照组
        iw.record_paper_event(self.root, _event(trace_id="tr-p2-002"))
        raw = iw.find_paper_event(self.root, "tr-p2-002")
        self.assertNotIn("proposed_real", raw)
        # 红线核验：全程零 Approval 写入
        db = self.root / "agent_platform" / "data" / "approvals" / "approvals.db"
        self.assertFalse(db.exists())

    def test_propose_real_requires_by_and_source(self):
        iw.record_paper_event(self.root, _event())
        with self.assertRaises(iw.InvestWiringError):
            iw.propose_real_transition(self.root, "tr-p2-001", by="")

    # ---- trace 链挂接 ----
    def test_link_with_shadow_run(self):
        _, _, f1 = iw.record_paper_event(
            self.root, _event(shadow_run_id="76cf6d30358f"),
            link_store=self.links)
        self.assertEqual(f1, [])
        r = replay_trace(self.links, "tr-p2-001")
        self.assertEqual(r["verdict"], "OK")
        self.assertEqual(r["matches"][0]["missing_segments"],
                         ["execution", "perf"])

    def test_link_failure_leaves_finding_not_block(self):
        # 链挂接失败（store.record 抛错）不阻断入账：账本先行，失败留 finding
        import unittest.mock as mock
        with mock.patch.object(self.links, "record",
                               side_effect=ValueError("链校验失败（模拟）")):
            _, _, fs = iw.record_paper_event(
                self.root, _event(shadow_run_id="76cf6d30358f"),
                link_store=self.links)
        self.assertEqual(len(fs), 1)
        self.assertIn("trace 链挂接失败", fs[0])
        self.assertEqual(len(iw.load_paper_events(self.root)), 1)  # 已入账

    def test_link_without_shadow_single_segment(self):
        iw.record_paper_event(self.root, _event(), link_store=self.links)
        r = replay_trace(self.links, "tr-p2-001")
        self.assertEqual(r["matches"][0]["missing_segments"],
                         ["shadow", "execution", "perf"])


class PaperEvidenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="paper_ev_"))
        # 隔离：collect 默认写真仓 evidence.db——注入 tmp store 防污染
        from engine.evidence import EvidenceStore
        self.store = EvidenceStore(
            self.root / "agent_platform" / "data" / "evidence" / "evidence.db")

    def tearDown(self):
        self.store.close()

    def test_green_field_zero_collected_zero_findings(self):
        report = collect(self.root, domains=["paper"], store=self.store)
        dom = report["domains"]["paper"]
        self.assertEqual(dom["collected"], 0)
        self.assertEqual(report["findings"], [])     # 绿地非异常

    def test_collect_paper_events(self):
        iw.record_paper_event(self.root, _event())
        report = collect(self.root, domains=["paper"], store=self.store)
        dom = report["domains"]["paper"]
        self.assertEqual(dom["collected"], 1)
        self.assertEqual(dom["new"], 1)


if __name__ == "__main__":
    unittest.main()
