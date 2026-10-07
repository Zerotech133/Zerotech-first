"""时间切分、后验隔离与摘要完整校验的行为测试。"""

import copy
import hashlib
import unittest

from ai_operator.contracts import canonical_json, normalize_time
from ai_operator.dataset import split_dataset, summarize_split
from ai_operator.shadow import _POSTERIOR_FIELDS


BOUNDARIES = {
    "train_end": "2026-01-10T00:00:00Z",
    "validation_end": "2026-01-20T00:00:00Z",
    "test_end": "2026-01-30T00:00:00Z",
    "as_of": "2026-02-01T00:00:00Z",
}


def row(sample_id="a", day=2, **changes):
    value = {
        "sample_id": sample_id, "code": sample_id, "content_family_id": None,
        "decision_at": f"2026-01-{day:02d}T00:00:00Z",
        "feature_available_at": f"2026-01-{day:02d}T00:00:00Z",
        "label_end_at": f"2026-01-{day + 1:02d}T00:00:00Z",
        "label_available_at": f"2026-01-{day + 1:02d}T01:00:00Z",
        "features": {"price": 10, "volume": 12.5}, "target": 0.2,
        "target_kind": "forward_close_return", "horizon": 5, "label_policy": "calendar-v1/raw",
    }
    value.update(changes)
    return value


def split(rows, **changes):
    arguments = dict(BOUNDARIES, feature_allowlist=["price", "volume"])
    arguments.update(changes)
    return split_dataset(rows, **arguments)


def rehash(value):
    if not value["excluded"] and all(type(value[name]) is list for name in ["train", "validation", "test"]):
        rows = copy.deepcopy(value["train"] + value["validation"] + value["test"])
        for item in rows:
            for name in ["decision_at", "feature_available_at", "label_end_at", "label_available_at"]:
                item[name] = normalize_time(item[name])
        rows.sort(key=lambda item: (item["decision_at"], item["sample_id"]))
        value["input_hash"] = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    value["split_hash"] = hashlib.sha256(canonical_json({k: v for k, v in value.items()
                                                       if k != "split_hash"}).encode("utf-8")).hexdigest()
    return value


