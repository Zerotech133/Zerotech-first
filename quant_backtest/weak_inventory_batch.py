# -*- coding: utf-8 -*-
"""Pure data and evidence checks for an offline weak-case inventory batch.

A matching receipt is not proof of its own provenance. Callers must inject an
independent approved spec and an independently trusted receipt verifier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable, Optional, Tuple

SCHEMA = "weak-inventory-v1"
SCOPE = "inventory_only"


@dataclass(frozen=True)
class InventoryMember:
    """One active or closed weak-case file bound to its complete raw bytes."""

    name: str
    sha256: str
    byte_size: int
    row_count: int
    closed: bool


@dataclass(frozen=True)
class InventorySnapshot:
    """Complete inventory, including closed streams for inventory integrity."""

    scope: str
    root_ref: str
    all_members: Tuple[InventoryMember, ...]
    active_count: int
    snapshot_sha256: str


@dataclass(frozen=True)
class ApprovedInventorySpec:
    """Externally approved exact task and timing constraints (not self-signed)."""

    schema: str
    root_ref: str
    spec_hash: str
    scope: str
    task: str
    action: str
    args: Tuple[str, ...]
    period: str
    max_age: timedelta
    period_start_at: datetime
    next_due_at: datetime
    grace: timedelta
    valid_until: datetime
    future_tolerance: timedelta = timedelta(0)
    approved_spec_sha256: str = ""


@dataclass(frozen=True)
class RunnerReceipt:
    """Untrusted receipt claiming a specific completed inventory snapshot."""

    schema: str
    root_ref: str
    run_id: str
    spec_hash: str
    scope: str
    task: str
    action: str
    args: Tuple[str, ...]
    period: str
    active_count: int
    member_count: int
    snapshot_sha256: str
    runner_id: str
    completed_at: datetime
    next_due_at: datetime


@dataclass(frozen=True)
class InventoryVerdict:
    """Offline verdict; a valid synthetic test is not production permission."""

    status: str
    error_code: Optional[str]
    snapshot: Optional[InventorySnapshot]
    production_permitted: bool = False


def canonical_json(value: object) -> bytes:
    """Encode a bounded DTO as deterministic UTF-8 JSON."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def snapshot_digest(
    scope: str, root_ref: str, all_members: Tuple[InventoryMember, ...]
) -> str:
    """Recompute the complete inventory digest without trusting metadata."""
    members = sorted(all_members, key=lambda member: member.name)
    body = {
        "scope": scope,
        "root_ref": root_ref,
        "all_members": [asdict(member) for member in members],
    }
    return hashlib.sha256(canonical_json(body)).hexdigest()


def approved_spec_digest(spec: ApprovedInventorySpec) -> str:
    """Hash the full approved spec content, excluding its independent SHA pin."""
    if not isinstance(spec, ApprovedInventorySpec):
        return ""
    try:
        payload = {
            "schema": spec.schema,
            "root_ref": spec.root_ref,
            "spec_hash": spec.spec_hash,
            "scope": spec.scope,
            "task": spec.task,
            "action": spec.action,
            "args": list(spec.args),
            "period": spec.period,
            "max_age_seconds": spec.max_age.total_seconds(),
            "period_start_at": spec.period_start_at.isoformat(),
            "next_due_at": spec.next_due_at.isoformat(),
            "grace_seconds": spec.grace.total_seconds(),
            "valid_until": spec.valid_until.isoformat(),
            "future_tolerance_seconds": spec.future_tolerance.total_seconds(),
        }
        return hashlib.sha256(canonical_json(payload)).hexdigest()
    except (TypeError, ValueError, AttributeError):
        return ""


