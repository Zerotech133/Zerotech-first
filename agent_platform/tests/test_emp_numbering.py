# -*- coding: utf-8 -*-
"""员工编号契约（engine/emp_numbering.py）的守卫测试。

锁六件事：部门字唯一、日限/部门限、工号永不复用、票号格式、
**票号必须与本次编号对应**（防张冠李戴）、发号须有放行人。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from engine import emp_numbering as E

ORG = pathlib.Path(__file__).resolve().parents[1] / "registry" / "org.json"
D = "20261023"


def tk(dept: str, seq: int, date: str = D, prefix: str = "LEGION") -> str:
    return E.make_ticket(prefix, date, dept, seq)


def test_dept_chars_cover_org_and_are_unique():
    org = json.loads(ORG.read_text(encoding="utf-8"))
    assert set(E.DEPT_CHAR) == {d["dept"] for d in org["departments"]}
    chars = list(E.DEPT_CHAR.values())
    assert len(chars) == len(set(chars))


def test_unknown_dept_rejected():
    with pytest.raises(E.NumberingError) as ei:
        E.dept_char("no_such_dept")
    assert ei.value.code == "UNKNOWN_DEPT"


def test_emp_no_format():
    assert E.validate_emp_no("000001") == "000001"
    for bad in ("1", "00001", "1234567", "abc123", ""):
        with pytest.raises(E.NumberingError):
            E.validate_emp_no(bad)
    with pytest.raises(E.NumberingError) as ei:
        E.validate_emp_no("000000")
    assert ei.value.code == "BAD_EMP_NO"


def test_enroll_no_round_trip():
    got = E.validate_enroll_no("20261023-情-01")
    assert (got["dept"], got["seq"]) == ("intel", 1)
    assert E.validate_enroll_no("20261023-量-99")["dept"] == "invest"
    for bad in ("20261023-情-00", "20261023-X-01", "2026-10-23-情-01"):
        with pytest.raises(E.NumberingError):
            E.validate_enroll_no(bad)


def test_ticket_format_prefix_date_dept_seq():
    """票号 = 前缀-年月日-部门字-序号（老田 2026-10-23 定）。"""
    assert tk("intel", 1) == "LEGION-20261023-情-01"
    assert E.make_ticket("WO", D, "invest", 8) == "WO-20261023-量-08"
    assert E.make_ticket("K", D, "risk", 3) == "K-20261023-风-03"
    p = E.parse_ticket("LEGION-20261023-情-01")
    assert (p["prefix"], p["dept"], p["seq"]) == ("LEGION", "intel", 1)
    with pytest.raises(E.NumberingError) as a:
        E.parse_ticket("LEGION-2026-情-01")
    assert a.value.code == "BAD_TICKET"
    with pytest.raises(E.NumberingError) as b:
        E.parse_ticket("XX-20261023-情-01")
    assert b.value.code == "BAD_TICKET"
    with pytest.raises(E.NumberingError) as c:
        E.make_ticket("ABC", D, "intel", 1)
    assert c.value.code == "BAD_TICKET_PREFIX"


def test_first_numbers_and_peek_does_not_write():
    led = E.empty_ledger()
    p = E.peek(led, "A0", "intel", D)
    assert (p["emp_no"], p["enroll_no"]) == ("000001", "20261023-情-01")
    assert led["next_emp"] == 1 and led["employees"] == {}


def test_assign_requires_signer_and_matching_ticket():
    led = E.empty_ledger()
    with pytest.raises(E.NumberingError) as a:
        E.assign(led, "A0", "intel", ticket="", signed_by="老田", date=D)
    assert a.value.code == "BAD_TICKET"
    with pytest.raises(E.NumberingError) as b:
        E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="", date=D)
    assert b.value.code == "NO_SIGNER"
    # 票号与本次编号不对应（部门不同 / 序号不同）必须拒绝
    with pytest.raises(E.NumberingError) as c:
        E.assign(led, "A0", "intel", ticket=tk("invest", 1), signed_by="老田", date=D)
    assert c.value.code == "TICKET_MISMATCH"
    with pytest.raises(E.NumberingError) as d:
        E.assign(led, "A0", "intel", ticket=tk("intel", 2), signed_by="老田", date=D)
    assert d.value.code == "TICKET_MISMATCH"
    assert led["employees"] == {}, "失败不得留下半条记录"


def test_assign_increments_and_separates_departments():
    led = E.empty_ledger()
    a = E.assign(
        led, "A0", "intel", ticket=tk("intel", 1), signed_by="老田", date=D, batch="B1"
    )
    b = E.assign(led, "D1", "intel", ticket=tk("intel", 2), signed_by="老田", date=D)
    c = E.assign(led, "ST1", "invest", ticket=tk("invest", 1), signed_by="老田", date=D)
    assert (a["emp_no"], b["emp_no"], c["emp_no"]) == ("000001", "000002", "000003")
    assert b["enroll_no"] == "20261023-情-02"
    assert c["enroll_no"] == "20261023-量-01", "换部门当天从 01 重新开始"
    assert a["batch"] == "B1" and a["ticket"] == "LEGION-20261023-情-01"
    assert led["daily"][D]["total"] == 3
    assert led["daily"][D]["depts"] == {"情": 2, "量": 1}


def test_second_day_restarts_department_seq():
    led = E.empty_ledger()
    E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    n = E.assign(
        led,
        "D1",
        "intel",
        ticket=tk("intel", 1, date="20261024"),
        signed_by="x",
        date="20261024",
    )
    assert n["enroll_no"] == "20261024-情-01"
    assert n["emp_no"] == "000002", "工号跨日连续"


def test_duplicate_assignment_rejected():
    led = E.empty_ledger()
    E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    with pytest.raises(E.NumberingError) as ei:
        E.assign(led, "A0", "intel", ticket=tk("intel", 2), signed_by="x", date=D)
    assert ei.value.code == "ALREADY_NUMBERED"


def test_daily_global_cap():
    led = E.empty_ledger()
    led["daily"][D] = {"total": E.DAILY_CAP_GLOBAL, "depts": {"情": 50, "量": 50}}
    with pytest.raises(E.NumberingError) as ei:
        E.peek(led, "NEW1", "dev", D)
    assert ei.value.code == "DAILY_CAP"


def test_per_dept_seq_width_cap():
    led = E.empty_ledger()
    led["daily"][D] = {"total": 99, "depts": {"情": E.SEQ_PER_DEPT_MAX}}
    with pytest.raises(E.NumberingError) as ei:
        E.peek(led, "NEW2", "intel", D)
    assert ei.value.code == "DEPT_DAY_CAP"


def test_emp_exhaustion():
    led = E.empty_ledger()
    led["next_emp"] = E.EMP_MAX + 1
    with pytest.raises(E.NumberingError) as ei:
        E.peek(led, "NEW3", "dev", D)
    assert ei.value.code == "EMP_EXHAUSTED"


def test_retire_does_not_recycle_number():
    led = E.empty_ledger()
    a = E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    E.retire(led, "A0", ticket=tk("intel", 1), signed_by="x", reason="调岗")
    assert (
        "A0" not in led["employees"] and led["retired"]["A0"]["emp_no"] == a["emp_no"]
    )
    nxt = E.assign(led, "NEW4", "intel", ticket=tk("intel", 2), signed_by="x", date=D)
    assert nxt["emp_no"] == "000002", "注销后工号不回收"


def test_retire_requires_ticket_and_signer():
    """注销也是单据：无票号/无放行人一律拒绝（此前只存不留痕）。"""
    led = E.empty_ledger()
    E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    with pytest.raises(E.NumberingError) as a:
        E.retire(led, "A0", ticket="", signed_by="x")
    assert a.value.code == "BAD_TICKET"
    with pytest.raises(E.NumberingError) as b:
        E.retire(led, "A0", ticket=tk("intel", 1), signed_by="")
    assert b.value.code == "NO_SIGNER"
    assert "A0" in led["employees"], "被拒的注销不得改动台账"


def test_rehire_restores_original_number():
    """复职恢复原号：不能给同一个人发第二个工号。"""
    led = E.empty_ledger()
    a = E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    E.retire(led, "A0", ticket=tk("intel", 1), signed_by="x")
    back = E.rehire(led, "A0", ticket=tk("intel", 2), signed_by="x")
    assert back["emp_no"] == a["emp_no"] and back["enroll_no"] == a["enroll_no"]
    assert "A0" in led["employees"] and "A0" not in led["retired"]
    assert led["next_emp"] == 2, "复职不得推进 next_emp"
    with pytest.raises(E.NumberingError) as ei:
        E.rehire(led, "A0", ticket=tk("intel", 3), signed_by="x")
    assert ei.value.code == "ALREADY_ACTIVE"


def test_summary_shape():
    led = E.empty_ledger()
    E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    s = E.summary(led, D)
    assert s["issued_today"] == 1 and s["daily_cap"] == 100
    assert s["next_emp"] == "000002" and s["active"] == 1


def test_module_never_touches_org():
    h = hashlib.sha256(ORG.read_bytes()).hexdigest()
    led = E.empty_ledger()
    E.assign(led, "A0", "intel", ticket=tk("intel", 1), signed_by="x", date=D)
    assert hashlib.sha256(ORG.read_bytes()).hexdigest() == h
