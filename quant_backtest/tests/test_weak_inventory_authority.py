# -*- coding: utf-8 -*-
"""Independent synthetic approval and signature boundary tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weak_inventory_batch import (ApprovedInventorySpec, RunnerReceipt, SCHEMA,
                                  SCOPE, approved_spec_digest, canonical_json)
from weak_inventory_offline import scan_inventory
from weak_inventory_authority import (AuthorityError, ControlledRoot, OfflineVerifyKey,
                                      ReceiptSeal, load_approval_bundle, receipt_message,
                                      validate_controlled_root, verify_receipt_seal)

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
SECRET = b"synthetic-offline-only-secret-32-byte-minimum"


def evidence(root: Path, *, period: str = "daily", run_id: str = "run-1",
             root_ref: str = "synthetic:root"):
    """Construct independent synthetic authorization and runner evidence."""
    snapshot = scan_inventory(str(root), root_ref)
    spec = ApprovedInventorySpec(
        SCHEMA, root_ref, "f" * 64, SCOPE, "weak_case_inventory", "scan",
        ("--inventory-only",), period, timedelta(days=1),
        NOW - timedelta(hours=1), NOW + timedelta(hours=1),
        timedelta(minutes=5), NOW + timedelta(hours=1, minutes=5))
    spec = replace(spec, approved_spec_sha256=approved_spec_digest(spec))
    root_info = root.stat()
    data = {"spec": {"schema": spec.schema, "root_ref": spec.root_ref,
                     "spec_hash": spec.spec_hash, "scope": spec.scope,
                     "task": spec.task, "action": spec.action, "args": list(spec.args),
                     "period": spec.period, "max_age_seconds": 86400,
                     "period_start_at": spec.period_start_at.isoformat(),
                     "next_due_at": spec.next_due_at.isoformat(), "grace_seconds": 300,
                     "valid_until": spec.valid_until.isoformat(),
                     "future_tolerance_seconds": 0,
                     "approved_spec_sha256": spec.approved_spec_sha256},
            "root": {"root_ref": root_ref, "path": str(root),
                     "device": root_info.st_dev, "inode": root_info.st_ino},
            "key_id": "offline-key-1", "key_purpose": "weak_inventory_receipt_v1",
            "runner_id": "synthetic-runner"}
    payload = canonical_json(data)
    pin = hashlib.sha256(payload).hexdigest()
    bundle = load_approval_bundle(payload, pin)
    receipt = RunnerReceipt(SCHEMA, root_ref, run_id, spec.spec_hash, SCOPE,
                            spec.task, spec.action, spec.args, spec.period,
                            snapshot.active_count, len(snapshot.all_members),
                            snapshot.snapshot_sha256, "synthetic-runner", NOW,
                            spec.next_due_at)
    key = OfflineVerifyKey("offline-key-1", "weak_inventory_receipt_v1", SECRET)
    unsigned = ReceiptSeal(key.key_id, key.purpose, "0" * 64)
    signature = hmac.new(SECRET, receipt_message(receipt, bundle, unsigned),
                         hashlib.sha256).hexdigest()
    return payload, pin, receipt, ReceiptSeal(key.key_id, key.purpose, signature), key


def test_approval_needs_external_pin_and_exact_bound_root(tmp_path):
    payload, pin, receipt, seal, key = evidence(tmp_path)
    assert verify_receipt_seal(receipt, seal, load_approval_bundle(payload, pin), key)
    for changed in ("", "0" * 64, hashlib.sha256(b"other").hexdigest()):
        with pytest.raises(AuthorityError, match="INVENTORY_SPEC_UNVERIFIED"):
            load_approval_bundle(payload, changed)
    data = json.loads(payload)
    data["root"]["inode"] += 1
    altered = canonical_json(data)
    with pytest.raises(AuthorityError, match="INVENTORY_SPEC_UNVERIFIED"):
        load_approval_bundle(altered, hashlib.sha256(altered).hexdigest())
    with pytest.raises(AuthorityError):
        load_approval_bundle(b'{"spec":{},"spec":{}}', hashlib.sha256(b'{"spec":{},"spec":{}}').hexdigest())


def test_receipt_binds_full_fields_key_id_and_purpose(tmp_path):
    payload, pin, receipt, seal, key = evidence(tmp_path)
    bundle = load_approval_bundle(payload, pin)
    for altered in (replace(receipt, completed_at=NOW + timedelta(seconds=1)),
                    replace(receipt, run_id="other"),
                    replace(receipt, snapshot_sha256="0" * 64),
                    replace(receipt, period="weekly")):
        assert not verify_receipt_seal(altered, seal, bundle, key)
    assert not verify_receipt_seal(receipt, replace(seal, key_id="other"), bundle, key)
    assert not verify_receipt_seal(receipt, seal, bundle, None)
    assert not verify_receipt_seal(receipt, seal, bundle, replace(key, synthetic_only=False))
    assert not verify_receipt_seal(receipt, seal, bundle, replace(key, secret=b"runner-controlled-secret-not-trusted-xxxxxxxx"))


def test_parent_symlink_and_naive_timestamp_fail_closed(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "alias"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory links unavailable")
    info = real.stat()
    with pytest.raises(AuthorityError, match="INVENTORY_PATH_INVALID"):
        validate_controlled_root(ControlledRoot("synthetic:root", str(link), info.st_dev, info.st_ino))
    payload, pin, _, _, _ = evidence(real)
    data = json.loads(payload)
    data["spec"]["period_start_at"] = "2026-10-18T11:00:00"
    altered = canonical_json(data)
    with pytest.raises(AuthorityError, match="INVENTORY_SPEC_UNVERIFIED"):
        load_approval_bundle(altered, hashlib.sha256(altered).hexdigest())
