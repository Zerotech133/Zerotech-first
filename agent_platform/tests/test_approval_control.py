# -*- coding: utf-8 -*-
"""approval_control 测试：批准/驳回/幂等/未授权/自审阻断 + M4 多方签字。

2026-10-30 WO-SUPERVISOR：基础审批卡回调契约（不回归）。
2026-10-31 WO-L4-M4-HARDEN：三方签字灰度机制（兼容入口不回归）。
2026-10-31 WO-D1-B123-20261031（D1 证据链 P0 / B2）：M4 多方签字状态机——
``required_signers`` 默认四方 QA1/GUANYU/VERIFY1/ARCH1；单签恒 incomplete、
绝不返回 complete、台账不写「通过」；四签才 complete；幂等/越权/自审防护
不回归（重复只算一签、未授权不落账、owner 自签被拒且不计签）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import approval_control as AC  # noqa: E402

ROLE_MAP = {"ou_qa1": "QA1", "ou_guanyu": "GUANYU", "ou_verify1": "VERIFY1",
            "ou_arch1": "ARCH1"}


def _payload(tag, task="T1", op="ou_ok"):
    return {"operator": {"open_id": op},
            "action": {"value": {"tag": tag, "task": task, "level": "S2"}}}


def _sign(ledger, op, task="M4", **kwargs):
    """M4 单次 approve 点击（默认注入四方角色映射）。"""
    return AC.handle_action(_payload("approval:approve", task=task, op=op),
                            ledger=ledger, signoff_roles=ROLE_MAP, **kwargs)


# ----------------------------------------------------------------------
# 基础契约（2026-10-30 WO-SUPERVISOR，不回归）
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# 旧三方灰度契约（2026-10-31 WO-L4-M4-HARDEN，兼容入口不回归）
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# B2：M4 四方多签（2026-10-31 WO-D1-B123-20261031）
# ----------------------------------------------------------------------
def test_default_required_signers_is_four_party():
    assert set(AC.REQUIRED_SIGNERS) == {"QA1", "GUANYU", "VERIFY1", "ARCH1"}


def test_evaluate_signoff_pure_state_machine():
    """纯函数判定：单签 incomplete、四签 complete、缺签名单可读。"""
    one = _rows("ou_qa1")
    r = AC.evaluate_signoff(one, "M4", role_of=ROLE_MAP)
    assert r["state"] == "incomplete" and r["ok"] is False
    assert r["distinct_signers"] == 1
    assert r["signed_roles"] == ["QA1"]
    assert r["missing_signers"] == ["GUANYU", "VERIFY1", "ARCH1"]
    full = _rows("ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1")
    r = AC.evaluate_signoff(full, "M4", role_of=ROLE_MAP)
    assert r["state"] == "complete" and r["ok"] is True
    assert r["signed_roles"] == ["QA1", "GUANYU", "VERIFY1", "ARCH1"]
    assert r["missing_signers"] == [] and r["reason"] == ""


def test_single_sign_incomplete_not_complete_and_no_pass_written(tmp_path):
    """B2 核心判据：单签不返回 complete、不写「通过」。"""
    ledger = tmp_path / "a.jsonl"
    r = _sign(ledger, "ou_qa1")
    assert r["ok"] is True                  # 动作已记录（主流程语义不变）
    assert r["signed"] is True              # 本次「已签」
    assert r["signoff_state"] == "incomplete"
    assert r["approved"] is False
    assert r["signoff"]["state"] == "incomplete"
    assert r["signoff"]["signed_roles"] == ["QA1"]
    assert r["signoff"]["missing_signers"] == ["GUANYU", "VERIFY1", "ARCH1"]
    assert any("m4_signoff_incomplete" in w for w in r["warnings"])
    text = ledger.read_text(encoding="utf-8")
    assert "通过" not in text
    for banned in ("approved", "complete", "verdict", "decision"):
        assert banned not in text


def test_all_four_signers_reach_complete(tmp_path):
    ledger = tmp_path / "a.jsonl"
    for op in ("ou_qa1", "ou_guanyu", "ou_verify1"):
        r = _sign(ledger, op)
        assert r["signoff_state"] == "incomplete"
        assert r["approved"] is False
    last = _sign(ledger, "ou_arch1")
    assert last["signoff_state"] == "complete"
    assert last["approved"] is True
    assert last["signoff"]["signed_roles"] == ["QA1", "GUANYU", "VERIFY1", "ARCH1"]
    assert "warnings" not in last


def test_duplicate_signature_counts_once(tmp_path):
    ledger = tmp_path / "a.jsonl"
    _sign(ledger, "ou_qa1")
    r = _sign(ledger, "ou_qa1")
    assert r["duplicate"] is True
    assert r["signoff"]["distinct_signers"] == 1
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_required_signers_configurable_subset(tmp_path):
    """required_signers 可由调用方覆盖（不锁死四方，也不放宽到任意）。"""
    ledger = tmp_path / "a.jsonl"
    r = _sign(ledger, "ou_qa1", required_signers=["QA1", "ARCH1"])
    assert r["signoff_state"] == "incomplete"
    r2 = _sign(ledger, "ou_arch1", required_signers=["QA1", "ARCH1"])
    assert r2["signoff_state"] == "complete"
    assert r2["approved"] is True


def test_legacy_signoff_required_roles_kwarg_still_supported(tmp_path):
    """旧参数名 signoff_required_roles/signoff_min_signers 兼容（三方口径）。"""
    ledger = tmp_path / "a.jsonl"
    kwargs = dict(signoff_required_roles=AC.THREE_PARTY_ROLES,
                  signoff_min_signers=3)
    for op in ("ou_qa1", "ou_guanyu"):
        r = _sign(ledger, op, **kwargs)
        assert r["signoff_state"] == "incomplete"
    r = _sign(ledger, "ou_verify1", **kwargs)
    assert r["signoff_state"] == "complete"


def test_roles_unresolved_never_complete(tmp_path):
    """不给角色映射 → 就算四人点满，也不能判 complete（不自定谁是谁）。"""
    ledger = tmp_path / "a.jsonl"
    last = None
    for op in ("ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1"):
        last = AC.handle_action(_payload("approval:approve", task="M4", op=op),
                                ledger=ledger)   # 不传 signoff_roles
        assert last["signoff_state"] == "incomplete"
        assert last["approved"] is False
    assert last["signoff"]["distinct_signers"] == 4
    assert last["signoff"]["reason"] == "roles_unresolved"


def test_self_review_signature_rejected_and_not_counted(tmp_path):
    """自审防护不回归：owner 自签拒收、不落账、不计入已签。"""
    ledger = tmp_path / "a.jsonl"
    r = AC.handle_action(_payload("approval:approve", task="M4", op="ou_qa1"),
                         ledger=ledger, owner="ou_qa1",
                         signoff_roles=ROLE_MAP)
    assert r["ok"] is False and r["error"] == "self_approval_blocked"
    assert not ledger.exists()
    for op in ("ou_guanyu", "ou_verify1", "ou_arch1"):
        last = _sign(ledger, op)
    assert last["signoff_state"] == "incomplete"
    assert last["signoff"]["distinct_signers"] == 3
    assert "QA1" in last["signoff"]["missing_signers"]


def test_unauthorized_signer_not_recorded(tmp_path):
    """越权防护不回归：未授权操作人拒收、不落账、不计签。"""
    ledger = tmp_path / "a.jsonl"
    r = AC.handle_action(_payload("approval:approve", task="M4", op="ou_bad"),
                         ledger=ledger, allowed_ops=["ou_qa1"],
                         signoff_roles=ROLE_MAP)
    assert r["ok"] is False and r["error"] == "operator_not_allowed"
    assert not ledger.exists()
    r2 = _sign(ledger, "ou_qa1")
    assert r2["signoff"]["distinct_signers"] == 1


def test_annotated_pilot_clicks_do_not_count_in_m4(tmp_path):
    """试点点击 annotate 后不计签：quorum 回落 incomplete，不写「通过」。"""
    ledger = tmp_path / "a.jsonl"
    _sign(ledger, "ou_qa1")
    ts = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])["ts"]
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-31T10:00:00", "task": "M4",
                            "action": "annotate", "operator": "DSH-Lead",
                            "refs": [ts]}, ensure_ascii=False) + "\n")
    for op in ("ou_guanyu", "ou_verify1", "ou_arch1"):
        last = _sign(ledger, op)
    assert last["signoff_state"] == "incomplete"
    assert last["approved"] is False
    assert "QA1" in last["signoff"]["missing_signers"]


# ----------------------------------------------------------------------
# 2026-11-01 老田拍板 B：owner_acceptance policy loader
# ----------------------------------------------------------------------
def test_load_m4_policy_owner_acceptance(tmp_path):
    cfg = tmp_path / "approval_signers.json"
    cfg.write_text(json.dumps({
        "map": {"ou_owner": "A0"},
        "m4_policy": {"mode": "owner_acceptance", "owner": "A0",
                      "advisory_roles": ["TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU"]},
    }, ensure_ascii=False), encoding="utf-8")
    pol = AC.load_m4_policy(cfg)
    assert pol["mode"] == "owner_acceptance"
    assert pol["required"] == ["A0"]
    assert pol["advisory_roles"] == ["TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU"]

    rows = _rows("ou_owner")
    r = AC.evaluate_signoff(rows, "M4", required_signers=pol["required"],
                            role_of={"ou_owner": "A0"})
    assert r["state"] == "complete" and r["required_signers"] == ["A0"]


def test_load_m4_policy_bad_config_falls_back_four(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{oops", encoding="utf-8")
    pol = AC.load_m4_policy(cfg)
    assert pol["mode"] == "four_sign"
    assert pol["required"] == list(AC.REQUIRED_SIGNERS)
