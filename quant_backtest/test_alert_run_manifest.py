"""Synthetic isolated negative and positive evidence for the offline validator.

All roots below are synthetic: passing tests do not establish a trusted producer,
calendar authority, independently operating runner, or approval for deployment.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from typing import Callable
import unittest

from alert_run_manifest import (ApprovedSpec, ArtifactEvidence, CompletionReceipt,
                                NineDimManifest, validate_nine_dim_run)


CN = timezone(timedelta(hours=8))
RUN_DAY = date(2026, 10, 18)  # Sunday; prior synthetic Thursday is last open day.
SERVICE_DAY = date(2026, 10, 15)
RUN_AT = datetime(2026, 10, 18, 15, 41, tzinfo=CN)
OBSERVED = datetime(2026, 10, 18, 16, tzinfo=CN)
VERSIONS = (("SSE", "a" * 64), ("SZSE", "b" * 64))
SNAPSHOT_HASHES = (("SSE", "d" * 64), ("SZSE", "e" * 64))
REFS = (("SSE", "registry:synthetic-sse"), ("SZSE", "registry:synthetic-szse"))


class SyntheticCalendar:
    """Entirely synthetic verified-looking identity, never a production source."""

    def __init__(self, exchange: str, start: date = date(2026, 10, 14),
                 end: date = date(2026, 10, 18),
                 open_days: frozenset[date] = frozenset({SERVICE_DAY})) -> None:
        self.open_days = open_days
        self.identity = SimpleNamespace(exchange=exchange, market="CN", timezone="Asia/Shanghai",
                                        version=dict(VERSIONS)[exchange], snapshot_hash=dict(SNAPSHOT_HASHES)[exchange],
                                        calendar_ref=dict(REFS)[exchange],
                                        source_authenticated=True, exception_completeness_verified=True,
                                        production_eligible=True, coverage_start=start, coverage_end=end)

    def is_open(self, session_date: date, *, as_of: datetime | None = None) -> bool:
        if not self.identity.coverage_start <= session_date <= self.identity.coverage_end:
            raise ValueError("uncovered synthetic date")
        return session_date in self.open_days

    def session_bounds(self, session_date: date, *, as_of: datetime | None = None) -> object:
        if not self.is_open(session_date):
            raise ValueError("closed")
        return SimpleNamespace(session_date=session_date,
                               close_at=datetime.combine(session_date, time(15, 0), CN))


def specimen(*, rows: list[dict[str, object]] | None = None,
             snapshot: dict[str, object] | None = None,
             history: bytes | None = None,
             run_at: datetime = RUN_AT) -> dict[str, object]:
    """Build a fresh and wholly synthetic attestation fixture."""
    spec = ApprovedSpec(spec_hash="c" * 64, approved=True, revoked=False,
                        task_id="synthetic-task", action="synthetic-action", args=("--synthetic",),
                        calendar_refs=REFS, calendar_versions=VERSIONS,
                        calendar_snapshot_hashes=SNAPSHOT_HASHES,
                        schedule_weekday=6, window_start=time(15, 30), window_end=time(16, 0),
                        next_due_time=time(15, 45), due_grace=timedelta(minutes=7),
                        max_lookback_days=7,
                        artifact_names=("nine_dim_scores.json", "nine_dim_pool_eval_cache.json",
                                        "nine_dim_history.jsonl"), max_artifact_bytes=100_000,
                        max_total_bytes=200_000, min_candidates=1, min_full_ratio=0.5,
                        min_cov=0.75, alert_threshold=75, strong_alert_threshold=85,
                        mid_threshold=60, buy_threshold=90)
    selected = rows if rows is not None else [dict(code="600001.SH", score=50, cov=0.9,
                                                    full=True, band="low")]
    doc: dict[str, object] = dict(generated_at=datetime(2026, 10, 18, 15, 37, tzinfo=CN).isoformat(),
                                  trade_date="20261015", n_cand=len(selected), n_full=1,
                                  n_buy=0, n_alert=0, min_cov=0.75, alert_th=75, buy_th=90,
                                  rows=selected)
    if snapshot:
        doc.update(snapshot)
    artifacts = {"nine_dim_scores.json": json.dumps(doc).encode(),
                 "nine_dim_pool_eval_cache.json": json.dumps({
                     "generated_at": datetime(2026, 10, 18, 15, 40, tzinfo=CN).isoformat(),
                     "n_pool": 0, "r5": {}, "r20": {}}).encode()}
    if history is not None:
        artifacts["nine_dim_history.jsonl"] = history
    receipt = CompletionReceipt(run_id="synthetic-run", spec_hash=spec.spec_hash,
                                calendar_versions=VERSIONS, calendar_snapshot_hashes=SNAPSHOT_HASHES,
                                task_id=spec.task_id,
                                action=spec.action, args=spec.args, run_date=RUN_DAY,
                                service_date=SERVICE_DAY, completed_at=run_at, exit_code=0,
                                final_artifacts=tuple(ArtifactEvidence(name, hashlib.sha256(data).hexdigest(),
                                                                       len(data)) for name, data in artifacts.items()),
                                history_mode="unchanged_no_new_alert", new_alert_count=0)
    manifest = NineDimManifest(run_id=receipt.run_id, spec_hash=receipt.spec_hash,
                               calendar_versions=receipt.calendar_versions,
                               calendar_snapshot_hashes=receipt.calendar_snapshot_hashes,
                               task_id=receipt.task_id,
                               action=receipt.action, args=receipt.args, run_date=receipt.run_date,
                               service_date=receipt.service_date, status="success")
    return dict(manifest=manifest, artifacts=artifacts, approved_spec=spec,
                calendar={exchange: SyntheticCalendar(exchange) for exchange in ("SSE", "SZSE")},
                completion=receipt, observed_at=OBSERVED)


class NineDimManifestTests(unittest.TestCase):
    """Adversarial checks never invoking an actual producer or external resource."""

    def check_rejection(self, expected: str, **changes: object) -> None:
        fixture = specimen()
        fixture.update(changes)
        result = validate_nine_dim_run(**fixture)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason_code, expected)
        self.assertIsNone(result.valid_until)

    def test_synthetic_zero_alert_is_not_production_attestation(self) -> None:
        result = validate_nine_dim_run(**specimen())
        self.assertTrue(result.valid)
        self.assertEqual(result.reason_code, "SYNTHETIC_OR_EXTERNALLY_ATTESTED_VALID")
        self.assertEqual(result.service_date, SERVICE_DAY)
        self.assertEqual(result.completed_at, RUN_AT)

    def test_missing_trust_roots_and_self_reported_success(self) -> None:
        self.check_rejection("SPEC_MISSING", approved_spec=None)
        self.check_rejection("CALENDAR_MISSING", calendar=None)
        self.check_rejection("COMPLETION_MISSING", completion=None)
        self.check_rejection("SPEC_NOT_APPROVED", approved_spec=replace(specimen()["approved_spec"], revoked=True))
        self.check_rejection("CALENDAR_UNTRUSTED", calendar={"SSE": SyntheticCalendar("SSE"),
                                                                "SZSE": None})

    def test_calendar_snapshot_hash_pin_and_due_inclusive_boundary(self) -> None:
        fixture = specimen()
        fixture["calendar"]["SSE"].identity.snapshot_hash = "f" * 64
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "CALENDAR_UNTRUSTED")
        fixture = specimen()
        fixture["completion"] = replace(fixture["completion"],
                                        calendar_snapshot_hashes=(("SSE", "f" * 64), SNAPSHOT_HASHES[1]))
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "RECEIPT_BINDING_MISMATCH")
        fixture = specimen()
        fixture["observed_at"] = datetime(2026, 10, 25, 15, 52, tzinfo=CN)
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "NEXT_RUN_OVERDUE")

    def test_perf_only_and_dryrun_are_not_eligible(self) -> None:
        base = specimen()["approved_spec"]
        self.check_rejection("SPEC_INVALID", approved_spec=replace(base, args=("--perf",)))
        self.check_rejection("SPEC_INVALID", approved_spec=replace(base, args=("--dryrun",)))

    def test_identity_and_runner_binding(self) -> None:
        original = specimen()
        self.check_rejection("RECEIPT_BINDING_MISMATCH",
                             completion=replace(original["completion"], run_id="old-run"))
        self.check_rejection("POLICY_BINDING_MISMATCH",
                             manifest=replace(original["manifest"], spec_hash="d" * 64),
                             completion=replace(original["completion"], spec_hash="d" * 64))
        self.check_rejection("COMPLETION_FAILED", completion=replace(original["completion"], exit_code=1))
        self.check_rejection("COMPLETION_TIME_INVALID",
                             completion=replace(original["completion"], completed_at=RUN_AT.replace(tzinfo=None)))
        self.check_rejection("OBSERVED_TIME_INVALID", observed_at=OBSERVED.replace(tzinfo=None))

    def test_old_snapshot_naive_and_future(self) -> None:
        for generated, reason in (("2026-10-11T15:37:00+08:00", "SNAPSHOT_STALE"),
                                  ("2026-10-18 15:37:00", "SNAPSHOT_TIME_INVALID"),
                                  ("2026-10-18T15:59:00+08:00", "SNAPSHOT_STALE")):
            fixture = specimen(snapshot={"generated_at": generated})
            self.assertEqual(validate_nine_dim_run(**fixture).reason_code, reason)

    def test_window_next_due_and_future(self) -> None:
        for run_at, expected in ((datetime(2026, 10, 18, 15, 25, tzinfo=CN), "COMPLETION_OUTSIDE_WINDOW"),
                                 (datetime(2026, 10, 18, 16, 1, tzinfo=CN), "COMPLETION_OUTSIDE_WINDOW")):
            fixture = specimen(run_at=run_at)
            fixture["observed_at"] = run_at + timedelta(minutes=10)
            self.assertEqual(validate_nine_dim_run(**fixture).reason_code, expected)
        self.check_rejection("NEXT_RUN_OVERDUE", observed_at=datetime(2026, 10, 25, 15, 53, tzinfo=CN))
        original = specimen()
        self.check_rejection("COMPLETION_FUTURE", completion=replace(original["completion"],
                                                                     completed_at=OBSERVED + timedelta(minutes=1)))

    def test_only_snapshot_and_bad_artifact_evidence(self) -> None:
        original = specimen()
        self.check_rejection("ARTIFACT_SET_INVALID", artifacts={"nine_dim_scores.json":
                                                                  original["artifacts"]["nine_dim_scores.json"]})
        data = dict(original["artifacts"])
        data["nine_dim_pool_eval_cache.json"] = b"altered"
        self.check_rejection("ARTIFACT_INTEGRITY_INVALID", artifacts=data)
        receipt = replace(original["completion"], final_artifacts=(replace(
            original["completion"].final_artifacts[0], size=4),) + original["completion"].final_artifacts[1:])
        self.check_rejection("ARTIFACT_INTEGRITY_INVALID", completion=receipt)
        bad = dict(original["artifacts"])
        bad["../escaped"] = b"bad"
        self.check_rejection("ARTIFACT_PATH_INVALID", artifacts=bad)
        too_big = replace(original["approved_spec"], max_artifact_bytes=10)
        self.check_rejection("ARTIFACT_TOO_LARGE", approved_spec=too_big)

    def test_bad_json_count_duplicate_nonfinite_coverage(self) -> None:
        original = specimen()
        artifacts = dict(original["artifacts"])
        artifacts["nine_dim_scores.json"] = b"{broken"
        receipt = replace(original["completion"], final_artifacts=tuple(
            ArtifactEvidence(name, hashlib.sha256(data).hexdigest(), len(data))
            for name, data in artifacts.items()))
        self.check_rejection("SNAPSHOT_INVALID", artifacts=artifacts, completion=receipt)
        cases = [({"n_full": 0}, "SNAPSHOT_COUNT_INVALID"),
                 ({"rows": [dict(code="600001.SH", score=50, cov=0.9, full=True, band="low")] * 2,
                   "n_cand": 2, "n_full": 2}, "SNAPSHOT_CODE_INVALID"),
                 ({"rows": [dict(code="600001.SH", score=float("nan"), cov=0.9, full=True,
                                 band="low")]}, "SNAPSHOT_INVALID"),
                 ({"rows": [dict(code="600001.SH", score=50, cov=0.2, full=True,
                                 band="low")]}, "SNAPSHOT_COVERAGE_INVALID")]
        for change, expected in cases:
            with self.subTest(change=change):
                self.assertEqual(validate_nine_dim_run(**specimen(snapshot=change)).reason_code, expected)

    def test_nested_duplicate_snapshot_and_eval_keys_rejected(self) -> None:
        fixture = specimen()
        artifacts = dict(fixture["artifacts"])
        snapshot = json.loads(artifacts["nine_dim_scores.json"])
        snapshot["rows"] = [dict(code="600001.SH", score=50, cov=0.9,
                                 full=True, band="low")]
        encoded = json.dumps(snapshot, separators=(",", ":")).encode()
        artifacts["nine_dim_scores.json"] = encoded.replace(
            b'"code":"600001.SH"', b'"code":"INVALID","code":"600001.SH"')
        fixture["artifacts"] = artifacts
        fixture["completion"] = replace(fixture["completion"], final_artifacts=tuple(
            ArtifactEvidence(name, hashlib.sha256(data).hexdigest(), len(data))
            for name, data in artifacts.items()))
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "SNAPSHOT_INVALID")
        fixture = specimen()
        artifacts = dict(fixture["artifacts"])
        artifacts["nine_dim_pool_eval_cache.json"] = artifacts["nine_dim_pool_eval_cache.json"].replace(
            b'"r5": {}', b'"r5": {"all": {"n": 0, "n": 1}}')
        fixture["artifacts"] = artifacts
        fixture["completion"] = replace(fixture["completion"], final_artifacts=tuple(
            ArtifactEvidence(name, hashlib.sha256(data).hexdigest(), len(data))
            for name, data in artifacts.items()))
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "EVAL_INVALID")

    def test_raw_coverage_rounding_and_negative_score(self) -> None:
        # Producer evaluates full on raw cov, THEN publishes round(raw, 2).
        # Both .749/full=False and .751/full=True display .75; neither claim
        # proves which side of the threshold the raw value actually occupied.
        reliable = dict(code="000001.SZ", score=-2.5, cov=0.9, full=True, band="low")
        for full, score, band in ((False, None, "na"), (True, 42, "low")):
            with self.subTest(full=full):
                rows = [dict(code="600001.SH", score=score, cov=0.75,
                             full=full, band=band), reliable]
                fixture = specimen(rows=rows, snapshot={"n_full": 1 + int(full)})
                result = validate_nine_dim_run(**fixture)
                self.assertFalse(result.valid)
                self.assertEqual(result.reason_code, "SNAPSHOT_COVERAGE_AMBIGUOUS")
        # Non-critical rounded coverages preserve the existing full/score rules.
        self.assertTrue(validate_nine_dim_run(**specimen(rows=[reliable])).valid)
        for cov, full, score, band in ((0.73, True, 30, "low"),
                                       (0.77, False, None, "na")):
            with self.subTest(cov=cov, full=full):
                rows = [dict(code="600001.SH", score=score, cov=cov,
                             full=full, band=band), reliable]
                altered = specimen(rows=rows, snapshot={"n_full": 1 + int(full)})
                self.assertEqual(validate_nine_dim_run(**altered).reason_code,
                                 "SNAPSHOT_COVERAGE_INVALID")

    def test_coverage_rounding_interval_adjacent_to_threshold(self) -> None:
        # At .74 no raw >= .75 rounds to .74, and .76 cannot come from raw < .75.
        full_row = dict(code="000001.SZ", score=-1.0, cov=0.9, full=True, band="low")
        for cov, full, score, band in ((0.74, False, None, "na"),
                                       (0.76, True, -1.0, "low")):
            with self.subTest(cov=cov):
                rows = [dict(code="600001.SH", score=score, cov=cov,
                             full=full, band=band), full_row]
                fixture = specimen(rows=rows, snapshot={"n_full": 1 + int(full)})
                self.assertTrue(validate_nine_dim_run(**fixture).valid)

    def test_run_day_open_never_selects_run_day(self) -> None:
        fixture = specimen()
        fixture["calendar"] = {
            exchange: SyntheticCalendar(exchange, open_days=frozenset({RUN_DAY, SERVICE_DAY}))
            for exchange in ("SSE", "SZSE")}
        result = validate_nine_dim_run(**fixture)
        self.assertTrue(result.valid)
        self.assertEqual(result.service_date, SERVICE_DAY)
        # A self-consistent claim of Sunday's own K must still be rejected.
        fixture["manifest"] = replace(fixture["manifest"], service_date=RUN_DAY)
        fixture["completion"] = replace(fixture["completion"], service_date=RUN_DAY)
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "SERVICE_DATE_MISMATCH")

    def test_immediately_prior_open_day_and_lookback_inclusive_boundary(self) -> None:
        prior = RUN_DAY - timedelta(days=1)
        fixture = specimen(snapshot={"trade_date": prior.strftime("%Y%m%d")})
        fixture["calendar"] = {
            exchange: SyntheticCalendar(exchange, open_days=frozenset({RUN_DAY, prior, SERVICE_DAY}))
            for exchange in ("SSE", "SZSE")}
        fixture["manifest"] = replace(fixture["manifest"], service_date=prior)
        fixture["completion"] = replace(fixture["completion"], service_date=prior)
        self.assertEqual(validate_nine_dim_run(**fixture).service_date, prior)

        # Thursday is three days back, exactly at the accepted limit.
        fixture = specimen()
        fixture["approved_spec"] = replace(fixture["approved_spec"], max_lookback_days=3)
        self.assertEqual(validate_nine_dim_run(**fixture).service_date, SERVICE_DAY)
        fixture["approved_spec"] = replace(fixture["approved_spec"], max_lookback_days=2)
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "CALENDAR_COVERAGE_MISSING")
        fixture = specimen()
        fixture["calendar"]["SZSE"].identity.coverage_start = RUN_DAY - timedelta(days=2)
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "CALENDAR_COVERAGE_MISSING")

    def test_long_holiday_coverage_missing_and_service_mismatch(self) -> None:
        self.check_rejection("CALENDAR_COVERAGE_MISSING", calendar={
            exchange: SyntheticCalendar(exchange, start=date(2026, 10, 17))
            for exchange in ("SSE", "SZSE")})
        original = specimen()
        self.check_rejection("SERVICE_DATE_MISMATCH",
                             manifest=replace(original["manifest"], service_date=date(2026, 10, 16)),
                             completion=replace(original["completion"], service_date=date(2026, 10, 16)))

    def test_prior_day_exchange_disagreement_and_close_time_fail_closed(self) -> None:
        fixture = specimen()
        fixture["calendar"]["SSE"].open_days = frozenset({RUN_DAY, SERVICE_DAY})
        fixture["calendar"]["SZSE"].open_days = frozenset({RUN_DAY})
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "CALENDAR_EXCHANGE_MISMATCH")

        # A prior session cannot be used before both exchanges' close.
        fixture = specimen()
        prior = RUN_DAY - timedelta(days=1)
        fixture["calendar"] = {
            exchange: SyntheticCalendar(exchange, open_days=frozenset({prior, SERVICE_DAY}))
            for exchange in ("SSE", "SZSE")}
        for exchange in ("SSE", "SZSE"):
            original_bounds = fixture["calendar"][exchange].session_bounds
            def future_bounds(session_date: date, *, as_of: datetime | None = None,
                              original: Callable[..., object] = original_bounds) -> object:
                bounds = original(session_date, as_of=as_of)
                if session_date == prior:
                    return SimpleNamespace(session_date=prior,
                                           close_at=RUN_AT + timedelta(minutes=1))
                return bounds
            fixture["calendar"][exchange].session_bounds = future_bounds
        result = validate_nine_dim_run(**fixture)
        self.assertTrue(result.valid)
        self.assertEqual(result.service_date, SERVICE_DAY)

    def test_eval_naive_timestamp_and_unpinned_history_rejected(self) -> None:
        fixture = specimen()
        artifacts = dict(fixture["artifacts"])
        artifacts["nine_dim_pool_eval_cache.json"] = b'{"generated_at":"2026-10-18 15:40:00","n_pool":0,"r5":{},"r20":{}}'
        fixture["completion"] = replace(fixture["completion"], final_artifacts=tuple(
            ArtifactEvidence(name, hashlib.sha256(data).hexdigest(), len(data))
            for name, data in artifacts.items()))
        fixture["artifacts"] = artifacts
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "EVAL_TIME_INVALID")
        no_pin = specimen(history=b'{"code":"600001.SH","dt":"20261015"}\n')
        self.assertEqual(validate_nine_dim_run(**no_pin).reason_code, "HISTORY_RECEIPT_INVALID")

    def test_history_duplicate_keys_invalid_dates_and_nonfinite_values(self) -> None:
        for data in (b'{"code":"INVALID","code":"600001.SH","dt":"20261015"}\n',
                     b'{"code":"600001.SH","dt":"20260230"}\n',
                     b'{"code":"600001.SH","dt":"20261015","score":NaN}\n',
                     b'{"code":"600001.SH","dt":"20261015","detail":{"x":1,"x":2}}\n'):
            with self.subTest(data=data):
                alert = dict(code="600001.SH", score=80, cov=0.9, full=True, band="alert")
                fixture = specimen(rows=[alert], snapshot={"n_alert": 1}, history=data)
                artifacts = dict(fixture["artifacts"])
                artifacts["nine_dim_pool_eval_cache.json"] = json.dumps({
                    "generated_at": datetime(2026, 10, 18, 15, 40, tzinfo=CN).isoformat(),
                    "n_pool": 1, "r5": {}, "r20": {}}).encode()
                fixture.update(artifacts=artifacts,
                               completion=replace(fixture["completion"],
                                                  history_mode="written", new_alert_count=1,
                                                  final_artifacts=tuple(
                                                      ArtifactEvidence(name, hashlib.sha256(blob).hexdigest(),
                                                                       len(blob)) for name, blob in artifacts.items())))
                self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "HISTORY_INVALID")

    def test_no_history_rejects_alert_even_with_matching_final_receipt(self) -> None:
        alert = dict(code="600001.SH", score=80, cov=0.9, full=True, band="alert")
        fixture = specimen(rows=[alert], snapshot={"n_alert": 1})
        # specimen hashes the altered final snapshot into the independent receipt.
        self.assertEqual(fixture["completion"].final_artifacts[0].sha256,
                         hashlib.sha256(fixture["artifacts"]["nine_dim_scores.json"]).hexdigest())
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "HISTORY_RECEIPT_INVALID")
        # The otherwise identical no-history, zero-pool, zero-alert batch is eligible.
        self.assertTrue(validate_nine_dim_run(**specimen()).valid)

    def test_unchanged_prior_history_allows_zero_new_alerts(self) -> None:
        history = b'{"code":"600002.SH","dt":"20261014"}\n'
        alert = dict(code="600001.SH", score=80, cov=0.9, full=True, band="alert")
        fixture = specimen(rows=[alert], snapshot={"n_alert": 1}, history=history)
        artifacts = dict(fixture["artifacts"])
        eval_doc = json.loads(artifacts["nine_dim_pool_eval_cache.json"])
        eval_doc["n_pool"] = 1
        artifacts["nine_dim_pool_eval_cache.json"] = json.dumps(eval_doc).encode()
        fixture["artifacts"] = artifacts
        fixture["completion"] = replace(
            fixture["completion"], prior_history_sha256=hashlib.sha256(history).hexdigest(),
            final_artifacts=tuple(ArtifactEvidence(name, hashlib.sha256(data).hexdigest(), len(data))
                                  for name, data in artifacts.items()))
        self.assertTrue(validate_nine_dim_run(**fixture).valid)

    def test_history_requires_explicit_new_alert_and_final_bytes(self) -> None:
        fixture = specimen(history=b'{"code":"600001.SH","dt":"20261015"}\n')
        self.assertEqual(validate_nine_dim_run(**fixture).reason_code, "HISTORY_RECEIPT_INVALID")
        alert = dict(code="600001.SH", score=80, cov=0.9, full=True, band="alert")
        fixture = specimen(rows=[alert], snapshot={"n_alert": 1},
                           history=b'{"code":"600001.SH","dt":"20261015"}\n')
        artifacts = dict(fixture["artifacts"])
        artifacts["nine_dim_pool_eval_cache.json"] = json.dumps({
            "generated_at": datetime(2026, 10, 18, 15, 40, tzinfo=CN).isoformat(),
            "n_pool": 1, "r5": {}, "r20": {}}).encode()
        receipt = replace(fixture["completion"], history_mode="written", new_alert_count=1,
                          final_artifacts=tuple(ArtifactEvidence(name, hashlib.sha256(data).hexdigest(), len(data))
                                                for name, data in artifacts.items()))
        fixture.update(artifacts=artifacts, completion=receipt)
        self.assertTrue(validate_nine_dim_run(**fixture).valid)


if __name__ == "__main__":
    unittest.main()
