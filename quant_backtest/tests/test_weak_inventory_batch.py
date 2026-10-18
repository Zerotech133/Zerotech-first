# -*- coding: utf-8 -*-
"""Pure batch receipt tests without a production runner or service."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weak_inventory_batch import (
    ApprovedInventorySpec, InventoryMember, InventorySnapshot, RunnerReceipt,
    SCHEMA, SCOPE, approved_spec_digest, snapshot_digest, validate_inventory_batch,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def fixture(empty: bool = False):
    members = () if empty else (
        InventoryMember("weak_track_000001.jsonl", hashlib.sha256(b"a").hexdigest(), 1, 1, False),
        InventoryMember("weak_track_000001.closed_20261017.jsonl", hashlib.sha256(b"b").hexdigest(), 1, 1, True),
    )
    snapshot = InventorySnapshot(SCOPE, "reports:weak-inventory", members,
                                 sum(not item.closed for item in members),
                                 snapshot_digest(SCOPE, "reports:weak-inventory", members))
    spec = ApprovedInventorySpec(SCHEMA, snapshot.root_ref, "f" * 64, SCOPE,
                                 "weak_case_inventory", "scan", ("--inventory-only",),
                                 "daily", timedelta(days=1),
                                 NOW - timedelta(hours=1), NOW + timedelta(hours=1),
                                 timedelta(minutes=5))
    spec = replace(spec, approved_spec_sha256=approved_spec_digest(spec))
    receipt = RunnerReceipt(SCHEMA, snapshot.root_ref, "run-1", spec.spec_hash,
                            SCOPE, spec.task, spec.action, spec.args, spec.period,
                            snapshot.active_count, len(members), snapshot.snapshot_sha256,
                            "trusted-runner", NOW - timedelta(minutes=1),
                            NOW + timedelta(hours=1))
    return snapshot, spec, receipt


def verdict(snapshot, spec, receipt, *, verifier=lambda _receipt: True, now=NOW):
    return validate_inventory_batch(snapshot, spec, receipt, now,
                                    ("trusted-runner",), verifier,
                                    lambda _ref: spec)


def test_closed_members_are_integrity_bound_but_not_counted_active():
    snapshot, spec, receipt = fixture()
    result = verdict(snapshot, spec, receipt)
    assert result.status == "VALID" and result.production_permitted is False
    assert snapshot.active_count == 1 and len(snapshot.all_members) == 2
    changed = replace(snapshot.all_members[1], sha256="0" * 64)
    tampered = replace(snapshot, all_members=(snapshot.all_members[0], changed))
    assert verdict(tampered, spec, receipt).error_code == "INVENTORY_INTEGRITY_MISMATCH"
    repinned = replace(tampered, snapshot_sha256=snapshot_digest(SCOPE, snapshot.root_ref,
                                                                tampered.all_members))
    assert verdict(repinned, spec, receipt).error_code == "INVENTORY_RECEIPT_MISMATCH"


def test_missing_independent_approved_spec_never_grants_valid():
    snapshot, spec, receipt = fixture()
    assert validate_inventory_batch(snapshot, spec, receipt, NOW,
                                    ("trusted-runner",), lambda _receipt: True).error_code == "INVENTORY_SPEC_UNVERIFIED"
    assert validate_inventory_batch(snapshot, spec, receipt, NOW,
                                    ("trusted-runner",), lambda _receipt: True,
                                    lambda _ref: None).error_code == "INVENTORY_SPEC_UNVERIFIED"


def test_zero_files_requires_same_trusted_receipt():
    snapshot, spec, receipt = fixture(empty=True)
    assert verdict(snapshot, spec, receipt).status == "VALID_EMPTY"
    assert verdict(snapshot, spec, receipt, verifier=lambda _receipt: False).error_code == "INVENTORY_RECEIPT_UNVERIFIED"


def test_receipt_must_match_exact_approved_task_and_all_arguments():
    snapshot, spec, receipt = fixture()
    for changed in (replace(receipt, args=("--weekly",)),
                    replace(receipt, scope="candidate_scan"),
                    replace(receipt, active_count=9),
                    replace(receipt, spec_hash="0" * 64),
                    replace(receipt, root_ref="other"),
                    replace(receipt, run_id="")):
        assert verdict(snapshot, spec, changed).error_code == "INVENTORY_RECEIPT_MISMATCH"
    assert verdict(snapshot, spec, replace(receipt, runner_id="forged")).error_code == "INVENTORY_RECEIPT_UNVERIFIED"


def test_approved_spec_pin_rejects_scope_period_and_age_changes():
    snapshot, spec, receipt = fixture()
    for changed in (replace(spec, period="weekly"),
                    replace(spec, max_age=timedelta(days=365)),
                    replace(spec, period_start_at=NOW - timedelta(days=1)),
                    replace(spec, next_due_at=NOW + timedelta(days=10)),
                    replace(spec, grace=timedelta(days=10)),
                    replace(spec, root_ref="other"),
                    replace(spec, scope="candidate_scan")):
        assert verdict(snapshot, changed, receipt).status == "INVALID"
    assert verdict(snapshot, replace(spec, approved_spec_sha256=""), receipt).error_code == "INVENTORY_SPEC_UNVERIFIED"


def test_expiry_future_and_timezone_naivety_fail_closed():
    snapshot, spec, receipt = fixture()
    assert verdict(snapshot, spec, receipt, now=NOW + timedelta(days=2)).error_code == "INVENTORY_RECEIPT_EXPIRED"
    assert verdict(snapshot, spec, replace(receipt, completed_at=NOW + timedelta(seconds=1))).error_code == "INVENTORY_RECEIPT_EXPIRED"
    assert verdict(snapshot, spec, replace(receipt, completed_at=NOW.replace(tzinfo=None))).error_code == "INVENTORY_TIME_INVALID"


def test_receipt_cannot_extend_independently_approved_due_time():
    snapshot, spec, receipt = fixture()
    forged = replace(receipt, next_due_at=NOW + timedelta(days=365))
    assert verdict(snapshot, spec, forged).error_code == "INVENTORY_RECEIPT_MISMATCH"
    assert verdict(snapshot, spec, forged, now=NOW + timedelta(hours=2)).status == "INVALID"
    same_instant = replace(receipt, next_due_at=spec.next_due_at.astimezone(timezone(timedelta(hours=8))))
    assert verdict(snapshot, spec, same_instant).status == "VALID"


def test_old_period_and_unapproved_time_window_fail_closed():
    snapshot, spec, receipt = fixture()
    old = replace(receipt, completed_at=spec.period_start_at - timedelta(seconds=1))
    assert verdict(snapshot, spec, old).error_code == "INVENTORY_RECEIPT_EXPIRED"
    assert verdict(snapshot, spec, receipt,
                   now=spec.period_start_at - timedelta(seconds=1)).error_code == "INVENTORY_RECEIPT_EXPIRED"
    fresh = replace(receipt, completed_at=spec.next_due_at)
    assert verdict(snapshot, spec, fresh,
                   now=spec.next_due_at + spec.grace).status == "VALID"
    assert verdict(snapshot, spec, fresh,
                   now=spec.next_due_at + spec.grace + timedelta(seconds=1)).error_code == "INVENTORY_RECEIPT_EXPIRED"


def test_invalid_approved_period_fields_fail_closed_even_if_repinned():
    snapshot, spec, receipt = fixture()
    for altered in (
        replace(spec, period_start_at=NOW.replace(tzinfo=None)),
        replace(spec, next_due_at=NOW.replace(tzinfo=None)),
        replace(spec, next_due_at=spec.period_start_at),
        replace(spec, grace=-timedelta(seconds=1)),
    ):
        repinned = replace(altered, approved_spec_sha256=approved_spec_digest(altered))
        assert verdict(snapshot, repinned, receipt).error_code == "INVENTORY_SPEC_UNVERIFIED"
