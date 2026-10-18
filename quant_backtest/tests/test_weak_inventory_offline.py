# -*- coding: utf-8 -*-
"""Read-only synthetic inventory scanning and race rejection."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weak_inventory_batch import ApprovedInventorySpec, RunnerReceipt, SCHEMA, SCOPE, approved_spec_digest
from weak_inventory_offline import (
    InventoryScanError, scan_inventory, verify_offline_inventory,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def write_member(root: Path, name: str, code: str = "000001", rows: int = 1) -> Path:
    path = root / name
    path.write_bytes((f'{{"code":"{code}","ts":"2026-10-18T12:00:00+08:00"}}\n' * rows).encode("utf-8"))
    return path


def signed_evidence(snapshot):
    spec = ApprovedInventorySpec(SCHEMA, snapshot.root_ref, "f" * 64,
                                 SCOPE, "weak_case_inventory", "scan",
                                 ("--inventory-only",), "daily", timedelta(days=1),
                                 NOW - timedelta(hours=1), NOW + timedelta(hours=2),
                                 timedelta(minutes=5))
    spec = replace(spec, approved_spec_sha256=approved_spec_digest(spec))
    receipt = RunnerReceipt(SCHEMA, spec.root_ref, "run-1", spec.spec_hash, SCOPE,
                            spec.task, spec.action, spec.args, spec.period,
                            snapshot.active_count, len(snapshot.all_members), snapshot.snapshot_sha256,
                            "runner", NOW, NOW + timedelta(hours=2))
    return spec, receipt


def test_active_and_closed_bytes_both_bound_to_inventory(tmp_path):
    write_member(tmp_path, "weak_track_000001.jsonl")
    closed = write_member(tmp_path, "weak_track_000001.closed_20261017.jsonl")
    snapshot = scan_inventory(str(tmp_path), "reports:test")
    assert len(snapshot.all_members) == 2 and snapshot.active_count == 1
    spec, receipt = signed_evidence(snapshot)
    assert verify_offline_inventory(str(tmp_path), "reports:test", spec, receipt,
                                    NOW, ("runner",), lambda _receipt: True,
                                    lambda _ref: spec).status == "VALID"
    closed.write_bytes(closed.read_bytes().replace(b"2026-10-18", b"2026-10-17"))
    assert verify_offline_inventory(str(tmp_path), "reports:test", spec, receipt,
                                    NOW, ("runner",), lambda _receipt: True,
                                    lambda _ref: spec).error_code == "INVENTORY_RECEIPT_MISMATCH"


def test_zero_files_two_pass_and_trusted_receipt(tmp_path):
    snapshot = scan_inventory(str(tmp_path), "reports:test")
    assert snapshot.active_count == 0 and snapshot.all_members == ()
    spec, receipt = signed_evidence(snapshot)
    assert verify_offline_inventory(str(tmp_path), "reports:test", spec, receipt,
                                    NOW, ("runner",), lambda _receipt: True,
                                    lambda _ref: spec).status == "VALID_EMPTY"
    assert verify_offline_inventory(str(tmp_path), "reports:test", spec, receipt,
                                    NOW, ("runner",), lambda _receipt: False,
                                    lambda _ref: spec).status == "INVALID"
    assert verify_offline_inventory(str(tmp_path), "reports:test", spec, receipt,
                                    NOW, ("runner",), lambda _receipt: True,
                                    lambda _ref: spec,
                                    lambda: write_member(tmp_path, "weak_track_000001.jsonl")).error_code == "INVENTORY_RACE_DETECTED"


@pytest.mark.parametrize("payload", [
    b"", b"\n", b'{"code":"000001"}\n\n', b'{"code":"000002"}\n',
    b'{"code":"000001","code":"000001"}\n', b'{"code":"000001","x":NaN}\n',
    b'\xff\n', b'[1,2]\n', b'{"code":"000001"}',
    b'{"code":"000001","x":Infinity}\n',
    b'{"code":"000001"}\n{"code":"000002"}\n',
    b'{"code":"000001"}\n',
    b'{"code":"000001","ts":"2026-10-18"}\n',
    b'{"code":"000001","ts":"2026-10-18T12:00:00"}\n',
    b'{"code":"000001","ts":"2026-02-30T12:00:00Z"}\n',
    b'{"code":"000001","ts":null}\n',
])
def test_every_physical_line_must_be_valid(payload, tmp_path):
    (tmp_path / "weak_track_000001.jsonl").write_bytes(payload)
    with pytest.raises(InventoryScanError, match="INVENTORY_ROW_INVALID"):
        scan_inventory(str(tmp_path), "reports:test")


def test_closed_stream_is_validated_even_when_active_count_is_zero(tmp_path):
    path = tmp_path / "weak_track_000001.closed_20261017.jsonl"
    path.write_bytes(b'{"code":"000002"}\n')
    with pytest.raises(InventoryScanError, match="INVENTORY_ROW_INVALID"):
        scan_inventory(str(tmp_path), "reports:test")


def test_malformed_weak_member_name_is_not_silently_omitted(tmp_path):
    (tmp_path / "weak_track_unknown.jsonl").write_bytes(b'{"code":"000001"}\n')
    with pytest.raises(InventoryScanError, match="INVENTORY_PATH_INVALID"):
        scan_inventory(str(tmp_path), "reports:test")


def test_directory_add_rename_and_same_size_overwrite_are_rejected(tmp_path):
    path = write_member(tmp_path, "weak_track_000001.jsonl")
    root = str(tmp_path)
    with pytest.raises(InventoryScanError, match="INVENTORY_RACE_DETECTED"):
        scan_inventory(root, "reports:test",
                       lambda: write_member(tmp_path, "weak_track_000002.jsonl", code="000002"))
    (tmp_path / "weak_track_000002.jsonl").unlink()
    with pytest.raises(InventoryScanError, match="INVENTORY_RACE_DETECTED"):
        scan_inventory(root, "reports:test",
                       lambda: path.rename(tmp_path / "weak_track_000001.closed_20261018.jsonl"))
    moved = tmp_path / "weak_track_000001.closed_20261018.jsonl"
    with pytest.raises(InventoryScanError, match="INVENTORY_RACE_DETECTED"):
        scan_inventory(root, "reports:test",
                       lambda: moved.write_bytes(moved.read_bytes().replace(b"2026-10-18", b"2026-10-17")))


def test_symlink_is_rejected_before_read(tmp_path):
    target = write_member(tmp_path, "payload.txt")
    link = tmp_path / "weak_track_000001.jsonl"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")
    if not os.path.islink(link):
        pytest.skip("platform symlink creation did not create a link")
    with pytest.raises(InventoryScanError, match="INVENTORY_PATH_INVALID"):
        scan_inventory(str(tmp_path), "reports:test")
