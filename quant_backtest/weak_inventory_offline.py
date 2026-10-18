# -*- coding: utf-8 -*-
"""Bounded, read-only two-pass weak-case inventory scan over an injected root."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
import re
import stat
from typing import Callable, Optional, Tuple

if __package__:
    from .weak_inventory_batch import (
        ApprovedInventorySpec, InventoryMember, InventorySnapshot,
        InventoryVerdict, RunnerReceipt, SCOPE, snapshot_digest,
        validate_inventory_batch,
    )
else:
    from weak_inventory_batch import (
        ApprovedInventorySpec, InventoryMember, InventorySnapshot,
        InventoryVerdict, RunnerReceipt, SCOPE, snapshot_digest,
        validate_inventory_batch,
    )

_NAME = re.compile(r"weak_track_(\d{6})(?:\.closed_(\d{8}))?\.jsonl\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z")
_MAX_FILES = 512
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024


class InventoryScanError(ValueError):
    """Stable fail-closed scan error without filesystem detail disclosure."""


def _reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryScanError("INVENTORY_ROW_INVALID")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise InventoryScanError("INVENTORY_ROW_INVALID")


def _code(value: object) -> Optional[str]:
    """Normalize only exact six-digit CN codes with an optional known suffix."""
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if match is None:
        return None
    return match.group(1)


def _validate_rows(payload: bytes, expected_code: str) -> int:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise InventoryScanError("INVENTORY_ROW_INVALID") from exc
    lines = text.splitlines()
    if not lines or not text.endswith("\n"):
        raise InventoryScanError("INVENTORY_ROW_INVALID")
    for line in lines:
        if not line.strip():
            raise InventoryScanError("INVENTORY_ROW_INVALID")
        try:
            row = json.loads(line, object_pairs_hook=_reject_duplicate,
                             parse_constant=_reject_constant)
        except (ValueError, TypeError) as exc:
            raise InventoryScanError("INVENTORY_ROW_INVALID") from exc
        if not isinstance(row, dict) or _code(row.get("code")) != expected_code:
            raise InventoryScanError("INVENTORY_ROW_INVALID")
        timestamp = row.get("ts")
        if not isinstance(timestamp, str) or _TIMESTAMP.fullmatch(timestamp) is None:
            raise InventoryScanError("INVENTORY_ROW_INVALID")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InventoryScanError("INVENTORY_ROW_INVALID") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InventoryScanError("INVENTORY_ROW_INVALID")
    return len(lines)


def _read_member(root: str, name: str) -> InventoryMember:
    parsed = _NAME.fullmatch(name)
    if parsed is None or os.path.basename(name) != name:
        raise InventoryScanError("INVENTORY_PATH_INVALID")
    path = os.path.join(root, name)
    try:
        if os.path.islink(path):
            raise InventoryScanError("INVENTORY_PATH_INVALID")
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE_BYTES:
            raise InventoryScanError("INVENTORY_PATH_INVALID")
        if os.path.commonpath((root, os.path.realpath(path))) != root:
            raise InventoryScanError("INVENTORY_PATH_INVALID")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or
                    (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or opened.st_size > _MAX_FILE_BYTES):
                raise InventoryScanError("INVENTORY_RACE_DETECTED")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                payload = handle.read(_MAX_FILE_BYTES + 1)
            after = os.lstat(path)
            if (len(payload) > _MAX_FILE_BYTES or len(payload) != opened.st_size
                    or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                    or after.st_size != len(payload)
                    or after.st_mtime_ns != opened.st_mtime_ns):
                raise InventoryScanError("INVENTORY_RACE_DETECTED")
        finally:
            os.close(fd)
    except InventoryScanError:
        raise
    except (OSError, ValueError) as exc:
        raise InventoryScanError("INVENTORY_PATH_INVALID") from exc
    rows = _validate_rows(payload, parsed.group(1))
    return InventoryMember(name, hashlib.sha256(payload).hexdigest(),
                           len(payload), rows, parsed.group(2) is not None)


def _scan_once(root: str) -> Tuple[InventoryMember, ...]:
    try:
        with os.scandir(root) as listing:
            names = sorted(item.name for item in listing
                           if item.name.startswith("weak_track_") and item.name.endswith(".jsonl"))
    except OSError as exc:
        raise InventoryScanError("INVENTORY_PATH_INVALID") from exc
    if len(names) > _MAX_FILES:
        raise InventoryScanError("INVENTORY_LIMIT_EXCEEDED")
    if any(_NAME.fullmatch(name) is None for name in names):
        raise InventoryScanError("INVENTORY_PATH_INVALID")
    members = tuple(_read_member(root, name) for name in names)
    if sum(member.byte_size for member in members) > _MAX_TOTAL_BYTES:
        raise InventoryScanError("INVENTORY_LIMIT_EXCEEDED")
    return members


def scan_inventory(root: str, root_ref: str,
                   between_passes: Optional[Callable[[], None]] = None) -> InventorySnapshot:
    """Read all active/closed members twice, rejecting renames and byte races."""
    if not isinstance(root, str) or not isinstance(root_ref, str) or not root_ref.strip():
        raise InventoryScanError("INVENTORY_PATH_INVALID")
    absolute = os.path.abspath(root)
    if os.path.islink(absolute) or not os.path.isdir(absolute):
        raise InventoryScanError("INVENTORY_PATH_INVALID")
    pinned = os.path.realpath(absolute)
    if os.path.normcase(pinned) != os.path.normcase(absolute):
        raise InventoryScanError("INVENTORY_PATH_INVALID")
    first = _scan_once(pinned)
    if between_passes is not None:
        between_passes()
    if os.path.islink(absolute) or os.path.realpath(absolute) != pinned:
        raise InventoryScanError("INVENTORY_RACE_DETECTED")
    second = _scan_once(pinned)
    if first != second:
        raise InventoryScanError("INVENTORY_RACE_DETECTED")
    return InventorySnapshot(SCOPE, root_ref, second,
                             sum(not item.closed for item in second),
                             snapshot_digest(SCOPE, root_ref, second))


def verify_offline_inventory(
    root: str, root_ref: str, spec: ApprovedInventorySpec,
    receipt: RunnerReceipt, now: datetime,
    trusted_runner_ids: Tuple[str, ...],
    trusted_receipt_verifier: Callable[[RunnerReceipt], bool],
    trusted_spec_resolver: Optional[Callable[[str], Optional[ApprovedInventorySpec]]] = None,
    between_passes: Optional[Callable[[], None]] = None,
) -> InventoryVerdict:
    """Evaluate a synthetic, offline inventory only; never issue production auth."""
    if (not isinstance(spec, ApprovedInventorySpec) or spec.root_ref != root_ref
            or spec.scope != SCOPE or spec.action != "scan"
            or spec.args != ("--inventory-only",) or not callable(trusted_spec_resolver)):
        return InventoryVerdict("INVALID", "INVENTORY_SPEC_UNVERIFIED", None)
    try:
        approved = trusted_spec_resolver(root_ref)
    except Exception:
        return InventoryVerdict("INVALID", "INVENTORY_SPEC_UNVERIFIED", None)
    if not isinstance(approved, ApprovedInventorySpec) or approved != spec:
        return InventoryVerdict("INVALID", "INVENTORY_SPEC_UNVERIFIED", None)
    try:
        snapshot = scan_inventory(root, root_ref, between_passes)
    except InventoryScanError as exc:
        return InventoryVerdict("INVALID", str(exc), None)
    return validate_inventory_batch(snapshot, spec, receipt, now,
                                    trusted_runner_ids, trusted_receipt_verifier,
                                    trusted_spec_resolver)
