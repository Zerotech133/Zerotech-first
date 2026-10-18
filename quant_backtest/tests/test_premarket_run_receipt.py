"""Isolated synthetic premarket completion evidence tests (no producer or disk I/O)."""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from premarket_run_receipt import (ARTIFACT_NAMES, ByteEvidence, PremarketApproval,
                                   PremarketClaim, PremarketReceipt, SHANGHAI,
                                   validate_premarket_run)


@dataclass(frozen=True)
class Identity:
    """Fixture pretending the caller authenticated one complete CN calendar."""

    exchange: str
    market: str = "CN"
    timezone: str = "Asia/Shanghai"
    calendar_ref: str = "approved-calendar"
    version: str = "a" * 64
    snapshot_hash: str = "b" * 64
    source_authenticated: bool = True
    exception_completeness_verified: bool = True
    production_eligible: bool = True
    coverage_start: date = date(2026, 10, 1)
    coverage_end: date = date(2026, 11, 30)


class StubCalendar:
    """Explicit session map, never infer an absent date as open."""

    def __init__(self, exchange: str, sessions: dict[date, bool]) -> None:
        self.identity = Identity(exchange=exchange)
        self.sessions = sessions

    def is_open(self, session_date: date) -> bool:
        """Return a pinned open/closed state; uncovered dates raise."""
        return self.sessions[session_date]


def fixture() -> dict[str, object]:
    """Build one synthetic weekly run with a replaced same-day historical row."""
    day = date(2026, 10, 19)  # Monday; producer serves the next open day.
    service = date(2026, 10, 20)
    started = datetime(2026, 10, 19, 14, 30, tzinfo=SHANGHAI)
    completed = datetime(2026, 10, 19, 14, 32, tzinfo=SHANGHAI)
    approval = PremarketApproval(True, False, "c" * 64, "weekly_premarket",
                                 "premarket.py", ("--push",), "weekly",
                                 "next_calendar_day_both_exchanges_open", "policy-v1", 0,
                                 time(14, 0), time(15, 0), time(14, 30),
                                 timedelta(minutes=15), 100_000,
                                 (("SSE", "approved-calendar"),
                                  ("SZSE", "approved-calendar")),
                                 (("SSE", "a" * 64), ("SZSE", "a" * 64)),
                                 (("SSE", "b" * 64), ("SZSE", "b" * 64)))
    claim = PremarketClaim("run-19", approval.spec_hash, approval.task_id,
                           approval.action, approval.args, day, service, "completed",
                           approval.service_date_policy,
                           approval.service_date_policy_version)
    latest = {"ts": "2026-10-19 14:31", "l1": {"score": 15},
              "overall": 10, "prob": 75, "verdict": "偏多", "vals": {"外围": 15},
              "alerts": ["观察信号"]}
    row = {"date": "20261019", "overall": latest["overall"],
           "prob": latest["prob"], "verdict": latest["verdict"],
           "vals": latest["vals"], "l6_ann": None, "alerts": 1}
    earlier = {**row, "date": "20261016", "overall": 1}
    obsolete = {**row, "overall": 9}
    def serialize(value: object) -> bytes:
        """Encode synthetic JSON exactly once for byte-level comparisons."""
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    before: dict[str, bytes | None] = {
        ARTIFACT_NAMES[0]: serialize({"ts": "2026-10-16 14:31"}),
        ARTIFACT_NAMES[1]: serialize(earlier) + b"\n" + serialize(obsolete) + b"\n",
        ARTIFACT_NAMES[2]: b"<html>old</html>",
    }
    after: dict[str, bytes] = {
        ARTIFACT_NAMES[0]: serialize(latest),
        ARTIFACT_NAMES[1]: serialize(earlier) + b"\n" + serialize(row) + b"\n",
        ARTIFACT_NAMES[2]: "<html>盘前分析 2026-10-19 14:31 综合 10</html>".encode("utf-8"),
    }
    evidence = tuple(ByteEvidence(name, hashlib.sha256(before[name]).hexdigest(),
                                  len(before[name]), hashlib.sha256(after[name]).hexdigest(),
                                  len(after[name])) for name in ARTIFACT_NAMES)
    receipt = PremarketReceipt(claim.run_id, approval.spec_hash, approval.task_id,
                               approval.action, approval.args, day, service,
                               "controlled-runner", "independent_runner",
                               started, completed, 0, False, False, evidence,
                               approval.service_date_policy,
                               approval.service_date_policy_version)
    days = {day: True, service: True}
    return {"claim": claim, "approval": approval, "receipt": receipt,
            "artifacts": after, "previous": before,
            "calendar": {exchange: StubCalendar(exchange, days.copy())
                         for exchange in ("SSE", "SZSE")},
            "observed_at": completed + timedelta(minutes=1)}


