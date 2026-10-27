# -*- coding: utf-8 -*-
"""Controlled synthetic offline gate; never sends or authorizes production work."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

try:
    from .weak_inventory_authority import (
        AuthorityError,
        OfflineVerifyKey,
        ReceiptSeal,
        load_approval_bundle,
        validate_controlled_root,
        verify_receipt_seal,
    )
    from .weak_inventory_batch import (
        InventoryVerdict,
        RunnerReceipt,
        validate_inventory_batch,
    )
    from .weak_inventory_offline import InventoryScanError, scan_inventory
    from .weak_inventory_replay import ReplayError, claim_receipt
except ImportError:
    from weak_inventory_authority import (
        AuthorityError,
        OfflineVerifyKey,
        ReceiptSeal,
        load_approval_bundle,
        validate_controlled_root,
        verify_receipt_seal,
    )
    from weak_inventory_batch import (
        InventoryVerdict,
        RunnerReceipt,
        validate_inventory_batch,
    )
    from weak_inventory_offline import InventoryScanError, scan_inventory
    from weak_inventory_replay import ReplayError, claim_receipt


def evaluate_offline_inventory(
    approval_bytes: bytes,
    independent_digest_pin: str,
    receipt: RunnerReceipt,
    seal: ReceiptSeal,
    verify_key: Optional[OfflineVerifyKey],
    now: datetime,
    ledger_path: str,
    between_passes: Optional[Callable[[], None]] = None,
    before_final_scan: Optional[Callable[[], None]] = None,
) -> InventoryVerdict:
    """Approve, bind root, scan twice, verify evidence, rescan, then claim.

    An offline synthetic VALID verdict is never production permission. Independent
    approval/key provisioning is external; this function reads only approved root.
    """

    def deny(code: str) -> InventoryVerdict:
        return InventoryVerdict("INVALID", code, None, False)

    try:
        bundle = load_approval_bundle(approval_bytes, independent_digest_pin)
    except AuthorityError:
        return deny("INVENTORY_SPEC_UNVERIFIED")
    try:
        root = validate_controlled_root(bundle.root)
        snapshot = scan_inventory(root, bundle.root.root_ref, between_passes)
        validate_controlled_root(bundle.root)
    except (AuthorityError, InventoryScanError, OSError, ValueError) as exc:
        return deny(
            str(exc)
            if isinstance(exc, InventoryScanError)
            else "INVENTORY_PATH_INVALID"
        )
    verified = verify_receipt_seal(receipt, seal, bundle, verify_key)
    result = validate_inventory_batch(
        snapshot,
        bundle.spec,
        receipt,
        now,
        (bundle.runner_id,),
        lambda candidate: candidate == receipt and verified,
        lambda ref: bundle.spec if ref == bundle.root.root_ref else None,
    )
    if result.status == "INVALID":
        return InventoryVerdict("INVALID", result.error_code, None, False)
    try:
        if before_final_scan is not None:
            before_final_scan()
        validate_controlled_root(bundle.root)
        final = scan_inventory(root, bundle.root.root_ref)
        validate_controlled_root(bundle.root)
    except (AuthorityError, InventoryScanError, OSError, ValueError):
        return deny("INVENTORY_RACE_DETECTED")
    if final != snapshot:
        return deny("INVENTORY_RACE_DETECTED")
    try:
        claim_receipt(ledger_path, receipt, bundle, seal)
    except ReplayError as exc:
        return deny(str(exc))
    return InventoryVerdict(result.status, None, final, False)
