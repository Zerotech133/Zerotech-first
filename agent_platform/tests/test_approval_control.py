# -*- coding: utf-8 -*-
"""approval_control 测试：批准/驳回/幂等/未授权/自审阻断。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import approval_control as AC  # noqa: E402


def _payload(tag, task="T1", op="ou_ok"):
    return {"operator": {"open_id": op},
            "action": {"value": {"tag": tag, "task": task, "level": "S2"}}}


def test_approve_and_duplicate(tmp_path):
    ledger = tmp_path / "approvals.jsonl"
    r = AC.handle_action(_payload("approval:approve"), ledger=ledger,
                         allowed_ops=["ou_ok"])
    assert r["ok"] and r["action"] == "approve"
    r2 = AC.handle_action(_payload("approval:approve"), ledger=ledger,
                          allowed_ops=["ou_ok"])
    assert r2["ok"] and r2["duplicate"] is True
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_reject_and_unauthorized(tmp_path):
    ledger = tmp_path / "a.jsonl"
    assert AC.handle_action(_payload("approval:reject"), ledger=ledger,
                            allowed_ops=["ou_ok"])["ok"]
    r = AC.handle_action(_payload("approval:approve", op="ou_bad"),
                         ledger=ledger, allowed_ops=["ou_ok"])
    assert not r["ok"] and r["error"] == "operator_not_allowed"


def test_self_approval_blocked(tmp_path):
    r = AC.handle_action(_payload("approval:approve", op="ou_owner"),
                         ledger=tmp_path / "a.jsonl", owner="ou_owner")
    assert not r["ok"] and r["error"] == "self_approval_blocked"


def test_owner_ids_blocked(tmp_path):
    r = AC.handle_action(_payload("approval:approve", op="ou_owner"),
                         ledger=tmp_path / "a.jsonl",
                         owner_ids=["ou_owner", "ou_other"])
    assert not r["ok"] and r["error"] == "self_approval_blocked"
