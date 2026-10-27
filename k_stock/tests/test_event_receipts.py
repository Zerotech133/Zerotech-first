# -*- coding: utf-8 -*-
"""W-Q1 事件回执账本测试（WO-QT-20261020-007，tmp 隔离）。

覆盖工单三部分验收：
  ① 并行响应语义：同一信号，操盘收悉先于投研收悉乱序入账 + 四线程并行
     入账 → 时间线完整、无顺序约束错误；
  ② 收悉≠办结：两类 ack 齐但无 conclusion/action → completeness 报
     2/4 缺投研结论+操盘动作；
  ③ RESEARCH_REVIEW 场景对照：投递 ack 成功但无结论 → 不得推断投研开
     过会（ack 无载荷 + completeness 缺 research_conclusion）；
  ④ 幂等重放拒绝、closed on failure、append-only（改写→检测拒绝）；
  ⑤ signal_id 契约对齐 -005 signal_consumer（源级正则断言 + 探针）。
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import event_receipts as er  # noqa: E402
from event_receipts import (
    DuplicateReceiptError,
    ReceiptError,  # noqa: E402
    ReceiptLedgerTampered,
    record_receipt,
    timeline,
    completeness,
    verify_integrity,
    RESEARCH_ACK,
    TRADER_ACK,
    RESEARCH_CONCLUSION,
    TRADER_ACTION,
)

SIG = "sig-20261009-0001"  # 对齐 -005 测试样例的 signal_id 格式


def _log(tmp_path) -> Path:
    return tmp_path / "receipts.jsonl"


def _ack(kind, actor, ts, **over):
    kw = dict(log_path=None, signal_id=SIG, receipt_kind=kind, actor=actor, ts=ts)
    kw.update(over)
    return kw


def _lines(path: Path) -> list[dict]:
    return [
        json.loads(x)
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


# ---------- ① 并行响应语义：乱序 + 并行入账，无顺序依赖 ----------


def test_out_of_order_receipts_full_timeline(tmp_path):
    """操盘收悉先于投研收悉入账（乱序）→ 时间线完整、无顺序约束错误。"""
    p = _log(tmp_path)
    # 定纲场景：操盘侧先响应，投研侧后收悉——不许有顺序约束
    record_receipt(p, SIG, TRADER_ACK, "trader_room", ts="2026-10-09T09:30:01")
    record_receipt(p, SIG, RESEARCH_ACK, "research_team", ts="2026-10-09T09:30:05")
    record_receipt(
        p,
        SIG,
        TRADER_ACTION,
        "trader_room",
        payload={"action": "BUY", "shares": 100, "combo_id": "combo_jijin"},
        combo_id="combo_jijin",
        ts="2026-10-09T09:31:00",
    )
    record_receipt(
        p,
        SIG,
        RESEARCH_CONCLUSION,
        "research_team",
        payload={"verdict": "hold_plan", "notes": "维持既有计划"},
        plan_version="pv-001",
        ts="2026-10-09T09:35:00",
    )
    tl = timeline(p, SIG)
    assert [e["receipt_kind"] for e in tl] == [
        TRADER_ACK,
        RESEARCH_ACK,
        TRADER_ACTION,
        RESEARCH_CONCLUSION,
    ]
    assert len(tl) == 4
    c = completeness(p, SIG)
    assert c["received_count"] == 4 and c["missing"] == []


def test_parallel_threaded_receipts(tmp_path):
    """四线程同时各入一类回执（真并行）→ 四类齐、互不阻塞。"""
    p = _log(tmp_path)
    jobs = [
        (RESEARCH_ACK, "research_team", None),
        (TRADER_ACK, "trader_room", None),
        (RESEARCH_CONCLUSION, "research_team", {"verdict": "adjust"}),
        (TRADER_ACTION, "trader_room", {"action": "SELL"}),
    ]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [
            ex.submit(
                record_receipt, p, SIG, kind, actor, *([payload] if payload else [])
            )
            for kind, actor, payload in jobs
        ]
        for f in futs:
            f.result(timeout=10)
    assert completeness(p, SIG)["received_count"] == 4
    assert {e["receipt_kind"] for e in _lines(p)} == set(er.RECEIPT_KINDS)


def test_independent_signals_do_not_interfere(tmp_path):
    """多信号并行：A 信号只有 ack 不影响 B 信号 4/4 齐全。"""
    p = _log(tmp_path)
    record_receipt(p, "sig-A", RESEARCH_ACK, "research_team", ts="2026-10-09T09:30:00")
    for kind, actor in [
        (RESEARCH_ACK, "research_team"),
        (TRADER_ACK, "trader_room"),
        (RESEARCH_CONCLUSION, "research_team"),
        (TRADER_ACTION, "trader_room"),
    ]:
        kw = dict(ts="2026-10-09T09:30:00")
        if kind in er.SUBSTANTIVE_KINDS:
            kw["payload"] = {"detail": "x"}
        record_receipt(p, "sig-B", kind, actor, **kw)
    assert completeness(p, "sig-A")["received_count"] == 1
    assert completeness(p, "sig-B")["received_count"] == 4


# ---------- ② 收悉≠办结：completeness 报缺口，不冒充办结 ----------


def test_acks_only_completeness_two_of_four(tmp_path):
    """只有两类 ack、无 conclusion/action → 报 2/4，缺投研结论+操盘动作。"""
    p = _log(tmp_path)
    record_receipt(p, SIG, RESEARCH_ACK, "research_team", ts="2026-10-09T09:30:00")
    record_receipt(p, SIG, TRADER_ACK, "trader_room", ts="2026-10-09T09:30:02")
    c = completeness(p, SIG)
    assert c["received"] == [RESEARCH_ACK, TRADER_ACK]
    assert c["missing"] == [RESEARCH_CONCLUSION, TRADER_ACTION]
    assert c["received_count"] == 2 and c["total"] == 4


# ---------- ③ RESEARCH_REVIEW 对照：投递成功≠开过会 ----------


def test_research_review_delivery_is_not_a_meeting(tmp_path):
    """对照现有 RESEARCH_REVIEW 误读路径：投递 ack 成功但无 conclusion
    → 不得推断投研开过会或改过计划。"""
    p = _log(tmp_path)
    entry = record_receipt(
        p, SIG, RESEARCH_ACK, "research_team", ts="2026-10-09T09:30:00"
    )
    # ack 条目零结论载荷：收悉条目本身不携带任何可被误读为「开会产出」的内容
    assert entry["payload"] is None
    assert entry["plan_version"] is None
    # 语义断言：completeness 明示缺 research_conclusion——投研尚未给出结论
    c = completeness(p, SIG)
    assert RESEARCH_CONCLUSION in c["missing"]
    assert c["received"] == [RESEARCH_ACK]
    # API 层堵死冒充路径：给 ack 塞结论载荷 → 显式拒绝
    with pytest.raises(ReceiptError) as ei:
        record_receipt(
            p, SIG, RESEARCH_ACK, "research_team", payload={"verdict": "开过会了"}
        )
    assert ei.value.reason_code == "ack_payload_forbidden"


# ---------- ④ 幂等重放 / closed on failure / append-only ----------


def test_idempotent_replay_rejected(tmp_path):
    """同 (signal_id, receipt_kind, actor) 重放 → 拒绝且账本行数不变；
    同类型不同角色（投研 vs 操盘）不算重复。"""
    p = _log(tmp_path)
    record_receipt(p, SIG, TRADER_ACK, "trader_room", ts="2026-10-09T09:30:00")
    with pytest.raises(DuplicateReceiptError):
        record_receipt(p, SIG, TRADER_ACK, "trader_room", ts="2026-10-09T09:31:00")
    assert len(_lines(p)) == 1  # 重放不写行
    record_receipt(p, SIG, TRADER_ACK, "trader_room_b", ts="2026-10-09T09:31:00")
    assert len(_lines(p)) == 2  # 不同角色独立入账（多团队口径）


def test_closed_on_failure_no_line_written(tmp_path):
    """字段缺失/未知回执类型/signal_id 非法/实质回执无载荷 → 显式抛错且零写入。"""
    p = _log(tmp_path)
    cases = [
        (
            lambda: record_receipt(p, "", RESEARCH_ACK, "research_team"),
            "signal_id_invalid",
        ),
        (
            lambda: record_receipt(p, "sig bad/slash", RESEARCH_ACK, "r"),
            "signal_id_invalid",
        ),
        (
            lambda: record_receipt(p, SIG, "meeting_done", "research_team"),
            "receipt_kind_invalid",
        ),
        (lambda: record_receipt(p, SIG, RESEARCH_ACK, ""), "actor_invalid"),
        (
            lambda: record_receipt(p, SIG, TRADER_ACTION, "trader_room"),
            "payload_required",
        ),
        (
            lambda: record_receipt(p, SIG, RESEARCH_CONCLUSION, "r", payload={}),
            "payload_required",
        ),
        (
            lambda: record_receipt(p, SIG, RESEARCH_ACK, "r", ts="不是时间"),
            "ts_invalid",
        ),
        (
            lambda: record_receipt(p, SIG, TRADER_ACK, "r", combo_id=""),
            "combo_id_invalid",
        ),
    ]
    for fn, code in cases:
        with pytest.raises(ReceiptError) as ei:
            fn()
        assert ei.value.reason_code == code
    assert not p.exists()  # 全部失败路径零写入


def test_append_only_tamper_rejected(tmp_path):
    """append-only：模块只提供 append API；行内容被改写 → verify_integrity
    检测拒绝（ReceiptLedgerTampered）。"""
    p = _log(tmp_path)
    record_receipt(p, SIG, RESEARCH_ACK, "research_team", ts="2026-10-09T09:30:00")
    record_receipt(
        p,
        SIG,
        RESEARCH_CONCLUSION,
        "research_team",
        payload={"verdict": "hold"},
        ts="2026-10-09T09:35:00",
    )
    assert verify_integrity(p) == 2
    raw = p.read_text(encoding="utf-8")
    # 模拟账本改写：把结论条目的载荷改成另一份「会后版本」
    tampered = raw.replace("hold", "revised_after_meeting")
    assert tampered != raw
    p.write_text(tampered, encoding="utf-8")
    with pytest.raises(ReceiptLedgerTampered):
        verify_integrity(p)
    with pytest.raises(ReceiptLedgerTampered):
        timeline(p, SIG)  # 读路径同样拒绝被改写的账
    with pytest.raises(ReceiptLedgerTampered):
        record_receipt(p, SIG, TRADER_ACK, "trader_room")  # 账本被改写后拒绝继续入账


# ---------- ⑤ signal_id 契约对齐 -005 signal_consumer ----------


def test_signal_id_contract_aligned_with_wo005():
    """源级断言：本模块 SIGNAL_ID_PATTERN 与 -005 signal_consumer 内联
    正则逐字符一致（只读对照，不 import 其依赖链上 WO-006 在途模块）。"""
    src = (HERE / "paper" / "signal_consumer.py").read_text(encoding="utf-8")
    m = re.search(r're\.fullmatch\(r"([^"]+)"', src)
    assert m, "signal_consumer.py 中未找到 signal_id 正则（契约漂移？）"
    assert er.SIGNAL_ID_PATTERN == m.group(1)
    # 探针：合法/非法样本判定一致
    assert re.fullmatch(er.SIGNAL_ID_PATTERN, SIG)
    assert re.fullmatch(er.SIGNAL_ID_PATTERN, "sig-2026-10-09_0001:W1")
    assert not re.fullmatch(er.SIGNAL_ID_PATTERN, "sig/../etc")
    assert not re.fullmatch(er.SIGNAL_ID_PATTERN, "")


def test_timeline_empty_and_unknown_signal(tmp_path):
    """未知信号：时间线空、completeness 报 0/4 全缺（只读不阻塞）。"""
    p = _log(tmp_path)
    record_receipt(p, SIG, RESEARCH_ACK, "research_team", ts="2026-10-09T09:30:00")
    assert timeline(p, "sig-none") == []
    c = completeness(p, "sig-none")
    assert c["received_count"] == 0 and len(c["missing"]) == 4
