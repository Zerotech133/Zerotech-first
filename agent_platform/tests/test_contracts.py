# -*- coding: utf-8 -*-
"""统一 contracts 基础契约测试。"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import (  # noqa: E402
    Contract,
    Evidence,
    GateDecision,
    IntelItem,
    OrderIntent,
    ResearchReport,
    Verdict,
)


class ContractsTest(unittest.TestCase):
    def test_import_uses_standard_library_only_and_old_style_constructor(self):
        value = IntelItem(title="旧调用方", source="legacy")
        self.assertIsInstance(value, Contract)
        self.assertEqual(value.schema_version, 1)
        self.assertTrue(value.trace_id)
        self.assertTrue(value.request_id)
        self.assertTrue(value.correlation_id)
        self.assertTrue(value.idempotency_key)

    def test_default_ids_are_unique(self):
        first = IntelItem()
        second = IntelItem()
        self.assertNotEqual(first.item_id, second.item_id)
        for field_name in (
            "trace_id",
            "request_id",
            "correlation_id",
            "idempotency_key",
        ):
            self.assertNotEqual(getattr(first, field_name), getattr(second, field_name))

    def test_round_trip_serialization(self):
        evidence = Evidence(
            source="shadow",
            source_ref="fact-1",
            statement="有可审计事实",
            strength=0.8,
        )
        report = ResearchReport(
            subject="样例标的",
            strategy="T1",
            summary="仅研究，不下单",
            evidence=[evidence],
            confidence=0.7,
        )
        restored = ResearchReport.from_json(report.to_json())
        self.assertEqual(restored.to_dict(), report.to_dict())
        self.assertIsInstance(restored.evidence[0], Evidence)
        self.assertEqual(
            json.loads(report.to_json())["evidence"][0]["source"], "shadow"
        )

    def test_all_contracts_are_json_serializable(self):
        values = [
            IntelItem(title="情报"),
            Evidence(statement="证据"),
            ResearchReport(subject="报告"),
            Verdict(subject="结论"),
            OrderIntent(symbol="000001.SZ"),
            GateDecision(target_id="decision-1", decision=GateDecision.REVIEW),
        ]
        for value in values:
            encoded = value.to_json()
            self.assertIsInstance(json.loads(encoded), dict)
            self.assertEqual(type(value.from_json(encoded)), type(value))

    def test_gate_decision_has_three_value_semantics(self):
        allowed = GateDecision(target_id="x", decision=True)
        denied = GateDecision(target_id="x", decision=False)
        review = GateDecision(target_id="x", decision=None)
        self.assertIs(allowed.decision, True)
        self.assertIs(denied.decision, False)
        self.assertIsNone(review.decision)
        self.assertIs(GateDecision.ALLOW, True)
        self.assertIs(GateDecision.DENY, False)
        self.assertIsNone(GateDecision.REVIEW)
        with self.assertRaises(TypeError):
            GateDecision(decision=1)

    def test_from_dict_ignores_unknown_legacy_fields(self):
        payload = {
            "title": "旧事件投影",
            "source": "portal",
            "trace_id": "trace-legacy",
            "legacy_field": "keep outside contract",
        }
        value = IntelItem.from_dict(payload)
        self.assertEqual(value.title, "旧事件投影")
        self.assertEqual(value.trace_id, "trace-legacy")
        self.assertNotIn("legacy_field", value.to_dict())


if __name__ == "__main__":
    unittest.main()
