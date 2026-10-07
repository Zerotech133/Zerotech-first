"""逐根结构回放的时点和审计行为测试。"""

import copy
import hashlib
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ai_operator.contracts import canonical_json, normalize_time
from ai_operator.replay import replay_bars


KINDS = ("fractals", "bis", "segments", "pivots", "signals")


def bars(count=4):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [dict(dt=f"bar-{i}", o=10, h=12, l=9, c=11, v=0,
                 close_at=(start + timedelta(minutes=i)).isoformat(),
                 available_at=(start + timedelta(minutes=i, seconds=20)).isoformat())
            for i in range(count)]


def empty_structures():
    return {kind: [] for kind in KINDS}


class ReplayTests(unittest.TestCase):
    def test_empty_bars_do_not_import_algorithm(self):
        with patch.dict(sys.modules, {"chanlun_bi": None}):
            result = replay_bars([])
        self.assertEqual(result, dict(schema_version=1, algorithm_id="local-chanlun-bi-v1",
                                     bar_count=0, snapshots=[]))

    def test_prefixes_are_isolated_and_future_suffix_preserves_history(self):
        seen = []

        def analyzer(prefix):
            seen.append(copy.deepcopy(prefix))
            result = empty_structures()
            result["fractals"] = [dict(dt=prefix[-1]["dt"], price=prefix[-1]["c"])]
            prefix[0]["c"] = 999
            prefix.clear()
            return result

        original = bars()
        saved = copy.deepcopy(original)
        first = replay_bars(original[:2], analyzer)
        extended = replay_bars(original, analyzer)
        self.assertEqual(first["snapshots"], extended["snapshots"][:2])
        self.assertEqual(original, saved)
        self.assertEqual([len(p) for p in seen], [1, 2, 1, 2, 3, 4])
        self.assertTrue(all(p[0]["c"] == 11 for p in seen))
        snapshot = extended["snapshots"][1]
        self.assertEqual(snapshot["as_of"], normalize_time(original[1]["available_at"]))
        self.assertEqual(snapshot["bar_dt"], "bar-1")
        self.assertEqual(snapshot["visible_count"], 2)
        json.loads(canonical_json(extended))

    def test_revisions_withdrawals_and_reappearance_retain_history(self):
        data_a = dict(s_dt="bar-0", e_dt="bar-0", e_price=11)
        data_b = dict(s_dt="bar-0", e_dt="bar-1", e_price=12)

        def analyzer(prefix):
            result = empty_structures()
            result["bis"] = [[data_a], [data_b], [], [data_a]][len(prefix) - 1]
            return result

        source = bars()
        snapshots = replay_bars(source, analyzer)["snapshots"]
        added = snapshots[0]["changes"]["added"][0]
        withdrawn = snapshots[1]["changes"]["withdrawn"][0]
        again = snapshots[3]["changes"]["added"][0]
        self.assertEqual(added["kind"], "bis")
        self.assertEqual(added["data"], data_a)
        self.assertEqual(added["pivot_at"], "bar-0")
        self.assertEqual(added["confirmed_at"], snapshots[0]["as_of"])
        self.assertEqual(added["status"], "observed")
        self.assertEqual(withdrawn["version_id"], added["version_id"])
        self.assertEqual(withdrawn["confirmed_at"], added["confirmed_at"])
        self.assertEqual(withdrawn["observed_at"], snapshots[1]["as_of"])
        self.assertEqual(again["version_id"], added["version_id"])
        self.assertEqual(again["confirmed_at"], snapshots[3]["as_of"])
        self.assertEqual(snapshots[0]["structures"]["bis"], [data_a])
        self.assertEqual(snapshots[2]["changes"]["withdrawn"][0]["data"], data_b)

    def test_unchanged_versions_have_no_new_change(self):
        def analyzer(prefix):
            return dict(fractals=[dict(dt="bar-0", price=11)])

        snapshots = replay_bars(bars(2), analyzer)["snapshots"]
        self.assertEqual(snapshots[1]["changes"], dict(added=[], withdrawn=[]))

    def test_content_hash_is_canonical_and_kind_specific(self):
        def analyzer(prefix):
            return dict(fractals=[dict(price=11, dt="bar-0")], signals=[dict(d="bar-0", p=11)])

        changes = replay_bars(bars(1), analyzer)["snapshots"][0]["changes"]["added"]
        expected = hashlib.sha256(canonical_json(dict(kind="fractals", data=dict(dt="bar-0", price=11))).encode("utf-8")).hexdigest()
        self.assertEqual(changes[0]["version_id"], expected)
        self.assertNotEqual(changes[0]["version_id"], changes[1]["version_id"])

    def test_output_is_frozen_when_analyzer_reuses_mutable_result(self):
        shared = empty_structures()

        def analyzer(prefix):
            shared["fractals"][:] = [dict(dt=prefix[-1]["dt"], price=len(prefix))]
            return shared

        snapshots = replay_bars(bars(2), analyzer)["snapshots"]
        shared["fractals"][0]["price"] = 999
        self.assertEqual(snapshots[0]["structures"]["fractals"][0]["price"], 1)
        self.assertEqual(snapshots[1]["structures"]["fractals"][0]["price"], 2)

    def test_all_kinds_choose_their_pivot_field(self):
        result = dict(fractals=[dict(dt="bar-0")], bis=[dict(e_dt="bar-0")],
                      segments=[dict(e_dt="bar-0")], pivots=[dict(e_dt="bar-0")],
                      signals=[dict(d="bar-0")], merged_n=1, top_n=0, bottom_n=1)
        snapshot = replay_bars(bars(1), lambda p: result)["snapshots"][0]
        self.assertEqual(snapshot["structures"], result)
        self.assertEqual([c["kind"] for c in snapshot["changes"]["added"]], list(KINDS))
        self.assertTrue(all(c["pivot_at"] == "bar-0" for c in snapshot["changes"]["added"]))

    def test_default_adapter_passes_prefix_closes(self):
        received = []
        module = types.ModuleType("chanlun_bi")

        def analyze(prefix, closes=None):
            received.append((copy.deepcopy(prefix), closes))
            return empty_structures()

        module.analyze = analyze
        with patch.dict(sys.modules, {"chanlun_bi": module}):
            replay_bars(bars(2))
        self.assertEqual([r[1] for r in received], [[11], [11, 11]])

    def test_missing_default_dependency_has_chinese_error(self):
        with patch.dict(sys.modules, {"chanlun_bi": None}):
            with self.assertRaisesRegex(RuntimeError, "算法|依赖"):
                replay_bars(bars(1))

    def test_default_adapter_rejects_nonincreasing_dt_before_analysis(self):
        source = bars(2)
        source[0]["dt"], source[1]["dt"] = "z", "a"
        received = []
        module = types.ModuleType("chanlun_bi")
        module.analyze = lambda prefix, closes=None: received.append(prefix) or empty_structures()
        with patch.dict(sys.modules, {"chanlun_bi": module}):
            with self.assertRaisesRegex(ValueError, "dt.*递增"):
                replay_bars(source)
        self.assertEqual(received, [])
        self.assertEqual(replay_bars(source, lambda p: empty_structures())["bar_count"], 2)

    def test_invalid_input_rejected_before_any_analysis(self):
        cases = []
        for key, value in [("o", 0), ("h", float("inf")), ("l", float("nan")),
                           ("c", True), ("v", -1), ("v", False), ("dt", " "),
                           ("dt", 12), ("close_at", "2026-01-01T00:00:00"),
                           ("available_at", None), ("available_at", "2025-01-01T00:00:00Z"),
                           ("h", 10), ("l", 11)]:
            case = bars(2)
            case[0][key] = value
            cases.append(case)
        missing = bars(2)
        del missing[0]["v"]
        cases.append(missing)
        extra = bars(2)
        extra[0]["label"] = "future"
        cases.append(extra)
        cases.extend([{}, tuple(bars(1)), [None], bars(2001)])
        for case in cases:
            with self.subTest(case=str(case)[:160]):
                called = []
                with self.assertRaises(ValueError):
                    replay_bars(case, lambda p: called.append(p))
                self.assertEqual(called, [])

    def test_duplicate_dt_or_nonincreasing_times_rejected(self):
        for field in ("dt", "close_at", "available_at"):
            for reverse in (False, True):
                source = bars(2)
                source[1][field] = source[0][field]
                if reverse and field != "dt":
                    source[0][field], source[1][field] = bars(2)[1][field], bars(2)[0][field]
                with self.subTest(field=field, reverse=reverse):
                    with self.assertRaises(ValueError):
                        replay_bars(source, lambda p: empty_structures())

    def test_equivalent_timezone_instants_are_not_strictly_increasing(self):
        source = bars(2)
        source[1]["close_at"] = "2026-01-01T08:00:00+08:00"
        with self.assertRaises(ValueError):
            replay_bars(source, lambda p: empty_structures())

    def test_long_availability_delay_remains_explicit(self):
        source = bars(2)
        source[0]["available_at"] = "2026-01-02T00:00:00Z"
        source[1]["available_at"] = "2026-01-02T00:01:00Z"
        snapshots = replay_bars(source, lambda p: empty_structures())["snapshots"]
        self.assertEqual(snapshots[0]["visible_count"], 1)
        self.assertEqual(snapshots[0]["as_of"], normalize_time(source[0]["available_at"]))

    def test_unknown_or_non_json_algorithm_output_is_rejected(self):
        cases = [None, [], {}, dict(unknown=[]), dict(fractals={}),
                 dict(fractals=[1]), dict(fractals=[dict(price=1)]),
                 dict(fractals=[dict(dt="bar-0", price=float("nan"))]),
                 dict(fractals=[dict(dt="bar-0", price=(1, 2))]),
                 dict(fractals=[], merged_n=True)]
        for result in cases:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    replay_bars(bars(1), lambda p: result)

    def test_algorithm_cannot_reference_future_or_unknown_bar(self):
        for result in [dict(fractals=[dict(dt="bar-1")]),
                       dict(bis=[dict(s_dt="future", e_dt="bar-0")]),
                       dict(signals=[dict(d="future")])]:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    replay_bars(bars(2), lambda p: result)

    def test_duplicate_structure_versions_are_rejected(self):
        with self.assertRaises(ValueError):
            replay_bars(bars(1), lambda p: dict(fractals=[dict(dt="bar-0"), dict(dt="bar-0")]))

    def test_algorithm_id_and_analyzer_are_explicitly_validated(self):
        for algorithm_id in ("", " ", None, 1):
            with self.subTest(algorithm_id=algorithm_id):
                with self.assertRaises(ValueError):
                    replay_bars([], algorithm_id=algorithm_id)
        with self.assertRaises(ValueError):
            replay_bars([], analyzer=42)


if __name__ == "__main__":
    unittest.main()