class PremarketReceiptTests(unittest.TestCase):
    """Both acceptance and adversarial structural scenarios stay in memory."""

    def test_synthetic_valid_but_never_production(self) -> None:
        """Passing fixtures never establish a production trust root."""
        verdict = validate_premarket_run(**fixture())
        self.assertTrue(verdict.valid, verdict.reason_code)
        self.assertEqual(verdict.reason_code, "SYNTHETIC_STRUCTURALLY_CONSISTENT")
        self.assertFalse(verdict.production_permitted)
        self.assertEqual(verdict.service_date, date(2026, 10, 20))
        self.assertEqual(verdict.valid_until,
                         datetime(2026, 10, 26, 14, 45, tzinfo=SHANGHAI))

    def test_fresh_start_without_prior_files(self) -> None:
        """A truly absent predecessor is allowed when history has exactly one row."""
        data = fixture()
        for name in ARTIFACT_NAMES:
            data["previous"][name] = None
        final_line = data["artifacts"][ARTIFACT_NAMES[1]].splitlines()[-1] + b"\n"
        data["artifacts"][ARTIFACT_NAMES[1]] = final_line
        data["receipt"] = replace(data["receipt"], artifacts=tuple(
            replace(entry, before_sha256=None, before_size=None,
                    after_sha256=hashlib.sha256(data["artifacts"][entry.name]).hexdigest(),
                    after_size=len(data["artifacts"][entry.name]))
            for entry in data["receipt"].artifacts))
        self.assertTrue(validate_premarket_run(**data).valid)
        self.assertFalse(validate_premarket_run(**data).production_permitted)

    def test_weekly_expiry_strict_upper_bound(self) -> None:
        """The next weekly due instant plus grace is exclusive."""
        data = fixture()
        data["observed_at"] = datetime(2026, 10, 26, 14, 45, tzinfo=SHANGHAI)
        self.assertEqual(validate_premarket_run(**data).reason_code, "NEXT_RUN_OVERDUE")
        data["observed_at"] -= timedelta(microseconds=1)
        self.assertTrue(validate_premarket_run(**data).valid)

    def test_unapproved_force_skip_and_false_empty_batch(self) -> None:
        """Neither bypass nor zero-work self-report is completed evidence."""
        scenarios = [
            ("approval", "approved", False), ("approval", "revoked", True),
            ("approval", "period", "daily"), ("approval", "args", ("--force",)),
            ("approval", "service_date_policy", ""),
            ("approval", "service_date_policy", "same_day"),
            ("approval", "service_date_policy_version", ""),
            ("claim", "service_date_policy", ""),
            ("receipt", "service_date_policy_version", "other-version"),
            ("claim", "force", True), ("claim", "status", "skip"),
            ("receipt", "forced", True), ("receipt", "skipped", True),
            ("receipt", "exit_code", 1), ("receipt", "observation_origin", "producer"),
        ]
        for target, field, value in scenarios:
            with self.subTest(field=field):
                data = fixture()
                data[target] = replace(data[target], **{field: value})
                self.assertFalse(validate_premarket_run(**data).valid)
        data = fixture()
        data["artifacts"][ARTIFACT_NAMES[0]] = b'{}'
        self.assertFalse(validate_premarket_run(**data).valid)

    def test_calendar_requires_both_authenticated_complete_exchanges(self) -> None:
        """Missing coverage, exchange disagreement, and untrusted calendars deny."""
        for variant in ("closed_today", "closed_tomorrow", "mismatch", "coverage",
                        "untrusted", "missing"):
            with self.subTest(variant=variant):
                data = fixture()
                cal = data["calendar"]
                if variant == "closed_today":
                    for entry in cal.values():
                        entry.sessions[date(2026, 10, 19)] = False
                elif variant == "closed_tomorrow":
                    for entry in cal.values():
                        entry.sessions[date(2026, 10, 20)] = False
                elif variant == "mismatch":
                    cal["SSE"].sessions[date(2026, 10, 19)] = False
                elif variant == "coverage":
                    cal["SSE"].identity = replace(cal["SSE"].identity,
                                                   coverage_end=date(2026, 10, 18))
                elif variant == "untrusted":
                    cal["SSE"].identity = replace(cal["SSE"].identity,
                                                   source_authenticated=False)
                else:
                    del cal["SZSE"]
                self.assertFalse(validate_premarket_run(**data).valid)

    def test_before_after_digest_and_prior_history_are_bound(self) -> None:
        """No replay of old history nor substitution of any of three artifacts."""
        for name in ARTIFACT_NAMES:
            with self.subTest(artifact=name):
                data = fixture()
                data["artifacts"][name] += b" "
                self.assertEqual(validate_premarket_run(**data).reason_code,
                                 "ARTIFACT_BINDING_MISMATCH")
                data = fixture()
                data["previous"][name] = None
                self.assertFalse(validate_premarket_run(**data).valid)
        data = fixture()
        data["previous"][ARTIFACT_NAMES[1]] = b'{"date":"20261015"}\n'
        item = next(entry for entry in data["receipt"].artifacts
                    if entry.name == ARTIFACT_NAMES[1])
        new_before = data["previous"][ARTIFACT_NAMES[1]]
        amended = replace(item, before_sha256=hashlib.sha256(new_before).hexdigest(),
                          before_size=len(new_before))
        data["receipt"] = replace(data["receipt"],
                                  artifacts=tuple(amended if entry.name == item.name else entry
                                                  for entry in data["receipt"].artifacts))
        self.assertEqual(validate_premarket_run(**data).reason_code, "HISTORY_PRIOR_MISMATCH")
        data = fixture()
        old_history = data["previous"][ARTIFACT_NAMES[1]]
        data["previous"][ARTIFACT_NAMES[1]] = b""
        entry = data["receipt"].artifacts[1]
        data["receipt"] = replace(data["receipt"], artifacts=(
            data["receipt"].artifacts[0],
            replace(entry, before_sha256=hashlib.sha256(b"").hexdigest(), before_size=0),
            data["receipt"].artifacts[2]))
        self.assertEqual(validate_premarket_run(**data).reason_code, "HISTORY_PRIOR_MISMATCH")
        self.assertTrue(old_history)

    def test_missing_unchanged_or_reformatted_artifacts_fail(self) -> None:
        """All three files must be present, updated, and historically byte-preserving."""
        for name in ARTIFACT_NAMES:
            with self.subTest(name=name, case="missing"):
                data = fixture()
                del data["artifacts"][name]
                self.assertEqual(validate_premarket_run(**data).reason_code,
                                 "ARTIFACT_SET_INVALID")
            with self.subTest(name=name, case="unchanged"):
                data = fixture()
                data["artifacts"][name] = data["previous"][name]
                entry = next(item for item in data["receipt"].artifacts if item.name == name)
                amended = replace(entry, after_sha256=entry.before_sha256,
                                  after_size=entry.before_size)
                data["receipt"] = replace(data["receipt"], artifacts=tuple(
                    amended if item.name == name else item for item in data["receipt"].artifacts))
                self.assertEqual(validate_premarket_run(**data).reason_code,
                                 "ARTIFACT_UNCHANGED")
        data = fixture()
        old, fresh = (data["artifacts"][ARTIFACT_NAMES[1]].splitlines())
        # Same JSON values but a different raw historical prefix: reject rewriting.
        rewritten = json.dumps(json.loads(old), ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
        updated = rewritten + b"\n" + fresh + b"\n"
        data["artifacts"][ARTIFACT_NAMES[1]] = updated
        entry = data["receipt"].artifacts[1]
        data["receipt"] = replace(data["receipt"], artifacts=(
            data["receipt"].artifacts[0],
            replace(entry, after_sha256=hashlib.sha256(updated).hexdigest(),
                    after_size=len(updated)), data["receipt"].artifacts[2]))
        self.assertFalse(validate_premarket_run(**data).valid)

    def test_historical_line_ending_mutation_is_rejected(self) -> None:
        """Rebinding digests cannot hide a prior CRLF to final LF rewrite."""
        data = fixture()
        name = ARTIFACT_NAMES[1]
        prior = data["previous"][name]
        data["previous"][name] = prior.replace(b"\n", b"\r\n", 1)
        item = data["receipt"].artifacts[1]
        data["receipt"] = replace(data["receipt"], artifacts=(
            data["receipt"].artifacts[0],
            replace(item, before_sha256=hashlib.sha256(data["previous"][name]).hexdigest(),
                    before_size=len(data["previous"][name])),
            data["receipt"].artifacts[2]))
        self.assertEqual(validate_premarket_run(**data).reason_code, "HISTORY_PRIOR_MISMATCH")

    def test_display_time_and_mtime_cannot_replace_runner_time(self) -> None:
        """Legacy naive-minute ts is only corroboration, not a completion anchor."""
        data = fixture()
        data["receipt"] = replace(data["receipt"], completed_at=datetime(2026, 10, 19, 14, 32))
        self.assertEqual(validate_premarket_run(**data).reason_code, "TIME_INVALID")
        data = fixture()
        data["receipt"] = replace(data["receipt"], started_at=datetime(2026, 10, 19, 15, 30,
                                                                        tzinfo=timezone.utc))
        self.assertFalse(validate_premarket_run(**data).valid)
        data = fixture()
        data["receipt"] = None
        self.assertEqual(validate_premarket_run(**data).reason_code, "CLAIM_OR_RECEIPT_MISSING")
        data = fixture()
        data["artifacts"][ARTIFACT_NAMES[2]] = None
        self.assertFalse(validate_premarket_run(**data).valid)

    def test_untrusted_verifier_not_accepted_as_input(self) -> None:
        """There is deliberately no verifier callback to exploit with lambda True."""
        data = fixture()
        data["trusted_receipt_verifier"] = lambda _receipt: True
        with self.assertRaises(TypeError):
            validate_premarket_run(**data)

    def test_duplicates_naive_timestamp_and_claim_divergence(self) -> None:
        """Parser and binding checks deny producer-side self-attestation."""
        data = fixture()
        data["claim"] = replace(data["claim"], service_date=date(2026, 10, 21))
        self.assertFalse(validate_premarket_run(**data).valid)
        data = fixture()
        latest = b'{"ts":"2026-10-19 14:31","ts":"2026-10-19 14:32"}'
        data["artifacts"][ARTIFACT_NAMES[0]] = latest
        entry = data["receipt"].artifacts[0]
        data["receipt"] = replace(data["receipt"], artifacts=(
            replace(entry, after_sha256=hashlib.sha256(latest).hexdigest(),
                    after_size=len(latest)), *data["receipt"].artifacts[1:]))
        self.assertEqual(validate_premarket_run(**data).reason_code, "LATEST_INVALID")


if __name__ == "__main__":
    unittest.main()