def _aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def validate_inventory_batch(
    snapshot: InventorySnapshot,
    spec: ApprovedInventorySpec,
    receipt: RunnerReceipt,
    now: datetime,
    trusted_runner_ids: Tuple[str, ...],
    trusted_receipt_verifier: Callable[[RunnerReceipt], bool],
    trusted_spec_resolver: Optional[
        Callable[[str], Optional[ApprovedInventorySpec]]
    ] = None,
) -> InventoryVerdict:
    """Compare independently approved spec/runner evidence; reject absent roots."""

    def deny(code: str) -> InventoryVerdict:
        return InventoryVerdict(
            "INVALID",
            code,
            snapshot if isinstance(snapshot, InventorySnapshot) else None,
        )

    if not isinstance(snapshot, InventorySnapshot) or not isinstance(
        spec, ApprovedInventorySpec
    ):
        return deny("INVENTORY_SPEC_UNVERIFIED")
    if not callable(trusted_spec_resolver):
        return deny("INVENTORY_SPEC_UNVERIFIED")
    try:
        approved = trusted_spec_resolver(spec.root_ref)
    except Exception:
        return deny("INVENTORY_SPEC_UNVERIFIED")
    if (
        not isinstance(approved, ApprovedInventorySpec)
        or approved != spec
        or not approved.approved_spec_sha256
    ):
        return deny("INVENTORY_SPEC_UNVERIFIED")
    if (
        spec.schema != SCHEMA
        or spec.scope != SCOPE
        or not spec.root_ref
        or not spec.spec_hash
        or spec.task != "weak_case_inventory"
        or spec.action != "scan"
        or spec.args != ("--inventory-only",)
        or not spec.period
        or not spec.approved_spec_sha256
        or spec.approved_spec_sha256 != approved_spec_digest(spec)
    ):
        return deny("INVENTORY_SPEC_UNVERIFIED")
    if (
        not _aware(spec.period_start_at)
        or not _aware(spec.next_due_at)
        or not _aware(spec.valid_until)
        or not isinstance(spec.max_age, timedelta)
        or not isinstance(spec.future_tolerance, timedelta)
        or not isinstance(spec.grace, timedelta)
        or spec.max_age <= timedelta(0)
        or spec.future_tolerance < timedelta(0)
        or spec.grace < timedelta(0)
        or spec.next_due_at <= spec.period_start_at
        or spec.next_due_at + spec.grace > spec.valid_until
    ):
        return deny("INVENTORY_SPEC_UNVERIFIED")
    if snapshot.scope != spec.scope or snapshot.root_ref != spec.root_ref:
        return deny("INVENTORY_SCOPE_MISMATCH")
    members = snapshot.all_members
    if (
        not isinstance(members, tuple)
        or not all(isinstance(m, InventoryMember) for m in members)
        or len({m.name for m in members}) != len(members)
        or type(snapshot.active_count) is not int
        or snapshot.active_count != sum(not member.closed for member in members)
        or any(
            type(member.byte_size) is not int
            or member.byte_size <= 0
            or type(member.row_count) is not int
            or member.row_count <= 0
            for member in members
        )
        or snapshot.snapshot_sha256
        != snapshot_digest(snapshot.scope, snapshot.root_ref, members)
    ):
        return deny("INVENTORY_INTEGRITY_MISMATCH")
    if not isinstance(receipt, RunnerReceipt) or not isinstance(
        trusted_runner_ids, tuple
    ):
        return deny("INVENTORY_RECEIPT_UNVERIFIED")
    if not all(
        _aware(item) for item in (now, receipt.completed_at, receipt.next_due_at)
    ):
        return deny("INVENTORY_TIME_INVALID")
    if (
        not callable(trusted_receipt_verifier)
        or receipt.runner_id not in trusted_runner_ids
    ):
        return deny("INVENTORY_RECEIPT_UNVERIFIED")
    try:
        verified = trusted_receipt_verifier(receipt)
    except Exception:
        return deny("INVENTORY_RECEIPT_UNVERIFIED")
    if verified is not True:
        return deny("INVENTORY_RECEIPT_UNVERIFIED")
    if (
        receipt.schema,
        receipt.root_ref,
        receipt.spec_hash,
        receipt.scope,
        receipt.task,
        receipt.action,
        receipt.args,
        receipt.period,
    ) != (
        spec.schema,
        spec.root_ref,
        spec.spec_hash,
        spec.scope,
        spec.task,
        spec.action,
        spec.args,
        spec.period,
    ):
        return deny("INVENTORY_RECEIPT_MISMATCH")
    if (
        not receipt.run_id
        or type(receipt.active_count) is not int
        or type(receipt.member_count) is not int
        or receipt.active_count != snapshot.active_count
        or receipt.member_count != len(members)
        or receipt.snapshot_sha256 != snapshot.snapshot_sha256
    ):
        return deny("INVENTORY_RECEIPT_MISMATCH")
    observed = now.astimezone(timezone.utc)
    completed = receipt.completed_at.astimezone(timezone.utc)
    period_start = spec.period_start_at.astimezone(timezone.utc)
    next_due = spec.next_due_at.astimezone(timezone.utc)
    valid_until = spec.valid_until.astimezone(timezone.utc)
    if receipt.next_due_at.astimezone(timezone.utc) != next_due:
        return deny("INVENTORY_RECEIPT_MISMATCH")
    if (
        observed < period_start
        or observed > next_due + spec.grace
        or observed > valid_until
        or completed > valid_until
        or completed < period_start
        or completed > next_due
        or completed > observed + spec.future_tolerance
        or observed > completed + spec.max_age
    ):
        return deny("INVENTORY_RECEIPT_EXPIRED")
    return InventoryVerdict("VALID_EMPTY" if not members else "VALID", None, snapshot)