class DatasetTests(unittest.TestCase):
    def test_half_open_partitions_policy_and_hash(self):
        result = split([row("c", 21), row("a", 2), row("b", 11)])
        self.assertEqual([[r["sample_id"] for r in result[p]] for p in ["train", "validation", "test"]],
                         [["a"], ["b"], ["c"]])
        self.assertEqual(set(result), {"schema_version", "policy", "feature_allowlist", "train", "validation",
                                       "test", "excluded", "input_hash", "split_hash", "production_eligible"})
        self.assertEqual(result["policy"], {
            "train_end": "2026-01-10T00:00:00.000000+00:00",
            "validation_end": "2026-01-20T00:00:00.000000+00:00",
            "test_end": "2026-01-30T00:00:00.000000+00:00",
            "as_of": "2026-02-01T00:00:00.000000+00:00", "embargo_seconds": 0,
            "target_kind": "forward_close_return", "horizon": 5, "label_policy": "calendar-v1/raw",
            "group_policy": "family_or_code_utc_day_connected",
        })
        self.assertIs(result["production_eligible"], False)
        self.assertEqual(result["split_hash"], rehash(copy.deepcopy(result))["split_hash"])

    def test_label_end_and_availability_equality_are_purged(self):
        values = [row("end", 2, label_end_at=BOUNDARIES["train_end"], label_available_at=BOUNDARIES["train_end"]),
                  row("delay", 3, label_available_at=BOUNDARIES["train_end"]),
                  row("vend", 11, label_end_at=BOUNDARIES["validation_end"], label_available_at=BOUNDARIES["validation_end"]),
                  row("vdelay", 12, label_available_at=BOUNDARIES["validation_end"])]
        result = split(values)
        self.assertEqual(result["excluded"], [{"sample_id": "end", "reason": "purge_train"},
                                               {"sample_id": "delay", "reason": "purge_train"},
                                               {"sample_id": "vend", "reason": "purge_validation"},
                                               {"sample_id": "vdelay", "reason": "purge_validation"}])

    def test_immaturity_has_priority_and_test_can_end_after_window(self):
        values = [row("future", 2, label_available_at="2026-02-02T00:00:00Z"),
                  row("test", 29, label_end_at="2026-02-01T00:00:00Z", label_available_at=BOUNDARIES["as_of"])]
        result = split(values)
        self.assertEqual(result["excluded"], [{"sample_id": "future", "reason": "immature"}])
        self.assertEqual([r["sample_id"] for r in result["test"]], ["test"])

    def test_embargo_is_half_open_and_test_end_is_outside(self):
        result = split([row("v0", 10), row("v1", 11), row("t0", 20), row("t1", 21), row("out", 30)],
                       embargo_seconds=86400)
        self.assertEqual([r["sample_id"] for r in result["validation"]], ["v1"])
        self.assertEqual([r["sample_id"] for r in result["test"]], ["t1"])
        self.assertEqual(result["excluded"], [{"sample_id": "v0", "reason": "embargo"},
                                               {"sample_id": "t0", "reason": "embargo"},
                                               {"sample_id": "out", "reason": "outside_window"}])

    def test_zero_embargo_includes_exact_fold_start(self):
        result = split([row("v", 10), row("t", 20)])
        self.assertEqual([r["sample_id"] for r in result["validation"]], ["v"])
        self.assertEqual([r["sample_id"] for r in result["test"]], ["t"])

    def test_family_and_code_utc_day_form_transitive_connected_groups(self):
        values = [row("early", 2, content_family_id="family-a"),
                  row("bridge", 11, code="same", content_family_id="family-a"),
                  row("bridge2", 11, code="same", content_family_id="family-b",
                      decision_at="2026-01-12T07:00:00+08:00", feature_available_at="2026-01-11T22:00:00Z",
                      label_end_at="2026-01-13T00:00:00Z", label_available_at="2026-01-13T01:00:00Z"),
                  row("latest", 21, content_family_id="family-b")]
        result = split(values)
        self.assertEqual(result["train"], [])
        self.assertEqual(result["validation"], [])
        self.assertEqual([r["sample_id"] for r in result["test"]], ["latest"])
        self.assertEqual(result["excluded"], [{"sample_id": "early", "reason": "group_overlap"},
                                               {"sample_id": "bridge", "reason": "group_overlap"},
                                               {"sample_id": "bridge2", "reason": "group_overlap"}])

    def test_input_order_timezone_equivalence_and_no_mutation(self):
        values = [row("z", 2), row("a", 2), row("later", 21)]
        original = copy.deepcopy(values)
        result = split(values)
        self.assertEqual(result, split(list(reversed(values))))
        self.assertEqual([r["sample_id"] for r in result["train"]], ["a", "z"])
        equivalent = copy.deepcopy(values)
        equivalent[0]["decision_at"] = "2026-01-02T08:00:00+08:00"
        self.assertEqual(result, split(equivalent, train_end="2026-01-10T08:00:00+08:00"))
        self.assertEqual(values, original)
        result["train"][0]["features"]["price"] = 99
        self.assertEqual(values, original)

    def test_same_code_utc_day_directly_isolates_intraday_folds(self):
        values = [row("early", 10, code="same", decision_at="2026-01-10T01:00:00Z",
                      label_end_at="2026-01-10T02:00:00Z", label_available_at="2026-01-10T03:00:00Z"),
                  row("late", 10, code="same", decision_at="2026-01-11T01:00:00+08:00",
                      label_end_at="2026-01-11T02:00:00+08:00", label_available_at="2026-01-11T03:00:00+08:00")]
        result = split(values, train_end="2026-01-10T12:00:00Z")
        self.assertEqual(result["train"], [])
        self.assertEqual([r["sample_id"] for r in result["validation"]], ["late"])
        self.assertEqual(result["excluded"], [{"sample_id": "early", "reason": "group_overlap"}])
        self.assertEqual(result, split(list(reversed(values)), train_end="2026-01-10T12:00:00Z"))

    def test_purged_rows_do_not_bridge_candidate_groups(self):
        values = [row("keep", 2, content_family_id="early-family"),
                  row("purged", 9, code="same", content_family_id="early-family",
                      label_available_at="2026-01-11T00:00:00Z"),
                  row("later", 9, code="same", content_family_id="late-family",
                      label_end_at="2026-01-09T12:00:00Z", label_available_at="2026-01-09T13:00:00Z"),
                  row("test", 21, content_family_id="late-family")]
        result = split(values)
        self.assertEqual([r["sample_id"] for r in result["train"]], ["keep"])
        self.assertEqual(result["excluded"], [{"sample_id": "later", "reason": "group_overlap"},
                                               {"sample_id": "purged", "reason": "purge_train"}])

    def test_excluded_features_and_targets_are_bound_by_input_hash(self):
        for reason, available in [("purge_train", BOUNDARIES["train_end"]),
                                  ("immature", "2026-02-02T00:00:00Z")]:
            values = [row("excluded", 2, label_available_at=available), row("kept", 21)]
            baseline = split(values)
            self.assertEqual(baseline["excluded"], [{"sample_id": "excluded", "reason": reason}])
            self.assertEqual(baseline, split(list(reversed(values))))
            for changes in [{"target": 0.9}, {"features": {"price": 99, "volume": 12.5}}]:
                changed = copy.deepcopy(values)
                changed[0].update(changes)
                result = split(changed)
                with self.subTest(reason=reason, changes=changes):
                    self.assertNotEqual(baseline["split_hash"], result["split_hash"])
                    self.assertNotEqual(baseline["input_hash"], result["input_hash"])

    def test_empty_and_all_excluded_summaries_preserve_policy(self):
        empty = split([])
        self.assertIsNone(empty["policy"]["horizon"])
        self.assertIsNone(empty["policy"]["label_policy"])
        for value in [empty, split([row("a", 2, label_available_at=BOUNDARIES["train_end"])])]:
            summary = summarize_split(value)
            self.assertEqual(summary["partitions"]["train"], {"n": 0, "mean_target": None, "positive_rate": None})

    def test_feature_allowlist_rejects_posterior_duplicate_and_empty(self):
        for names in [[], ["price", "price"], [""], [" "], "price", ("price",), [1]]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                split([], feature_allowlist=names)
        for name in _POSTERIOR_FIELDS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                split([], feature_allowlist=[name])

    def test_rows_reject_unknown_missing_and_non_json(self):
        invalids = [{}, dict(row(), extra=1), dict(row(), content_family_id=""), dict(row(), code=" "),
                    dict(row(), sample_id=""), dict(row(), features={"price": 1}),
                    dict(row(), features={"price": 1, "volume": 1, "extra": 2}),
                    dict(row(), features={"price": (1,), "volume": 2}), dict(row(), features={1: 2}),
                    dict(row(), target_kind="pnl"), dict(row(), horizon=True), dict(row(), horizon=251),
                    dict(row(), horizon=0), dict(row(), label_policy=""), dict(row(), content_family_id=1)]
        for value in invalids:
            with self.subTest(value=value), self.assertRaises(ValueError):
                split([value])
        with self.assertRaises(ValueError):
            split([row(), row()])
        for values in [(row(),), {}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                split(values)

    def test_numeric_values_reject_bool_nan_infinity_and_none(self):
        for value in [True, False, None, "1", float("nan"), float("inf"), -float("inf")]:
            for changes in [{"target": value}, {"features": {"price": value, "volume": 1}}]:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    split([row(**changes)])

    def test_time_order_and_shared_label_policy_are_enforced(self):
        for changes in [{"feature_available_at": "2026-01-03T00:00:00Z"},
                        {"label_end_at": "2026-01-01T00:00:00Z"},
                        {"label_available_at": "2026-01-02T00:00:00Z"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                split([row(**changes)])
        for field in ["decision_at", "feature_available_at", "label_end_at", "label_available_at"]:
            for value in ["2026-01-02T00:00:00", None, 1]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    split([row(**{field: value})])
        for changes in [{"horizon": 20}, {"label_policy": "other"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                split([row(), row("b", **changes)])

    def test_empty_rows_still_validate_all_policy_parameters(self):
        for changes in [{"train_end": BOUNDARIES["validation_end"]}, {"test_end": "2026-02-02T00:00:00Z"},
                        {"as_of": "2026-02-01T00:00:00"}, {"embargo_seconds": True},
                        {"embargo_seconds": -1}, {"embargo_seconds": 0.1}, {"embargo_seconds": 10 ** 100}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                split([], **changes)


class SummaryTests(unittest.TestCase):
    def test_descriptive_statistics_and_exclusion_counts(self):
        value = split([row("a", 2, target=0.5), row("b", 3, target=-0.5), row("c", 4, target=0),
                       row("late", 11, label_available_at=BOUNDARIES["validation_end"])])
        result = summarize_split(value)
        self.assertEqual(result, {"split_hash": value["split_hash"],
                                  "partitions": {"train": {"n": 3, "mean_target": 0, "positive_rate": 1 / 3},
                                                 "validation": {"n": 0, "mean_target": None, "positive_rate": None},
                                                 "test": {"n": 0, "mean_target": None, "positive_rate": None}},
                                  "excluded_by_reason": {"purge_validation": 1}, "production_eligible": False})

    def test_raw_hash_mismatch_is_rejected(self):
        value = split([row()])
        value["train"][0]["target"] = 0.8
        with self.assertRaises(ValueError):
            summarize_split(value)

    def test_rehashed_time_leakage_and_wrong_partition_are_rejected(self):
        original = split([row()])
        for field, bad in [("label_end_at", BOUNDARIES["train_end"]),
                           ("label_available_at", BOUNDARIES["train_end"]),
                           ("label_available_at", "2026-02-02T00:00:00Z"),
                           ("feature_available_at", "2026-01-04T00:00:00Z")]:
            value = copy.deepcopy(original)
            value["train"][0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                summarize_split(rehash(value))
        value = copy.deepcopy(original)
        value["test"] = value["train"]
        value["train"] = []
        with self.assertRaises(ValueError):
            summarize_split(rehash(value))

    def test_rehashed_cross_fold_family_and_embargo_are_rejected(self):
        value = split([row("a", 2), row("b", 11)])
        value["train"][0]["content_family_id"] = "linked"
        value["validation"][0]["content_family_id"] = "linked"
        with self.assertRaises(ValueError):
            summarize_split(rehash(value))
        value = split([row("a", 10)])
        value["policy"]["embargo_seconds"] = 86400
        with self.assertRaises(ValueError):
            summarize_split(rehash(value))

    def test_rehashed_code_utc_day_overlap_is_rejected(self):
        values = [row("a", 10, decision_at="2026-01-10T01:00:00Z",
                      label_end_at="2026-01-10T02:00:00Z", label_available_at="2026-01-10T03:00:00Z"),
                  row("b", 10, decision_at="2026-01-10T17:00:00Z",
                      label_end_at="2026-01-10T18:00:00Z", label_available_at="2026-01-10T19:00:00Z")]
        value = split(values, train_end="2026-01-10T12:00:00Z")
        value["validation"][0]["code"] = value["train"][0]["code"]
        with self.assertRaises(ValueError):
            summarize_split(rehash(value))

    def test_input_hash_requires_lowercase_hex_and_reconstruction_when_possible(self):
        for excluded in [False, True]:
            original = split([row("a", 2, label_available_at=BOUNDARIES["train_end"])]) if excluded else split([row()])
            for bad in [None, 1, "", "g" * 64, "A" * 64, "0" * 63]:
                value = copy.deepcopy(original)
                value["input_hash"] = bad
                value["split_hash"] = hashlib.sha256(canonical_json({k: v for k, v in value.items()
                                                                   if k != "split_hash"}).encode("utf-8")).hexdigest()
                with self.subTest(excluded=excluded, bad=bad), self.assertRaises(ValueError):
                    summarize_split(value)
        value = split([row()])
        value["input_hash"] = "0" * 64
        value["split_hash"] = hashlib.sha256(canonical_json({k: v for k, v in value.items()
                                                           if k != "split_hash"}).encode("utf-8")).hexdigest()
        with self.assertRaises(ValueError):
            summarize_split(value)

    def test_rehashed_top_policy_and_excluded_schema_are_rejected(self):
        original = split([row()])
        variants = [dict(original, schema_version=True), dict(original, production_eligible=0),
                    dict(original, extra=1), dict(original, train=()),
                    dict(original, excluded=[{"sample_id": "a", "reason": "immature"}]),
                    dict(original, excluded=[{"sample_id": "x", "reason": "unknown"}]),
                    dict(original, excluded=[{"sample_id": "x", "reason": "immature", "extra": 1}]),
                    dict(original, excluded=[{"sample_id": "x", "reason": "immature"}] * 2)]
        for changes in [{"horizon": True}, {"label_policy": "other"}, {"target_kind": "pnl"},
                        {"group_policy": "none"}, {"extra": 1}, {"embargo_seconds": False}]:
            variants.append(dict(original, policy=dict(original["policy"], **changes)))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize_split(rehash(value))

    def test_extreme_finite_targets_do_not_produce_nonfinite_summary(self):
        value = split([row("a", 2, target=1e308), row("b", 3, target=1e308)])
        self.assertEqual(summarize_split(value)["partitions"]["train"]["mean_target"], 1e308)
        huge = split([row("a", 2, target=10 ** 1000), row("b", 3, target=1.0)])
        with self.assertRaises(ValueError):
            summarize_split(huge)


if __name__ == "__main__":
    unittest.main()
