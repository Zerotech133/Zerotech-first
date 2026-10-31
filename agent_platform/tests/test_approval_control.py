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


# ── 2026-10-31 WO-L4-M4-HARDEN-20261031：M4 三方签字最小校验（灰度） ──
ROLE_MAP = {"ou_qa1": "QA1", "ou_guanyu": "GUANYU", "ou_verify1": "VERIFY1"}


def _rows(*signers, task="M4"):
    return [{"ts": "2026-10-31T09:0%d:00" % i, "task": task,
             "action": "approve", "operator": op}
            for i, op in enumerate(signers)]


def test_three_party_incomplete_single_operator():
    rows = _rows("ou_qa1", "ou_qa1")  # 重复点击只算一签
    r = AC.evaluate_three_party_signoff(rows, "M4", role_of=ROLE_MAP)
    assert r["ok"] is False
    assert r["distinct_signers"] == 1
    assert r["reason"].startswith("distinct_signers_lt_3")


def test_three_party_complete_with_roles():
    rows = _rows("ou_qa1", "ou_guanyu", "ou_verify1")
    r = AC.evaluate_three_party_signoff(rows, "M4", role_of=ROLE_MAP)
    assert r["ok"] is True, r
    assert r["missing_roles"] == []
    assert {s["role"] for s in r["signers"]} == {"QA1", "GUANYU", "VERIFY1"}


def test_three_party_role_mapping_missing_is_not_pass():
    """不给角色映射 → roles_unresolved（不自定谁是谁），不算通过。"""
    r = AC.evaluate_three_party_signoff(_rows("ou_qa1", "ou_guanyu", "ou_verify1"), "M4")
    assert r["ok"] is False
    assert r["reason"] == "roles_unresolved"


def test_three_party_missing_role_not_pass():
    role_map = {"ou_a": "QA1", "ou_b": "GUANYU", "ou_c": "GUANYU"}
    r = AC.evaluate_three_party_signoff(_rows("ou_a", "ou_b", "ou_c"), "M4",
                                        role_of=role_map)
    assert r["ok"] is False
    assert r["reason"] == "missing_roles:VERIFY1"


def test_three_party_annotated_pilot_excluded():
    rows = _rows("ou_qa1", "ou_guanyu", "ou_verify1")
    rows.append({"ts": "2026-10-31T09:59:00", "task": "M4", "action": "annotate",
                 "operator": "DSH-Lead", "refs": ["2026-10-31T09:00:00"]})
    r = AC.evaluate_three_party_signoff(rows, "M4", role_of=ROLE_MAP)
    assert r["ok"] is False and r["distinct_signers"] == 2
    assert r["excluded_ts"] == ["2026-10-31T09:00:00"]


def test_handle_action_gray_warns_but_does_not_block(tmp_path):
    ledger = tmp_path / "a.jsonl"
    r = AC.handle_action(_payload("approval:approve", task="M4", op="ou_qa1"),
                         ledger=ledger, signoff_roles=ROLE_MAP)
    assert r["ok"] is True  # 灰度：不阻断存量审批
    assert r["signoff"]["ok"] is False
    assert any("three_party_incomplete" in w for w in r["warnings"])
    for op in ("ou_guanyu", "ou_verify1"):
        last = AC.handle_action(_payload("approval:approve", task="M4", op=op),
                                ledger=ledger, signoff_roles=ROLE_MAP)
    assert last["ok"] is True
    assert last["signoff"]["ok"] is True
    assert "warnings" not in last
