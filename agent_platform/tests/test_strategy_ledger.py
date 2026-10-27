# -*- coding: utf-8 -*-
"""strategy 域账本与证据源测试（K-ORG-167 wiring，M6 深化刀）。

覆盖：record_verdicts 幂等/安全阀（root 缺失拒写）/坏行容忍读侧；
evidence_collect 第十一证据源 strategy（真仓零副作用 tmp 隔离）。
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform"))
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))

from strategy_registry import (  # noqa: E402
    StrategyError,
    Verdict,
    _ledger_path,
    load_verdicts,
    record_verdicts,
    verdict_key,
)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


evc = _load("evc_s", "agent_platform/adapters/evidence_collect.py")


def _v(
    strategy_id="s1",
    symbol="600519",
    trade_date="2026-10-18",
    recommendation="hold",
    confidence=0.6,
):
    return Verdict(
        strategy_id=strategy_id,
        symbol=symbol,
        recommendation=recommendation,
        confidence=confidence,
        stance="bull",
        score=0.1,
        rationale="r",
    )


def _run(tmp, verdicts, **kw):
    return record_verdicts(verdicts, root=tmp, **kw)


class StrategyLedgerTests(unittest.TestCase):
    def test_root_required_rejects_write(self):
        # M5 刀 2 判例平移：无显式 root 拒绝落盘（安全阀）
        with self.assertRaises(StrategyError):
            record_verdicts([_v()], None, trade_date="2026-10-18")

    def test_record_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            rep = _run(td, [_v()], trade_date="2026-10-18", run_id="r1")
            self.assertEqual(rep["recorded"], 1)
            rows = load_verdicts(td)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["strategy_id"], "s1")
            self.assertEqual(rows[0]["run_id"], "r1")
            self.assertEqual(rows[0]["trade_date"], "2026-10-18")

    def test_idempotent_same_day(self):
        # 同 strategy|symbol|trade_date 二次入账 → replayed 不重复
        with tempfile.TemporaryDirectory() as td:
            _run(td, [_v()], trade_date="2026-10-18")
            rep2 = _run(td, [_v()], trade_date="2026-10-18")
            self.assertEqual((rep2["recorded"], rep2["replayed"]), (0, 1))
            self.assertEqual(len(load_verdicts(td)), 1)

    def test_cross_day_not_skipped(self):
        # 幂等键含日期（f59a0738 判例平移）：跨天必须可再入账
        with tempfile.TemporaryDirectory() as td:
            _run(td, [_v()], trade_date="2026-10-18")
            rep = _run(td, [_v()], trade_date="2026-10-19")
            self.assertEqual(rep["recorded"], 1)
            self.assertEqual(len(load_verdicts(td)), 2)

    def test_key_stable_shape(self):
        k1 = verdict_key("s1", "600519", "2026-10-18")
        k2 = verdict_key("s1", "600519", "2026-10-18")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 16)
        self.assertNotEqual(k1, verdict_key("s2", "600519", "2026-10-18"))

    def test_invalid_verdict_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            bad = _v(recommendation="yolo")
            with self.assertRaises(StrategyError):
                _run(td, [bad], trade_date="2026-10-18")
            self.assertFalse(_ledger_path(td).exists())


class StrategyEvidenceTests(unittest.TestCase):
    def test_collect_strategy_domain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "agent_platform" / "data" / "strategy"
            p.mkdir(parents=True)
            (p / "strategy_runs.jsonl").write_text(
                json.dumps(
                    {
                        "verdict_key": "abc123def4567890",
                        "strategy_id": "shadow_trend",
                        "symbol": "600519",
                        "recommendation": "buy",
                        "stance": "bull",
                        "run_id": "r1",
                        "trade_date": "2026-10-18",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            evs, findings = evc._collect_strategy(root)
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0].payload["domain"], "strategy")
            self.assertEqual(evs[0].payload["kind"], "strategy_verdict_fingerprint")
            self.assertIn("shadow_trend", evs[0].statement)
            self.assertEqual(findings, [])

    def test_collect_missing_ledger_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            evs, findings = evc._collect_strategy(Path(td))
            self.assertEqual((evs, findings), ([], []))

    def test_collect_bad_line_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "agent_platform" / "data" / "strategy"
            p.mkdir(parents=True)
            (p / "strategy_runs.jsonl").write_text("{broken\n", encoding="utf-8")
            evs, findings = evc._collect_strategy(root)
            self.assertEqual(len(evs), 0)
            self.assertEqual(len(findings), 1)
            self.assertIn("第 1 行", findings[0])

    def test_all_domains_includes_strategy(self):
        self.assertIn("strategy", evc.ALL_DOMAINS)
        self.assertIn("strategy", evc._COLLECTORS)


if __name__ == "__main__":
    unittest.main()
