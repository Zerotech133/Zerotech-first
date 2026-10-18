# -*- coding: utf-8 -*-
"""Synthetic gate ordering and final inventory consistency."""
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_weak_inventory_authority import NOW, evidence
from test_weak_inventory_replay import initialize_synthetic_ledger
from weak_inventory_gate import evaluate_offline_inventory


def evaluate(root: Path, *, mutate=None, key_override=False):
    payload, pin, receipt, seal, key = evidence(root)
    if not (root / "claims.sqlite").exists():
        initialize_synthetic_ledger(str(root / "claims.sqlite"))
    key = None if key_override else key
    return evaluate_offline_inventory(payload, pin, receipt, seal, key, NOW,
                                      str(root / "claims.sqlite"),
                                      before_final_scan=mutate)


def test_synthetic_empty_claim_is_valid_empty_but_never_production(tmp_path):
    result = evaluate(tmp_path)
    assert result.status == "VALID_EMPTY" and result.production_permitted is False
    # Ledger files are not in the weak-track namespace; exact receipt remains idempotent.
    assert evaluate(tmp_path).status == "VALID_EMPTY"


def test_no_key_or_independent_pin_cannot_grant_valid(tmp_path):
    assert evaluate(tmp_path, key_override=True).error_code == "INVENTORY_RECEIPT_UNVERIFIED"
    payload, _, receipt, seal, key = evidence(tmp_path)
    result = evaluate_offline_inventory(payload, "0" * 64, receipt, seal, key,
                                        NOW, str(tmp_path / "claims.sqlite"))
    assert result.status == "INVALID" and result.production_permitted is False
    assert (tmp_path / "claims.sqlite").exists()  # Explicitly provisioned test ledger.


def test_gate_rejects_missing_ledger_without_recreating(tmp_path):
    payload, pin, receipt, seal, key = evidence(tmp_path)
    missing = tmp_path / "missing.sqlite"
    result = evaluate_offline_inventory(payload, pin, receipt, seal, key,
                                        NOW, str(missing))
    assert result.error_code == "INVENTORY_REPLAY_UNAVAILABLE"
    assert not missing.exists()


def test_final_rescan_rejects_new_member_without_claim(tmp_path):
    def mutation():
        (tmp_path / "weak_track_000001.jsonl").write_bytes(
            b'{"code":"000001","ts":"2026-10-18T12:00:00+08:00"}\n')

    payload, pin, receipt, seal, key = evidence(tmp_path)
    db = tmp_path / "claims.sqlite"
    initialize_synthetic_ledger(str(db))
    result = evaluate_offline_inventory(payload, pin, receipt, seal, key, NOW,
                                        str(db), before_final_scan=mutation)
    assert result.error_code == "INVENTORY_RACE_DETECTED"
    assert result.production_permitted is False
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM claims").fetchone() == (0,)
    (tmp_path / "weak_track_000001.jsonl").unlink()
    # Re-entry with the original receipt runs every trust check again before claiming.
    assert evaluate_offline_inventory(payload, pin, receipt, seal, None, NOW,
                                      str(db)).error_code == "INVENTORY_RECEIPT_UNVERIFIED"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM claims").fetchone() == (0,)
    restored = evaluate_offline_inventory(payload, pin, receipt, seal, key, NOW,
                                          str(db))
    assert restored.status == "VALID_EMPTY" and restored.production_permitted is False
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM claims").fetchone() == (1,)


def test_naive_legacy_row_not_normalized(tmp_path):
    payload, pin, receipt, seal, key = evidence(tmp_path)
    (tmp_path / "weak_track_000001.jsonl").write_bytes(
        b'{"code":"000001","ts":"2026-10-18T12:00:00"}\n')
    result = evaluate_offline_inventory(payload, pin, receipt, seal, key,
                                        NOW, str(tmp_path / "claims.sqlite"))
    assert result.error_code == "INVENTORY_ROW_INVALID"
    assert result.production_permitted is False
    assert not (tmp_path / "claims.sqlite").exists()
