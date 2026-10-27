# -*- coding: utf-8 -*-
"""B1 证据链库测试：四态全路径 + 非法跃迁拒绝 + 幂等重放 + append-only。

全程 tmp SQLite（tempfile），不触碰任何生产库。
运行：python -m pytest test_evidence_chain.py -q --no-header
"""

import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from evidence_chain import (  # noqa: E402
    ConflictError,
    EvidenceChainStore,
    IllegalTransitionError,
    S_AUTHORIZED,
    S_POOL_IN,
    S_POOL_OUT,
    S_RESULT,
    make_entry_id,
    make_idempotency_key,
    payload_hash,
)

ENTRY = make_entry_id("chan_monitor", "2026-10-19", "002633.SZ")
BASE = {
    "_meta": {
        "symbol": "002633.SZ",
        "trade_date": "2026-10-19",
        "strategy": "chan_monitor",
    },
    "score": 95,
    "weight": 0.5,
    "rationale": "一买+背驰",
}


@pytest.fixture()
def store(tmp_path):
    s = EvidenceChainStore(str(tmp_path / "evidence.db"))
    yield s
    s.close()


# ── 1. 四态全路径 ────────────────────────────────────────────────────────
def test_full_happy_path(store):
    r1 = store.transition(ENTRY, S_POOL_IN, dict(BASE))
    assert r1["outcome"] == "recorded" and r1["state"] == S_POOL_IN
    r2 = store.transition(
        ENTRY, S_AUTHORIZED, {"approval_id": "AP-001", "granted_by": "老田"}
    )
    r3 = store.transition(
        ENTRY, S_POOL_OUT, {"reason": "displacement", "note": "换强票"}
    )
    r4 = store.transition(ENTRY, S_RESULT, {"ret_t5": 0.031, "ret_t10": -0.012})
    assert [r["state"] for r in (r1, r2, r3, r4)] == [
        S_POOL_IN,
        S_AUTHORIZED,
        S_POOL_OUT,
        S_RESULT,
    ]
    st = store.get_state(ENTRY)
    assert st["state"] == S_RESULT and st["symbol"] == "002633.SZ"
    hist = store.get_history(ENTRY)
    assert [e["to_state"] for e in hist] == [
        S_POOL_IN,
        S_AUTHORIZED,
        S_POOL_OUT,
        S_RESULT,
    ]
    assert [e["from_state"] for e in hist] == [
        None,
        S_POOL_IN,
        S_AUTHORIZED,
        S_POOL_OUT,
    ]


def test_entry_id_format(store):
    assert ENTRY == "chan_monitor:2026-10-19:002633.SZ"


# ── 2. 非法跃迁拒绝 ──────────────────────────────────────────────────────
def test_illegal_transitions_rejected(store):
    store.transition(ENTRY, S_POOL_IN, dict(BASE))
    # 同态重试：同内容=重放（另测）；不同内容=粘性冲突，须人工介入
    with pytest.raises(ConflictError):
        store.transition(ENTRY, S_POOL_IN, dict(BASE, score=96))
    # 入池后不能直接出池/结果（无先前账目 → 纯非法跃迁）
    for bad in (S_POOL_OUT, S_RESULT):
        with pytest.raises(IllegalTransitionError):
            store.transition(ENTRY, bad, {"x": 1})
    store.transition(ENTRY, S_AUTHORIZED, {"approval_id": "AP-001"})
    # 授权后不能回入池/直接结果
    for bad in (S_POOL_IN, S_RESULT):
        with pytest.raises(IllegalTransitionError):
            store.transition(ENTRY, bad, {"x": 1})
    store.transition(ENTRY, S_POOL_OUT, {"reason": "displacement"})
    # 出池后不能回退
    for bad in (S_POOL_IN, S_AUTHORIZED):
        with pytest.raises(IllegalTransitionError):
            store.transition(ENTRY, bad, {"x": 1})
    store.transition(ENTRY, S_RESULT, {"ret_t5": 0.0})
    # 结果是终态，任何跃迁都非法
    for bad in (S_POOL_IN, S_AUTHORIZED, S_POOL_OUT):
        with pytest.raises(IllegalTransitionError):
            store.transition(ENTRY, bad, {"x": 1})
    # 非法跃迁不得留下任何痕迹
    assert len(store.get_history(ENTRY)) == 4


def test_unknown_state_and_bad_input(store):
    with pytest.raises(ValueError):
        store.transition(ENTRY, "frozen", {"x": 1})
    with pytest.raises(ValueError):
        store.transition(ENTRY, S_POOL_IN, {})
    with pytest.raises(ValueError):
        store.transition("bad_id", S_POOL_IN, {"x": 1})


# ── 3. 幂等重放与冲突 ────────────────────────────────────────────────────
def test_idempotent_replay(store):
    r1 = store.transition(ENTRY, S_POOL_IN, dict(BASE))
    r2 = store.transition(ENTRY, S_POOL_IN, dict(BASE))  # 同内容重放
    assert r2["outcome"] == "replayed"
    assert r2["event"]["id"] == r1["event"]["id"]  # 返回首次事件
    assert len(store.get_history(ENTRY)) == 1  # 不重复记账


def test_conflict_same_state_different_payload(store):
    store.transition(ENTRY, S_POOL_IN, dict(BASE))
    with pytest.raises(ConflictError):
        store.transition(ENTRY, S_POOL_IN, dict(BASE, score=96))
    assert len(store.get_history(ENTRY)) == 1  # 粘性：不覆盖


def test_idempotency_key_format():
    ph = payload_hash({"a": 1})
    key = make_idempotency_key(ENTRY, S_POOL_IN, ph)
    assert key == f"evidence:{ENTRY}:pool_in:{ph[:16]}"


def test_independent_entries(store):
    e2 = make_entry_id("strong", "2026-10-19", "600519.SH")
    store.transition(ENTRY, S_POOL_IN, dict(BASE))
    store.transition(
        e2,
        S_POOL_IN,
        {
            "_meta": {
                "symbol": "600519.SH",
                "trade_date": "2026-10-19",
                "strategy": "strong",
            },
            "score": 80,
        },
    )
    assert store.get_state(ENTRY)["state"] == S_POOL_IN
    assert store.get_state(e2)["state"] == S_POOL_IN
    rows = store.query_state(trade_date="2026-10-19")
    assert len(rows) == 2


# ── 4. append-only 强制 ──────────────────────────────────────────────────
def test_append_only_triggers(store, tmp_path):
    store.transition(ENTRY, S_POOL_IN, dict(BASE))
    db = sqlite3.connect(str(tmp_path / "evidence.db"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE evidence_events SET payload_hash='tampered'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM evidence_events")
    db.close()
    assert len(store.get_history(ENTRY)) == 1


# ── 5. 重建投影 ──────────────────────────────────────────────────────────
def test_rebuild_state(store):
    store.transition(ENTRY, S_POOL_IN, dict(BASE))
    store.transition(ENTRY, S_AUTHORIZED, {"approval_id": "AP-001"})
    store._con.execute("DELETE FROM evidence_state")  # 模拟投影损坏
    assert store.get_state(ENTRY) is None
    n = store.rebuild_state()
    assert n == 1
    assert store.get_state(ENTRY)["state"] == S_AUTHORIZED
