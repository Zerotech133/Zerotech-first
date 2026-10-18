# -*- coding: utf-8 -*-
"""Offline-only independent approval, controlled root and receipt credentials.

Neither a locally calculated digest nor possession of a synthetic key constitutes
production approval. All authority inputs must be supplied by a separate caller.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Optional

try:
    from .weak_inventory_batch import (ApprovedInventorySpec, RunnerReceipt,
                                       approved_spec_digest, canonical_json)
except ImportError:
    from weak_inventory_batch import (ApprovedInventorySpec, RunnerReceipt,
                                      approved_spec_digest, canonical_json)

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_REF = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9:._-]{0,127}\Z")


class AuthorityError(ValueError):
    """A stable refusal of unapproved or unsafe offline evidence."""


@dataclass(frozen=True)
class ControlledRoot:
    """Approved lexical absolute directory and its separately pinned identity."""

    root_ref: str
    path: str
    device: int
    inode: int


@dataclass(frozen=True)
class ApprovalBundle:
    """Decoded independent approval, never proof of its own authorization."""

    spec: ApprovedInventorySpec
    root: ControlledRoot
    key_id: str
    key_purpose: str
    runner_id: str
    bundle_sha256: str


@dataclass(frozen=True)
class ReceiptSeal:
    """Out-of-band HMAC receipt seal for synthetic offline evaluation only."""

    key_id: str
    purpose: str
    signature_hex: str


@dataclass(frozen=True)
class OfflineVerifyKey:
    """Externally supplied offline testing credential, not a production root."""

    key_id: str
    purpose: str
    secret: bytes
    synthetic_only: bool = True


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityError("INVENTORY_SPEC_UNVERIFIED")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise AuthorityError("INVENTORY_SPEC_UNVERIFIED")


def _aware_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise AuthorityError("INVENTORY_SPEC_UNVERIFIED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("naive")
        return parsed
    except ValueError as exc:
        raise AuthorityError("INVENTORY_SPEC_UNVERIFIED") from exc


def load_approval_bundle(payload: bytes, independent_digest_pin: str) -> ApprovalBundle:
    """Load bounded strict JSON only when an independently supplied SHA pin matches."""
    if (not isinstance(payload, bytes) or len(payload) > 16384
            or not isinstance(independent_digest_pin, str)
            or _HEX.fullmatch(independent_digest_pin) is None
            or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), independent_digest_pin)):
        raise AuthorityError("INVENTORY_SPEC_UNVERIFIED")
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs,
                         parse_constant=_reject_constant)
        if not isinstance(raw, dict) or set(raw) != {"spec", "root", "key_id", "key_purpose", "runner_id"}:
            raise ValueError("bundle shape")
        s, r = raw["spec"], raw["root"]
        if not isinstance(s, dict) or not isinstance(r, dict):
            raise ValueError("invalid objects")
        if set(s) != {"schema", "root_ref", "spec_hash", "scope", "task", "action",
                      "args", "period", "max_age_seconds", "period_start_at", "next_due_at",
                      "grace_seconds", "valid_until", "future_tolerance_seconds",
                      "approved_spec_sha256"}:
            raise ValueError("spec shape")
        if set(r) != {"root_ref", "path", "device", "inode"}:
            raise ValueError("root shape")
        durations = (s["max_age_seconds"], s["grace_seconds"], s["future_tolerance_seconds"])
        if any(type(number) not in (int, float) or not 0 <= number <= 86400 * 31
               for number in durations):
            raise ValueError("invalid durations")
        if not isinstance(s["args"], list) or not all(isinstance(arg, str) for arg in s["args"]):
            raise ValueError("invalid args")
        spec = ApprovedInventorySpec(
            s["schema"], s["root_ref"], s["spec_hash"], s["scope"], s["task"],
            s["action"], tuple(s["args"]), s["period"], timedelta(seconds=durations[0]),
            _aware_timestamp(s["period_start_at"]), _aware_timestamp(s["next_due_at"]),
            timedelta(seconds=durations[1]), _aware_timestamp(s["valid_until"]),
            timedelta(seconds=durations[2]), s["approved_spec_sha256"])
        root = ControlledRoot(**r)
        if (not all(isinstance(getattr(spec, name), str) for name in
                    ("schema", "root_ref", "spec_hash", "scope", "task", "action", "period"))
                or _ROOT_REF.fullmatch(spec.root_ref) is None
                or not isinstance(root.root_ref, str) or root.root_ref != spec.root_ref
                or spec.schema != "weak-inventory-v1" or spec.scope != "inventory_only"
                or spec.task != "weak_case_inventory" or spec.action != "scan"
                or spec.args != ("--inventory-only",) or not spec.period
                or not isinstance(spec.spec_hash, str)
                or _HEX.fullmatch(spec.spec_hash) is None
                or not isinstance(spec.approved_spec_sha256, str)
                or not hmac.compare_digest(spec.approved_spec_sha256, approved_spec_digest(spec))
                or any(not isinstance(raw[name], str) or not raw[name]
                       or len(raw[name]) > 128
                       for name in ("key_id", "key_purpose", "runner_id"))
                or raw["key_purpose"] != "weak_inventory_receipt_v1"):
            raise ValueError("unapproved spec")
        validate_controlled_root(root)
        return ApprovalBundle(spec, root, raw["key_id"], raw["key_purpose"],
                              raw["runner_id"], independent_digest_pin)
    except (AuthorityError, UnicodeError, ValueError, TypeError, KeyError, OverflowError,
            AttributeError, OSError) as exc:
        raise AuthorityError("INVENTORY_SPEC_UNVERIFIED") from exc


def validate_controlled_root(root: ControlledRoot) -> str:
    """Reject lexical traversal, every symlink/junction ancestor and identity drift."""
    if (not isinstance(root, ControlledRoot) or not isinstance(root.path, str)
            or not os.path.isabs(root.path) or os.path.normpath(root.path) != root.path
            or any(type(number) is not int or number < 0 for number in (root.device, root.inode))):
        raise AuthorityError("INVENTORY_PATH_INVALID")
    try:
        path = Path(root.path)
        for ancestor in reversed((path, *path.parents)):
            info = os.lstat(ancestor)
            is_reparse = bool(getattr(info, "st_file_attributes", 0)
                              & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or is_reparse or info.st_nlink == 0):
                raise AuthorityError("INVENTORY_PATH_INVALID")
        info = os.lstat(root.path)
        if ((info.st_dev, info.st_ino) != (root.device, root.inode)
                or os.path.normcase(os.path.realpath(root.path)) != os.path.normcase(root.path)):
            raise AuthorityError("INVENTORY_PATH_INVALID")
        return root.path
    except (OSError, ValueError) as exc:
        raise AuthorityError("INVENTORY_PATH_INVALID") from exc


def receipt_message(receipt: RunnerReceipt, bundle: ApprovalBundle,
                    seal: ReceiptSeal) -> bytes:
    """Bind every receipt field plus independently pinned spec/root/period/key use."""
    if not isinstance(receipt, RunnerReceipt) or not isinstance(bundle, ApprovalBundle):
        raise AuthorityError("INVENTORY_RECEIPT_UNVERIFIED")
    try:
        values = {field.name: getattr(receipt, field.name) for field in fields(RunnerReceipt)}
        for key in ("completed_at", "next_due_at"):
            if not isinstance(values[key], datetime) or values[key].utcoffset() is None:
                raise AuthorityError("INVENTORY_RECEIPT_UNVERIFIED")
            values[key] = values[key].isoformat()
        values["args"] = list(values["args"])
        return canonical_json({"domain": "offline-weak-inventory-v1", "receipt": values,
                               "approval_bundle_sha256": bundle.bundle_sha256,
                               "approved_spec_sha256": bundle.spec.approved_spec_sha256,
                               "root": {"root_ref": bundle.root.root_ref,
                                        "path": bundle.root.path, "device": bundle.root.device,
                                        "inode": bundle.root.inode},
                               "period_start_at": bundle.spec.period_start_at.isoformat(),
                               "key_id": seal.key_id, "key_purpose": seal.purpose})
    except (TypeError, ValueError, AttributeError) as exc:
        raise AuthorityError("INVENTORY_RECEIPT_UNVERIFIED") from exc


def verify_receipt_seal(receipt: RunnerReceipt, seal: ReceiptSeal,
                        bundle: ApprovalBundle, key: Optional[OfflineVerifyKey]) -> bool:
    """Verify injected synthetic HMAC; arbitrary callbacks/self-signatures never qualify."""
    if (not isinstance(receipt, RunnerReceipt) or not isinstance(bundle, ApprovalBundle)
            or not isinstance(seal, ReceiptSeal) or not isinstance(key, OfflineVerifyKey)
            or key.synthetic_only is not True or not isinstance(key.secret, bytes)
            or len(key.secret) < 32 or not isinstance(seal.signature_hex, str)
            or _HEX.fullmatch(seal.signature_hex) is None
            or (seal.key_id, seal.purpose) != (bundle.key_id, bundle.key_purpose)
            or (key.key_id, key.purpose) != (seal.key_id, seal.purpose)
            or receipt.runner_id != bundle.runner_id):
        return False
    try:
        expected = hmac.new(key.secret, receipt_message(receipt, bundle, seal),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(seal.signature_hex, expected)
    except (AuthorityError, TypeError, ValueError):
        return False
