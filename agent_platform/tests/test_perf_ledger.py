# -*- coding: utf-8 -*-
"""M4-P4 绩效账本测试：幂等/冲突拒绝/缺锚点跳过/链挂接/分组聚合/
intel 自动起草阈值与幂等。全部 tmp 仓隔离（真仓零写副作用判例）。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from adapters.trace_link import LinkStore  # noqa: E402
from engine import perf_ledger as pl  # noqa: E402


def _row(**kw):
    base = {
        "date": "2026-10-16",
        "symbol": "600519",
        "run_id": "run-1",
        "score": 3.2,
        "confidence": 0.7,
        "ret_pct": 1.25,
        "horizon": 3,
        "base": 1500.0,
        "exit": 1518.75,
        "recommendation": "buy",
        "stance": "坚守",
        "dk_regime": "rotation",
        "dk_profile": "relaxed",
        "dk_blocked": False,
    }
    base.update(kw)
    return base


class PerfLedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="perf4_"))

    def test_record_and_replay_idempotent(self):
        r = pl.record_perf_rows([_row()], self.root)
        self.assertEqual(r["recorded"], 1)
        r2 = pl.record_perf_rows([_row()], self.root)
        self.assertEqual(r2["replayed"], 1)
        self.assertEqual(r2["recorded"], 0)
        self.assertEqual(len(pl.load_perf_rows(self.root)), 1)

    def test_same_key_different_ret_rejected(self):
        pl.record_perf_rows([_row(ret_pct=1.25)], self.root)
        with self.assertRaises(ValueError):
            pl.record_perf_rows([_row(ret_pct=-2.0)], self.root)

    def test_missing_anchor_skipped_with_finding(self):
        r = pl.record_perf_rows([_row(run_id=""), _row(ret_pct="pending")], self.root)
        self.assertEqual(r["recorded"], 0)
        self.assertEqual(r["skipped"], 2)
        self.assertEqual(len(r["findings"]), 2)

    def test_link_store_two_segment_chain(self):
        links = LinkStore(self.root / "t" / "links.jsonl")
        r = pl.record_perf_rows([_row()], self.root, link_store=links)
        self.assertEqual(r["recorded"], 1)
        self.assertEqual(r["findings"], [])
        st = links.stats()
        self.assertEqual(st["by_segment"]["perf"], 1)
        self.assertEqual(st["by_segment"]["shadow"], 1)
        # 同行重放：perf 入账 replayed、链 replayed 零副作用
        r2 = pl.record_perf_rows([_row()], self.root, link_store=links)
        self.assertEqual(r2["replayed"], 1)
        self.assertEqual(r2["findings"], [])

    def test_green_field_empty(self):
        self.assertEqual(pl.load_perf_rows(self.root), [])
        self.assertEqual(pl.by_profile(root=self.root), {})

    def test_corrupt_line_tolerated(self):
        p = pl.ledger_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{broken json\n", encoding="utf-8")
        self.assertEqual(pl.load_perf_rows(self.root), [])


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="perf4p_"))

    def test_grouping_by_profile(self):
        rows = [
            _row(run_id="r%d" % i, dk_profile="relaxed", ret_pct=1.0 + i)
            for i in range(3)
        ]
        rows += [
            _row(run_id="s%d" % i, dk_profile="strict", ret_pct=-0.5) for i in range(2)
        ]
        rows += [_row(run_id="x%d" % i, dk_profile=None, ret_pct=2.0) for i in range(2)]
        pl.record_perf_rows(rows, self.root)
        g = pl.by_profile(root=self.root)
        self.assertEqual(g["relaxed"]["n"], 3)
        self.assertEqual(g["strict"]["n"], 2)
        self.assertEqual(g["none"]["n"], 2)
        self.assertAlmostEqual(g["relaxed"]["mean_ret"], 2.0)
        self.assertAlmostEqual(g["strict"]["win_rate"], 0.0)

    def test_none_group_never_triggers(self):
        # none 组（无阵容字段）不参与起草——只观测
        rows = [_row(run_id="n%d" % i, dk_profile=None, ret_pct=-3.0) for i in range(8)]
        pl.record_perf_rows(rows, self.root)
        drafted, findings = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual(drafted, [])
        self.assertEqual(findings, [])


class AutoDraftTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="perf4d_"))

    def _feed(self, prof, n, ret):
        pl.record_perf_rows(
            [
                _row(run_id="%s%d" % (prof, i), dk_profile=prof, ret_pct=ret)
                for i in range(n)
            ],
            self.root,
        )

    def test_relaxed_negative_triggers_degrade(self):
        self._feed("relaxed", 6, -2.0)
        drafted, findings = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual(findings, [])
        self.assertEqual(len(drafted), 1)
        self.assertEqual(drafted[0]["decision"], "degrade")
        self.assertEqual(drafted[0]["table_urn"], "agent_platform/perf_ledger")
        # 落池实锤 + content_hash 盖章
        pool = json.loads(
            (self.root / "data" / "intel_verdicts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertEqual(pool["verdict_id"], drafted[0]["verdict_id"])
        self.assertTrue(pool["content_hash"])

    def test_strict_negative_triggers_watch_only(self):
        self._feed("strict", 6, -1.0)
        drafted, _ = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual([d["decision"] for d in drafted], ["watch"])

    def test_small_sample_below_threshold_silent(self):
        self._feed("relaxed", 4, -5.0)
        drafted, findings = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual(drafted, [])
        self.assertEqual(findings, [])

    def test_positive_profile_no_trigger(self):
        self._feed("relaxed", 6, 1.5)
        drafted, _ = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual(drafted, [])

    def test_rerun_same_stats_idempotent(self):
        self._feed("relaxed", 6, -2.0)
        d1, _ = pl.auto_draft_roster(self.root, by="tester")
        d2, _ = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual(len(d1), 1)
        self.assertEqual(d2, [])  # 同统计指纹幂等跳过
        pool = (
            (self.root / "data" / "intel_verdicts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(len(pool), 1)

    def test_stats_shift_creates_new_verdict(self):
        self._feed("relaxed", 6, -2.0)
        pl.auto_draft_roster(self.root, by="tester")
        # 统计态变化（追加新样本）→ 新作业 → 新裁决（#r 修订语义）
        pl.record_perf_rows([_row(run_id="relaxed-x7", ret_pct=-3.0)], self.root)
        d2, _ = pl.auto_draft_roster(self.root, by="tester")
        self.assertEqual(len(d2), 1)
        pool = (
            (self.root / "data" / "intel_verdicts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(len(pool), 2)


if __name__ == "__main__":
    unittest.main()
