# -*- coding: utf-8 -*-
"""Persistent replay conflict, idempotency and failure tests."""
from dataclasses import replace
import concurrent.futures
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_weak_inventory_authority import evidence
from weak_inventory_authority import load_approval_bundle
from weak_inventory_replay import ReplayError, claim_receipt


def initialize_synthetic_ledger(db: str) -> None:
    """Externally provision a synthetic ledger before invoking the runtime gate."""
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE claims (run_id TEXT PRIMARY KEY NOT NULL, "
                           "period_key TEXT UNIQUE NOT NULL, "
                           "logical_slot TEXT UNIQUE NOT NULL, receipt_token TEXT NOT NULL)")
        connection.execute("PRAGMA user_version=2")


def _concurrent_claim(args):
    db, receipt, bundle, seal = args
    try:
        return claim_receipt(db, receipt, bundle, seal)
    except ReplayError as exc:
        return str(exc)


def test_identical_claim_is_read_only_and_second_period_run_conflicts(tmp_path):
    payload, pin, receipt, seal, _ = evidence(tmp_path)
    bundle = load_approval_bundle(payload, pin)
    db = str(tmp_path / "claims.sqlite")
    initialize_synthetic_ledger(db)
    assert claim_receipt(db, receipt, bundle, seal) is True
    before = Path(db).stat().st_size
    assert claim_receipt(db, receipt, bundle, seal) is False
    assert Path(db).stat().st_size == before
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_CONFLICT"):
        claim_receipt(db, replace(receipt, run_id="run-2"), bundle, seal)
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_CONFLICT"):
        claim_receipt(db, replace(receipt, snapshot_sha256="1" * 64), bundle, seal)


def test_distinct_approved_spec_same_root_same_period_conflicts(tmp_path):
    payload_a, pin_a, receipt_a, seal_a, _ = evidence(tmp_path, run_id="run-a")
    payload_b, pin_b, receipt_b, seal_b, _ = evidence(tmp_path, period="weekly", run_id="run-b")
    db = str(tmp_path / "claims.sqlite")
    initialize_synthetic_ledger(db)
    assert claim_receipt(db, receipt_a, load_approval_bundle(payload_a, pin_a), seal_a)
    assert pin_a != pin_b
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_CONFLICT"):
        claim_receipt(db, receipt_b, load_approval_bundle(payload_b, pin_b), seal_b)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM claims").fetchone() == (1,)


def test_global_run_id_rejects_different_root_or_period(tmp_path):
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    data_a, pin_a, receipt_a, seal_a, _ = evidence(root_a)
    data_b, pin_b, receipt_b, seal_b, _ = evidence(root_b, run_id="run-2",
                                                    root_ref="synthetic:other")
    db = str(tmp_path / "claims.sqlite")
    initialize_synthetic_ledger(db)
    assert claim_receipt(db, receipt_a, load_approval_bundle(data_a, pin_a), seal_a)
    assert claim_receipt(db, receipt_b, load_approval_bundle(data_b, pin_b), seal_b)
    # A different approved root/spec may use the same time window, never share a slot.
    assert claim_receipt(db, receipt_b, load_approval_bundle(data_b, pin_b), seal_b) is False
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_CONFLICT"):
        claim_receipt(db, replace(receipt_b, run_id=receipt_a.run_id),
                      load_approval_bundle(data_b, pin_b), seal_b)


def test_missing_or_deleted_ledger_and_old_schema_fail_closed(tmp_path):
    payload, pin, receipt, seal, _ = evidence(tmp_path)
    bundle = load_approval_bundle(payload, pin)
    db = tmp_path / "claims.sqlite"
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_UNAVAILABLE"):
        claim_receipt(str(db), receipt, bundle, seal)
    assert not db.exists()
    initialize_synthetic_ledger(str(db))
    assert claim_receipt(str(db), receipt, bundle, seal)
    # A distinct missing path is rejected without touching an in-use SQLite file.
    missing_after_prior_claim = tmp_path / "lost-ledger.sqlite"
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_UNAVAILABLE"):
        claim_receipt(str(missing_after_prior_claim), receipt, bundle, seal)
    assert not missing_after_prior_claim.exists()
    old_schema = tmp_path / "old-schema.sqlite"
    with sqlite3.connect(old_schema) as connection:
        connection.execute("CREATE TABLE claims (run_id TEXT PRIMARY KEY NOT NULL, "
                           "period_key TEXT UNIQUE NOT NULL, receipt_token TEXT NOT NULL)")
        connection.execute("INSERT INTO claims VALUES ('prior-run', 'prior-period', 'prior-token')")
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_UNAVAILABLE"):
        claim_receipt(str(old_schema), receipt, bundle, seal)
    with sqlite3.connect(old_schema) as connection:
        assert connection.execute("SELECT count(*) FROM claims").fetchone() == (1,)
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)


def test_corrupted_and_locked_database_fail_closed(tmp_path):
    payload, pin, receipt, seal, _ = evidence(tmp_path)
    bundle = load_approval_bundle(payload, pin)
    db = tmp_path / "claims.sqlite"
    db.write_bytes(b"not a database")
    with pytest.raises(ReplayError, match="INVENTORY_REPLAY_UNAVAILABLE"):
        claim_receipt(str(db), receipt, bundle, seal)
    db.unlink()
    initialize_synthetic_ledger(str(db))
    lock = sqlite3.connect(db, isolation_level=None)
    try:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(ReplayError, match="INVENTORY_REPLAY_UNAVAILABLE"):
            claim_receipt(str(db), receipt, bundle, seal)
    finally:
        lock.execute("ROLLBACK")
        lock.close()


def test_concurrent_claim_is_atomic(tmp_path):
    payload, pin, receipt, seal, _ = evidence(tmp_path)
    bundle = load_approval_bundle(payload, pin)
    db = str(tmp_path / "claims.sqlite")
    initialize_synthetic_ledger(db)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_concurrent_claim, [(db, receipt, bundle, seal)] * 2))
    assert results.count(True) == 1 and results.count(False) == 1, results
