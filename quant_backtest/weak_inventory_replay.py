# -*- coding: utf-8 -*-
"""Cross-process atomic offline claim ledger; corruption always rejects."""
from __future__ import annotations

from datetime import timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

try:
    from .weak_inventory_authority import ApprovalBundle, ReceiptSeal, receipt_message
    from .weak_inventory_batch import RunnerReceipt
except ImportError:
    from weak_inventory_authority import ApprovalBundle, ReceiptSeal, receipt_message
    from weak_inventory_batch import RunnerReceipt


class ReplayError(ValueError):
    """Claim refused because of conflict or unavailable persistent ledger."""


def claim_receipt(db_path: str, receipt: RunnerReceipt,
                  bundle: ApprovalBundle, seal: ReceiptSeal) -> bool:
    """Atomically claim a run, approved period, and controlled logical slot.

    Returns True for a newly persisted claim and False for an exact duplicate.
    Caller must first validate approval, root, scan, and receipt independently.
    """
    if not isinstance(db_path, str) or not os.path.isabs(db_path) or db_path == ":memory:":
        raise ReplayError("INVENTORY_REPLAY_UNAVAILABLE")
    if os.path.islink(db_path) or not os.path.isdir(os.path.dirname(db_path)):
        raise ReplayError("INVENTORY_REPLAY_UNAVAILABLE")
    try:
        token = hashlib.sha256(receipt_message(receipt, bundle, seal)
                               + bytes.fromhex(seal.signature_hex)).hexdigest()
        period_start = bundle.spec.period_start_at.astimezone(timezone.utc).isoformat()
        period_key = json.dumps((bundle.root.root_ref,
                                 bundle.spec.approved_spec_sha256, period_start),
                                separators=(",", ":"))
        logical_slot = json.dumps((bundle.root.root_ref, bundle.spec.scope,
                                   bundle.spec.task, period_start),
                                  separators=(",", ":"))
        if not Path(db_path).is_file():
            raise ReplayError("INVENTORY_REPLAY_UNAVAILABLE")
        connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=rw",
                                     uri=True, timeout=2.0, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=2000")
            connection.execute("BEGIN IMMEDIATE")
            schema = connection.execute("PRAGMA table_info(claims)").fetchall()
            expected = [("run_id", "TEXT", 1, 1), ("period_key", "TEXT", 1, 0),
                        ("logical_slot", "TEXT", 1, 0),
                        ("receipt_token", "TEXT", 1, 0)]
            indexes = connection.execute("PRAGMA index_list(claims)").fetchall()
            unique_columns = {
                tuple(field[2] for field in connection.execute(
                    f"PRAGMA index_info('{index[1]}')"))
                for index in indexes if index[2] == 1 and index[3] == "u"
            }
            if (connection.execute("PRAGMA user_version").fetchone() != (2,)
                    or [(row[1], row[2], row[3], row[5]) for row in schema] != expected
                    or unique_columns != {("period_key",), ("logical_slot",)}):
                raise ReplayError("INVENTORY_REPLAY_UNAVAILABLE")
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity != ("ok",):
                raise ReplayError("INVENTORY_REPLAY_UNAVAILABLE")
            by_run = connection.execute(
                "SELECT period_key, logical_slot, receipt_token FROM claims "
                "WHERE run_id=?", (receipt.run_id,)).fetchone()
            by_period = connection.execute(
                "SELECT run_id, logical_slot, receipt_token FROM claims "
                "WHERE period_key=?", (period_key,)).fetchone()
            by_slot = connection.execute(
                "SELECT run_id, period_key, receipt_token FROM claims "
                "WHERE logical_slot=?", (logical_slot,)).fetchone()
            if by_run is not None or by_period is not None or by_slot is not None:
                if (by_run != (period_key, logical_slot, token)
                        or by_period != (receipt.run_id, logical_slot, token)
                        or by_slot != (receipt.run_id, period_key, token)):
                    raise ReplayError("INVENTORY_REPLAY_CONFLICT")
                connection.execute("COMMIT")
                return False
            connection.execute(
                "INSERT INTO claims(run_id, period_key, logical_slot, receipt_token) "
                "VALUES (?, ?, ?, ?)", (receipt.run_id, period_key, logical_slot, token))
            connection.execute("COMMIT")
            return True
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
    except ReplayError:
        raise
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise ReplayError("INVENTORY_REPLAY_UNAVAILABLE") from exc
