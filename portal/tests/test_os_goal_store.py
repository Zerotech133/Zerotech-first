# -*- coding: utf-8 -*-
"""G0 账本单测：append-only / 哈希链 / 幂等 / 投影 / 证据包。"""

import json
import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import os_goal_contract as C  # noqa: E402
import os_goal_store as S  # noqa: E402

SHA = "sha256:" + "b" * 64
CITE = {
    "source_surface": "research",
    "source_ref": "RES-001",
    "source_kind": "data",
    "claim": "样本内胜率 55%",
    "confidence": "fact",
}


def make_goal(store):
    return store.create_goal(
        "把零界做成持续创业者的个人事业OS",
        "连续创业者",
        [{"id": "A1", "text": "用户愿付费", "status": "hypothesis"}],
        ["四类成果各验收一次"],
        idem="IDEM-GOAL-1",
    )


def test_write_read_and_reload(tmp_path):
    d = str(tmp_path / "os_goals")
    st = S.OsGoalStore(d)
    g = make_goal(st)
    it = st.create_item(
        g["payload"]["goal_id"],
        "做一页可试用原型",
        "老田今天要试用",
        "software",
        idem="IDEM-ITEM-1",
    )
    st.produce_outcome(
        it["payload"]["item_id"],
        "software",
        "v1",
        "portal/data/os_goals/x/a1.html",
        SHA,
        [],
    )
    assert st.health()["goals"] == 1
    assert st.health()["outcomes"] == 1
    st2 = S.OsGoalStore(d)  # 重放
    assert st2.health()["events"] == 5 or st2.health()["events"] == 3
    assert st2.health()["last_hash"] == st.health()["last_hash"]
    assert st2.get_item(it["payload"]["item_id"])["outcomes"][0]["sha256"] == SHA


def test_duplicate_idempotency_key_rejected(tmp_path):
    st = S.OsGoalStore(str(tmp_path / "d"))
    make_goal(st)
    with pytest.raises(C.DuplicateKey):
        make_goal(st)


def test_item_without_goal_rejected(tmp_path):
    st = S.OsGoalStore(str(tmp_path / "d"))
    with pytest.raises(C.ContractError):
        st.create_item("GOL-20261024-9999", "无目标事项", "now", "software")


def test_outcome_kind_mismatch_rejected(tmp_path):
    st = S.OsGoalStore(str(tmp_path / "d"))
    g = make_goal(st)
    it = st.create_item(g["payload"]["goal_id"], "t", "now", "software")
    with pytest.raises(C.ContractError):
        st.produce_outcome(it["payload"]["item_id"], "research", "v1", "x", SHA, [CITE])


def test_evidence_bundle_has_report_and_events(tmp_path):
    st = S.OsGoalStore(str(tmp_path / "d"))
    g = make_goal(st)
    it = st.create_item(g["payload"]["goal_id"], "t", "now", "content")
    st.produce_outcome(
        it["payload"]["item_id"], "content", "v1", "x", SHA, [dict(CITE)]
    )
    bundle = st.evidence_bundle(it["payload"]["item_id"])
    assert bundle["report"]["verdict"] == "PASS", bundle["report"]["checks"]
    assert bundle["event_count"] >= 3
    assert any(ev["type"] == "outcome.produced" for ev in bundle["events"])


def test_citation_upgrade_across_outcomes_rejected(tmp_path):
    st = S.OsGoalStore(str(tmp_path / "d"))
    g = make_goal(st)
    it = st.create_item(g["payload"]["goal_id"], "t", "now", "business_plan")
    hyp = dict(CITE, confidence="hypothesis")
    st.produce_outcome(
        it["payload"]["item_id"], "business_plan", "v1", "x", SHA, [hyp], idem="OUT-1"
    )
    fact = dict(hyp, confidence="fact")
    with pytest.raises(C.ContractError):
        st.produce_outcome(
            it["payload"]["item_id"],
            "business_plan",
            "v2",
            "x2",
            SHA,
            [fact],
            idem="OUT-2",
        )


def test_tampered_ledger_reload_fails(tmp_path):
    d = str(tmp_path / "d")
    st = S.OsGoalStore(d)
    make_goal(st)
    path = os.path.join(d, S.LEDGER)
    lines = open(path, encoding="utf-8").read().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["statement"] = "被篡改的目标"
    lines[0] = json.dumps(rec, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with pytest.raises(S.LedgerError):
        S.OsGoalStore(d)


def test_ids_are_unique_and_sequential(tmp_path):
    st = S.OsGoalStore(str(tmp_path / "d"))
    a = make_goal(st)
    b = st.create_goal(
        "第二个目标",
        "小团队",
        [{"id": "A1", "text": "h", "status": "hypothesis"}],
        ["信号"],
        idem="IDEM-GOAL-2",
    )
    assert a["payload"]["goal_id"] != b["payload"]["goal_id"]
    assert b["seq"] == 2


# ---- 锁文件 open 的瞬时 Errno 13（2026-11-07 K-ORG-20261107-LOCKOPEN）----
# 背景：Windows 上并发创建/打开同一文件会瞬时 PermissionError（共享模式竞争 /
# 安全软件持有扫描句柄，同仓 inbox_gateway 实测约 16%）。原先 os.open 写在
# 加锁等待逻辑之外，异常直接上升为"账本写入失败"，与加锁侧的 10s 等待不一致。


def _flaky_os_open(monkeypatch, target, fail_times):
    """只让 target 这个路径的 os.open 前 fail_times 次报 Errno 13，其余透传。"""
    real_open = os.open
    calls = {"n": 0}

    def flaky(path, flags, mode=0o777, **kw):
        if str(path) == str(target):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", flaky)
    return calls


def test_file_lock_retries_transient_open_permission_error(tmp_path, monkeypatch):
    """瞬时两次拒绝后仍能拿到锁——不把"等一下"变成"账本写失败"。"""
    lock = tmp_path / "ledger.lock"
    calls = _flaky_os_open(monkeypatch, lock, fail_times=2)
    with S._file_lock(str(lock)):
        assert lock.exists()
    assert calls["n"] >= 3, calls


def test_file_lock_open_gives_up_after_deadline(tmp_path, monkeypatch):
    """持续拒绝必须抛错而不是无限等——把真故障变成 hang 更糟。"""
    lock = tmp_path / "ledger2.lock"
    _flaky_os_open(monkeypatch, lock, fail_times=10 ** 9)
    monkeypatch.setattr(S, "_LOCK_TIMEOUT_S", 0.05)
    with pytest.raises(PermissionError):
        with S._file_lock(str(lock)):
            pass
