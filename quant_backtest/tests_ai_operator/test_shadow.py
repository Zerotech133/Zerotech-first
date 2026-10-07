"""事实包、时点约束与可恢复影子审计的行为测试。"""

import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_operator.contracts import canonical_json
from ai_operator.memory import open_store
from ai_operator.shadow import build_fact_pack, make_shadow_decision, record_shadow_decision


AS_OF = "2026-10-07T10:00:00+08:00"
VALID_UNTIL = "2026-10-07T11:00:00+08:00"
VERSIONS = {"data": "d1", "algorithm": "a1", "knowledge": "k1", "rules": "r1"}
KNOWN_SAMPLE_POSTERIOR_FIELDS = [
    "r5", "r10", "r20", "r60", "ret5", "ret10", "ret20", "ret60",
    "mature5", "mature20", "post10", "post_hm10",
]


def facts():
    return [{"id": "bar-1", "source": "local", "occurred_at": "2026-10-07T09:30:00+08:00",
             "available_at": "2026-10-07T09:31:00+08:00", "data": {"close": 12.5}}]


def pack():
    return build_fact_pack("000001", "30m", AS_OF, facts(), VERSIONS)


def decision(value=None, **changes):
    arguments = {"action": "candidate_buy", "evidence_ids": ["bar-1"],
                 "rationale": "结构候选，供影子审计", "valid_until": VALID_UNTIL}
    arguments.update(changes)
    return make_shadow_decision(pack() if value is None else value, **arguments)


def rehash(value, field):
    value[field] = hashlib.sha256(canonical_json({k: v for k, v in value.items() if k != field}).encode("utf-8")).hexdigest()
    return value


class FactPackTests(unittest.TestCase):
    def test_pack_schema_time_hash_and_input_isolation(self):
        inputs = facts()
        versions = copy.deepcopy(VERSIONS)
        result = build_fact_pack("000001", "30m", AS_OF, inputs, versions)
        self.assertEqual(set(result), {"schema_version", "environment", "code", "period", "as_of", "facts", "versions", "fact_hash"})
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["environment"], "shadow")
        self.assertEqual(result["as_of"], "2026-10-07T02:00:00.000000+00:00")
        self.assertEqual(result["fact_hash"], rehash(copy.deepcopy(result), "fact_hash")["fact_hash"])
        inputs[0]["data"]["close"] = 99
        versions["data"] = "changed"
        self.assertEqual(result["facts"][0]["data"]["close"], 12.5)
        self.assertEqual(result["versions"], VERSIONS)

    def test_canonical_hash_ignores_object_key_order(self):
        original = facts()
        reordered = [{key: original[0][key] for key in reversed(original[0])}]
        self.assertEqual(build_fact_pack("000001", "30m", AS_OF, original, VERSIONS),
                         build_fact_pack("000001", "30m", AS_OF, reordered, dict(reversed(list(VERSIONS.items())))))

    def test_future_or_reverse_times_are_rejected(self):
        for occurred, available in [("2026-10-07T09:30:00+08:00", "2026-10-07T10:01:00+08:00"),
                                    ("2026-10-07T09:32:00+08:00", "2026-10-07T09:31:00+08:00")]:
            with self.subTest(occurred=occurred, available=available):
                items = facts()
                items[0].update(occurred_at=occurred, available_at=available)
                with self.assertRaises(ValueError):
                    build_fact_pack("000001", "30m", AS_OF, items, VERSIONS)

    def test_times_require_explicit_timezone_and_json_string(self):
        for bad in [None, "2026-10-07T09:30:00", "invalid", datetime.now(timezone.utc), 1]:
            for field in ["occurred_at", "available_at", "as_of"]:
                with self.subTest(field=field, bad=bad):
                    items = facts()
                    instant = AS_OF
                    if field == "as_of":
                        instant = bad
                    else:
                        items[0][field] = bad
                    with self.assertRaises(ValueError):
                        build_fact_pack("000001", "30m", instant, items, VERSIONS)

    def test_unknown_missing_fact_fields_duplicate_ids_and_invalid_types(self):
        variants = []
        for key in facts()[0]:
            item = facts()[0]
            del item[key]
            variants.append([item])
        variants += [facts() * 2, [dict(facts()[0], extra=True)], [dict(facts()[0], id=" ")],
                     [dict(facts()[0], source="")], [dict(facts()[0], data=[])], {}, (facts()[0],), [None]]
        for items in variants:
            with self.subTest(items=items), self.assertRaises(ValueError):
                build_fact_pack("000001", "30m", AS_OF, items, VERSIONS)

    def test_fixed_nonempty_versions_and_security_period(self):
        for invalid in [None, {}, dict(VERSIONS, other="x"), dict(VERSIONS, data=""),
                        dict(VERSIONS, knowledge=1), {k: v for k, v in VERSIONS.items() if k != "rules"}]:
            with self.subTest(versions=invalid), self.assertRaises(ValueError):
                build_fact_pack("000001", "30m", AS_OF, facts(), invalid)
        for code, period in [("", "30m"), ("000001", " "), (1, "30m"), ("000001", None)]:
            with self.subTest(code=code, period=period), self.assertRaises(ValueError):
                build_fact_pack(code, period, AS_OF, facts(), VERSIONS)

    def test_recursive_posterior_keys_rejected(self):
        for key in ["post", "forward_return", "label", "outcome"]:
            items = facts()
            items[0]["data"] = {"nested": [{"another": {key: False}}]}
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_fact_pack("000001", "30m", AS_OF, items, VERSIONS)

    def test_known_sample_posterior_fields_rejected_in_nested_data(self):
        for key in KNOWN_SAMPLE_POSTERIOR_FIELDS:
            items = facts()
            items[0]["data"] = {"samples": [{"derived": {key: None}}]}
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_fact_pack("000001", "30m", AS_OF, items, VERSIONS)

    def test_historical_pre_close_is_allowed_in_nested_data(self):
        items = facts()
        items[0]["data"] = {"samples": [{"pre_close": 12.0}]}
        value = build_fact_pack("000001", "30m", AS_OF, items, VERSIONS)
        self.assertEqual(value["facts"][0]["data"], {"samples": [{"pre_close": 12.0}]})
        self.assertEqual(decision(value)["fact_hash"], value["fact_hash"])

    def test_strict_json_rejects_nonfinite_nonstring_keys_and_cycles(self):
        cycle = {}
        cycle["cycle"] = cycle
        for invalid in [{"close": float("nan")}, {"close": float("inf")}, {1: "x"}, {"x": (1, 2)}, cycle]:
            items = facts()
            items[0]["data"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_fact_pack("000001", "30m", AS_OF, items, VERSIONS)

    def test_empty_fact_list_is_allowed(self):
        value = build_fact_pack("000001", "30m", AS_OF, [], VERSIONS)
        self.assertEqual(value["facts"], [])
        self.assertEqual(decision(value, action="abstain", evidence_ids=[])["action"], "abstain")


class DecisionTests(unittest.TestCase):
    def test_decision_schema_hash_and_nonexecution_flags(self):
        value = pack()
        ids = ["bar-1"]
        result = make_shadow_decision(value, "candidate_buy", ids, "候选", VALID_UNTIL)
        self.assertEqual(set(result), {"schema_version", "environment", "code", "period", "as_of", "fact_hash", "versions",
                                      "action", "evidence_ids", "rationale", "valid_until", "confidence", "execution_allowed", "decision_id"})
        self.assertIsNone(result["confidence"])
        self.assertIs(result["execution_allowed"], False)
        self.assertEqual(result["decision_id"], rehash(copy.deepcopy(result), "decision_id")["decision_id"])
        ids.append("changed")
        value["versions"]["data"] = "changed"
        self.assertEqual(result["evidence_ids"], ["bar-1"])
        self.assertEqual(result["versions"], VERSIONS)

    def test_all_frozen_actions_and_candidate_evidence_requirement(self):
        for action in ["observe", "candidate_buy", "candidate_sell", "abstain"]:
            self.assertEqual(decision(action=action)["action"], action)
        for action in ["observe", "abstain"]:
            self.assertEqual(decision(action=action, evidence_ids=[])["evidence_ids"], [])
        for action in ["candidate_buy", "candidate_sell"]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                decision(action=action, evidence_ids=[])

    def test_invalid_actions_ids_reasons_and_expiry(self):
        variants = [{"action": "buy"}, {"action": []}, {"evidence_ids": ["missing"]},
                    {"evidence_ids": ["bar-1", "bar-1"]}, {"evidence_ids": "bar-1"},
                    {"evidence_ids": [None]}, {"rationale": " "}, {"rationale": 1},
                    {"valid_until": AS_OF}, {"valid_until": "2026-10-07T01:59:00+00:00"},
                    {"valid_until": "2026-10-07T11:00:00"}]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decision(**changes)

    def test_rechecks_fact_hash(self):
        value = pack()
        value["facts"][0]["data"]["close"] = 900
        with self.assertRaises(ValueError):
            decision(value)

    def test_rehashed_known_sample_posterior_fields_are_rejected(self):
        for key in KNOWN_SAMPLE_POSTERIOR_FIELDS:
            value = pack()
            value["facts"][0]["data"] = {"samples": [{"derived": {key: False}}]}
            rehash(value, "fact_hash")
            with self.subTest(key=key), self.assertRaises(ValueError):
                decision(value)

    def test_rehashed_invalid_full_schema_is_rejected(self):
        variants = [dict(pack(), schema_version=True), dict(pack(), schema_version=2),
                    dict(pack(), environment="paper"), dict(pack(), extra="x"),
                    dict(pack(), code=""), dict(pack(), as_of="2026-10-07T09:00:00+08:00"),
                    dict(pack(), versions=dict(VERSIONS, data=""))]
        missing = pack()
        del missing["period"]
        variants.append(missing)
        for key in ["post", "forward_return", "label", "outcome"]:
            value = pack()
            value["facts"][0]["data"] = {"items": [{key: 1}]}
            variants.append(value)
        invalid_fact = pack()
        invalid_fact["facts"][0]["available_at"] = "2026-10-07T02:01:00+00:00"
        variants.append(invalid_fact)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decision(rehash(value, "fact_hash"))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = open_store(Path(self.temporary.name) / "memory.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_two_events_have_frozen_provenance_and_are_idempotent(self):
        value = pack()
        recommendation = decision(value)
        result = record_shadow_decision(self.store, value, recommendation)
        self.assertEqual(set(result), {"fact_event_id", "decision_event_id"})
        self.assertEqual(record_shadow_decision(self.store, value, recommendation), result)
        self.assertEqual(self.store.snapshot()["events"], 2)
        fact_event = self.store.get_event(result["fact_event_id"])
        decision_event = self.store.get_event(result["decision_event_id"])
        for event, kind, source_id in [(fact_event, "shadow.fact_pack", value["fact_hash"]),
                                       (decision_event, "shadow.decision", recommendation["decision_id"])]:
            self.assertEqual(event["kind"], kind)
            self.assertEqual(event["source"], "ai_operator.shadow")
            self.assertEqual(event["source_id"], source_id)
            self.assertEqual(event["environment"], "shadow")
            self.assertEqual(event["occurred_at"], value["as_of"])
            self.assertEqual(event["available_at"], value["as_of"])
        self.assertEqual(fact_event["payload"], value)
        self.assertEqual(decision_event["payload"], dict(recommendation, fact_event_id=result["fact_event_id"]))

    def test_second_event_failure_leaves_fact_and_retry_recovers(self):
        class FailSecondWrite:
            def __init__(self, store):
                self.store = store
                self.calls = 0

            def append_event(self, event):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("模拟第二次写入中断")
                return self.store.append_event(event)

        value = pack()
        recommendation = decision(value)
        with self.assertRaisesRegex(RuntimeError, "中断"):
            record_shadow_decision(FailSecondWrite(self.store), value, recommendation)
        self.assertEqual(self.store.snapshot()["events_by_kind"], {"shadow.fact_pack": 1})
        result = record_shadow_decision(self.store, value, recommendation)
        self.assertEqual(self.store.snapshot()["events_by_kind"], {"shadow.decision": 1, "shadow.fact_pack": 1})
        self.assertEqual(self.store.get_event(result["decision_event_id"])["payload"]["fact_event_id"], result["fact_event_id"])

    def test_tampered_decisions_rejected_before_any_write(self):
        original = decision()
        variants = [dict(original, execution_allowed=True), dict(original, execution_allowed=0),
                    dict(original, confidence=0.8), dict(original, action="buy"),
                    dict(original, evidence_ids=["missing"]), dict(original, code="600000"),
                    dict(original, period="5m"), dict(original, fact_hash="0" * 64),
                    dict(original, as_of="2026-10-07T01:00:00+00:00"), dict(original, schema_version=True),
                    dict(original, versions=dict(VERSIONS, algorithm="other")), dict(original, extra="x")]
        missing = copy.deepcopy(original)
        del missing["confidence"]
        variants.append(missing)
        variants.append(dict(original, rationale="篡改后未重算 ID"))
        for index, item in enumerate(variants):
            if index != len(variants) - 1:
                rehash(item, "decision_id")
            with self.subTest(item=item), self.assertRaises(ValueError):
                record_shadow_decision(self.store, pack(), item)
        self.assertEqual(self.store.snapshot()["events"], 0)

    def test_invalid_pack_rejected_before_any_write(self):
        value = pack()
        recommendation = decision(value)
        value["facts"][0]["data"]["label"] = "后验"
        rehash(value, "fact_hash")
        with self.assertRaises(ValueError):
            record_shadow_decision(self.store, value, recommendation)
        self.assertEqual(self.store.snapshot()["events"], 0)

    def test_different_decisions_reuse_fact_event(self):
        value = pack()
        first = record_shadow_decision(self.store, value, decision(value))
        second = record_shadow_decision(self.store, value, decision(value, action="abstain", rationale="暂缓"))
        self.assertEqual(first["fact_event_id"], second["fact_event_id"])
        self.assertNotEqual(first["decision_event_id"], second["decision_event_id"])
        self.assertEqual(self.store.snapshot()["events"], 3)


if __name__ == "__main__":
    unittest.main()
