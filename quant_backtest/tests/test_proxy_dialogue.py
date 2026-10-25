# -*- coding: utf-8 -*-
"""K-QUANT-20261023-OSCHAT 代理对话只读影子·对抗测试（合同 v1 隔离最小子集）。

对照合同 v1 硬约束 H1-H7 与批次 dispatch 十条测试要求：
  H1 代行标识强制   → 正常路径/冒充用例/present() 结构（标识由 gate 附加）
  H2 禁冒充司马懿   → test_impersonating_body_still_marked_as_proxy
  H3 证据引用可取证 → test_unresolvable_claim_* / test_status_source_failures_*
  H4 无审批交易副作用→ test_module_has_no_approval_order_permit_members
  H5 落盘可取证     → test_ledger_roundtrip_*（含损坏/半条显式报错）
  H6 诚实空态       → test_entry_notice_marks_qs1_without_dialogue_capability
  H7 错席位关闭     → test_non_routed_seat_*（不得呈现 + 锁会话禁绕答）
前置状态一律 RuntimeError 守卫，不静默 pass；负面用例（伪造/缺失/撤销类）
全覆盖；时基固定为 T，不依赖墙钟。
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from quant_backtest.agent_bridge.proxy_dialogue import (
    ANSWER_STATES,
    ANSWERED_BY_PROXY,
    NO_STATUS_SOURCE,
    NO_STATUS_TEXT,
    NOT_DELIVERED,
    PRESENTABLE_STATES,
    PROXY_FOR,
    QS1_NO_DIALOGUE_CAPABILITY_NOTICE,
    REFUSED_MISFIT,
    REFUSAL_TEXT,
    SCHEMA,
    SEAT_TIANFENG,
    SEAT_ZHUGELIANG,
    SOURCE_ACTIVE,
    SOURCE_STATES,
    FileLedger,
    LedgerError,
    LedgerPort,
    ProxyAnswer,
    ProxyDialogueGate,
    ProxyInquiry,
    SeatAnswerPort,
    StatusClaim,
    StatusSourcePort,
    TRANSFER_TO_ADVISOR_PENDING_SIGNING,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
POINT_AS_OF = "2026-10-19T01:00:00+00:00"
POINT_REF = "agent_platform/data/quant_sector/status_point.json#QS1"
SEAT_BODY = "田丰代答：QS1 当前持仓 3 只；策略池运行中。"
CLAIM_TEXT = "QS1 当前持仓 3 只"


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def make_inquiry(inquiry_id: str = "inq-1", text: str = "田丰，QS1 现在什么状态？",
                 asked_at: datetime = T,
                 trace_id: Optional[str] = None) -> ProxyInquiry:
    return ProxyInquiry(inquiry_id=inquiry_id, text=text, trace_id=trace_id,
                        asked_at=asked_at)


def claim(text: str = CLAIM_TEXT,
          as_of: Optional[str] = None,
          source_ref: Optional[str] = None) -> StatusClaim:
    return StatusClaim(claim_text=text, as_of=as_of, source_ref=source_ref)


def active_result(as_of: str = POINT_AS_OF,
                  source_ref: str = POINT_REF) -> Dict[str, Any]:
    """状态源假数据：active 态新近状态点（参照 status_point 归结构形）。"""
    return {"state": SOURCE_ACTIVE, "age_sec": 12,
            "point": {"schema": "quant-sector-status-point/v1", "writer": "QS1",
                      "state": SOURCE_ACTIVE, "as_of": as_of,
                      "source_ref": source_ref},
            "reason": "状态点新近（12 秒内）", "path": source_ref}


def no_writer_result() -> Dict[str, Any]:
    """当前真实态：QS1 无运行时写入者（W2 接线前唯一的诚实状态）。"""
    return {"state": "no_writer", "point": None,
            "reason": "QS1 当前无运行时写入者", "path": "(unbound)"}


class FakeSeatPort:
    """席位应答入口假实现（满足 SeatAnswerPort 协议）：记录代行上下文。"""

    def __init__(self, body: str) -> None:
        self.body = body
        self.contexts: List[Dict[str, Any]] = []

    def answer(self, inquiry: ProxyInquiry, *,
               proxy_context: Dict[str, Any]) -> str:
        self.contexts.append(dict(proxy_context))
        return self.body


class FlakySeatPort:
    """可控故障席位通道：fail 为真时抛异常，否则返回预设 body。"""

    def __init__(self, body: str = "田丰代答：通道已恢复。") -> None:
        self.body = body
        self.fail = True
        self.calls = 0

    def answer(self, inquiry: ProxyInquiry, *,
               proxy_context: Dict[str, Any]) -> str:
        self.calls += 1
        if self.fail:
            raise ConnectionError("席位通道不可达")
        return self.body


class EmptySeatPort:
    def answer(self, inquiry: ProxyInquiry, *,
               proxy_context: Dict[str, Any]) -> str:
        return "   "


class FakeStatusSource:
    """状态源假实现（满足 StatusSourcePort 协议）：按 claim 计数返回预设结果。"""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.claims_seen: List[str] = []

    def read_status(self, *, claim: StatusClaim) -> Any:
        self.claims_seen.append(claim.claim_text)
        return self.result


class RaisingStatusSource:
    def read_status(self, *, claim: StatusClaim) -> Any:
        raise RuntimeError("状态源不可达")


class FailingLedger:
    """落盘故障假台账（满足 LedgerPort 协议）：第 fail_from 次 save 起抛
    LedgerError；load 委托真台账（只读路径不受影响）。"""

    def __init__(self, inner: FileLedger, fail_from: int = 3) -> None:
        self.inner = inner
        self.fail_from = fail_from
        self.saves = 0

    def save(self, record: Dict[str, Any]) -> Path:
        self.saves += 1
        if self.saves >= self.fail_from:
            raise LedgerError("模拟落盘故障：磁盘不可写")
        return self.inner.save(record)

    def load(self, trace_id: str) -> Dict[str, Any]:
        return self.inner.load(trace_id)


def build_gate(root: Path, *, seat_body: str = SEAT_BODY,
               status_result: Any = None,
               ledger: Any = None,
               clock_now: datetime = T
               ) -> Tuple[ProxyDialogueGate, FakeSeatPort, FakeStatusSource,
                          Any]:
    seat = FakeSeatPort(seat_body)
    status = FakeStatusSource(no_writer_result() if status_result is None
                              else status_result)
    ledger = ledger if ledger is not None else FileLedger(root / "ledger")
    gate = ProxyDialogueGate(seat, status, ledger, clock=lambda: clock_now)
    return gate, seat, status, ledger


# ---- dispatch #1：正常路径（H1/H3/H5） -------------------------------------

def test_happy_path_answer_carries_identity_and_verified_refs(tmp_path):
    gate, seat, status, _ = build_gate(tmp_path, status_result=active_result())
    tid = gate.submit(make_inquiry())
    assert gate.route(tid) == SEAT_TIANFENG

    # 席位自称的 as_of/source_ref 是伪造值：呈现引用不得采信
    answer = gate.answer(tid, seat_id=SEAT_TIANFENG,
                         claims=[claim(as_of="1999-01-01T00:00:00+00:00",
                                       source_ref="seat-self-claimed")],
                         answered_at=T)

    assert answer.status == ANSWERED_BY_PROXY
    assert answer.proxy_for == "QS1" == PROXY_FOR
    assert answer.seat_id == SEAT_TIANFENG
    assert answer.answered_at == T
    assert answer.body == SEAT_BODY

    presented = gate.present(tid)
    assert presented is not None
    assert presented.proxy_for == PROXY_FOR
    assert presented.seat_id == SEAT_TIANFENG
    assert presented.answered_at == T
    assert presented.status == ANSWERED_BY_PROXY

    # 引用值一律取自状态源解析结果，而非席位自称（H3）
    assert len(presented.status_refs) == 1
    ref = presented.status_refs[0]
    assert ref.claim_text == CLAIM_TEXT
    assert ref.as_of == POINT_AS_OF
    assert ref.source_ref == POINT_REF

    # gate 经席位端口取答复，并明确告知其在代行（H1 上下文）
    assert seat.contexts == [{"proxy_for": "QS1", "seat_id": SEAT_TIANFENG,
                              "trace_id": tid}]
    assert status.claims_seen == [CLAIM_TEXT]


# ---- dispatch #2：非路由席位作答（H7） --------------------------------------

def test_non_routed_seat_answer_is_refused_and_never_presented(tmp_path):
    root = tmp_path / "ledger"
    gate, seat, _, _ = build_gate(tmp_path)
    tid = gate.submit(make_inquiry())
    gate.route(tid)

    answer = gate.answer(tid, seat_id="simayi", answered_at=T)

    assert answer.status == REFUSED_MISFIT
    assert answer.body == REFUSAL_TEXT
    assert answer.status_refs == ()
    # 呈现集合中无该答复；错席位甚至不被询问
    assert gate.present(tid) is None
    assert seat.contexts == []

    # 席位原始内容不得以任何形态落入台账（只落 gate 拒绝文本）
    raw = (root / ("%s.json" % tid)).read_bytes().decode("utf-8")
    assert SEAT_BODY not in raw
    assert json.loads(raw)["answer"]["body"] == REFUSAL_TEXT
    assert json.loads(raw)["answer"]["status"] == REFUSED_MISFIT

    # 拒绝落定后归口席不得绕答（会话锁定，交人工复核）
    with pytest.raises(RuntimeError):
        gate.answer(tid, seat_id=SEAT_TIANFENG, answered_at=T)


# ---- dispatch #3：任一 claim 不可解析（H3） ----------------------------------

def test_unresolvable_claim_fails_closed_with_honest_replacement(tmp_path):
    gate, _, _, _ = build_gate(tmp_path, status_result=no_writer_result())
    tid = gate.submit(make_inquiry())
    gate.route(tid)

    answer = gate.answer(tid, seat_id=SEAT_TIANFENG,
                         claims=[claim(CLAIM_TEXT), claim("策略池运行中")],
                         answered_at=T)

    assert answer.status == NO_STATUS_SOURCE
    presented = gate.present(tid)
    assert presented is not None  # 诚实答复（无真实状态）仍可呈现
    # 原始 claim 文本被诚实替换，不出现；禁估计值/历史值/演示值填充
    assert CLAIM_TEXT not in presented.body
    assert "策略池运行中" not in presented.body
    assert presented.body.count(NO_STATUS_TEXT) == 2
    assert "3 只" not in presented.body
    assert all(r.claim_text == NO_STATUS_TEXT for r in presented.status_refs)
    assert all(r.as_of is None and r.source_ref is None
               for r in presented.status_refs)


# ---- dispatch #4：状态源全故障模式参数化（H3） ------------------------------

@pytest.mark.parametrize("make_source", [
    lambda: FakeStatusSource(no_writer_result()),
    lambda: FakeStatusSource({"state": "no_source", "point": None,
                              "reason": "status_point 文件缺失"}),
    lambda: FakeStatusSource({"state": "stale",
                              "point": {"as_of": "2026-10-18T01:00:00+00:00",
                                        "source_ref": POINT_REF},
                              "reason": "状态点已 900 秒未更新"}),
    lambda: FakeStatusSource({"state": "active", "point": None}),
    lambda: FakeStatusSource({"state": "active"}),
    lambda: FakeStatusSource({"state": "active",
                              "point": {"as_of": "not-a-time",
                                        "source_ref": POINT_REF}}),
    lambda: FakeStatusSource({"state": "active",
                              "point": {"as_of": POINT_AS_OF}}),
    lambda: FakeStatusSource({"state": "active",
                              "point": {"as_of": POINT_AS_OF,
                                        "source_ref": "   "}}),
    lambda: FakeStatusSource({"state": "passed", "point": {"as_of": POINT_AS_OF,
                                                           "source_ref": POINT_REF}}),
    lambda: FakeStatusSource({"point": {"as_of": POINT_AS_OF,
                                        "source_ref": POINT_REF}}),
    lambda: FakeStatusSource("损坏：不是映射"),
    lambda: FakeStatusSource(None),
    lambda: RaisingStatusSource(),
], ids=["no_writer", "no_source", "stale", "active_no_point", "active_missing_point",
        "as_of_unparseable", "missing_source_ref", "blank_source_ref",
        "state_outside_vocabulary", "missing_state", "non_mapping", "none",
        "port_raises"])
def test_status_source_failures_all_fail_closed(tmp_path, make_source):
    seat = FakeSeatPort(SEAT_BODY)
    ledger = FileLedger(tmp_path / "ledger")
    gate = ProxyDialogueGate(seat, make_source(), ledger, clock=lambda: T)
    tid = gate.submit(make_inquiry())
    gate.route(tid)

    answer = gate.answer(tid, seat_id=SEAT_TIANFENG,
                         claims=[claim(CLAIM_TEXT)], answered_at=T)

    assert answer.status == NO_STATUS_SOURCE
    presented = gate.present(tid)
    assert presented is not None
    assert CLAIM_TEXT not in presented.body
    assert NO_STATUS_TEXT in presented.body
    assert presented.status_refs[0].claim_text == NO_STATUS_TEXT
    assert presented.status_refs[0].as_of is None
    assert presented.status_refs[0].source_ref is None
    # 失败关闭不是错误：不向席位端口外泄、不造数
    assert "3 只" not in presented.body


# ---- dispatch #5：冒充负面（H1/H2） -----------------------------------------

def test_impersonating_body_still_marked_as_proxy(tmp_path):
    body = "我是司马懿（QS1 本体），现在亲自回复：量化口径以我为准。"
    gate, seat, _, _ = build_gate(tmp_path, seat_body=body,
                                  status_result=active_result())
    tid = gate.submit(make_inquiry())
    gate.route(tid)

    answer = gate.answer(tid, seat_id=SEAT_TIANFENG, claims=(), answered_at=T)

    assert answer.status == ANSWERED_BY_PROXY
    presented = gate.present(tid)
    assert presented is not None
    # 席位自称"QS1 本体"不改变 gate 标识：代行标记优先，且强制携带
    assert presented.proxy_for == "QS1"
    assert presented.seat_id == SEAT_TIANFENG
    assert presented.answered_at == T
    # 席位被告知自己在代行，而非以 QS1 身份作答
    assert seat.contexts[0]["proxy_for"] == "QS1"
    # 原始 body 无任何单独呈现入口（只能以带标识的答复形态出现）
    assert presented.body == body
    for leaked in ("raw_body", "seat_body", "original_body"):
        assert leaked not in dir(gate)


# ---- dispatch #6：词表封闭 ---------------------------------------------------

@pytest.mark.parametrize("scenario", ["answered", "no_status", "misfit",
                                      "not_delivered"])
def test_gate_only_produces_closed_four_state_vocabulary(tmp_path, scenario):
    sub = tmp_path / scenario
    if scenario == "not_delivered":
        ledger: Any = FailingLedger(FileLedger(sub / "ledger"), fail_from=3)
    else:
        ledger = FileLedger(sub / "ledger")
    gate, _, _, _ = build_gate(
        sub, status_result=active_result() if scenario == "answered"
        else no_writer_result(), ledger=ledger)
    tid = gate.submit(make_inquiry())
    gate.route(tid)

    if scenario == "misfit":
        answer = gate.answer(tid, seat_id="simayi", answered_at=T)
    else:
        answer = gate.answer(tid, seat_id=SEAT_TIANFENG,
                             claims=[claim(CLAIM_TEXT)], answered_at=T)

    expected = {"answered": ANSWERED_BY_PROXY, "no_status": NO_STATUS_SOURCE,
                "misfit": REFUSED_MISFIT, "not_delivered": NOT_DELIVERED}
    assert answer.status == expected[scenario]
    assert answer.status in ANSWER_STATES
    # 拒绝/未交付不得呈现；answered/no_status 可呈现
    if scenario in ("misfit", "not_delivered"):
        assert gate.present(tid) is None
    else:
        assert gate.present(tid) is not None

    # 词表封闭：恰好四态，无"通过/正常"类结论性取值；可呈现态恰好两态
    assert ANSWER_STATES == frozenset({"answered_by_proxy", "no_status_source",
                                       "refused_misfit", "not_delivered"})
    assert PRESENTABLE_STATES == frozenset({ANSWERED_BY_PROXY,
                                            NO_STATUS_SOURCE})
    for state in ANSWER_STATES:
        for conclusive in ("通过", "正常", "批准", "成功"):
            assert conclusive not in state
    # 状态源词表沿用 status_point 契约四态
    assert SOURCE_STATES == frozenset({"no_source", "no_writer", "active",
                                       "stale"})


# ---- dispatch #7：转派未签认 ------------------------------------------------

def test_route_never_transfers_to_advisor_before_signing(tmp_path):
    assert TRANSFER_TO_ADVISOR_PENDING_SIGNING is True
    gate, _, _, _ = build_gate(tmp_path)
    texts = ["常规量化问询", "产品方案比较：A 策略 vs B 策略",
             "架构评审：数据链路怎么改", "帮我选个方案", "监控口径要不要改"]
    seats = []
    last_tid = ""
    for i, text in enumerate(texts * 5):
        last_tid = gate.submit(make_inquiry(inquiry_id="inq-%d" % i, text=text))
        seats.append(gate.route(last_tid))

    assert set(seats) == {SEAT_TIANFENG}
    assert SEAT_ZHUGELIANG not in seats
    record = gate.review(last_tid)
    assert record["route"]["seat_id"] == SEAT_TIANFENG
    assert record["route"]["transfer_to_advisor_pending_signing"] is True


# ---- dispatch #8：无审批/交易/许可入口（H4 结构性） --------------------------

def test_module_has_no_approval_order_permit_members():
    import quant_backtest.agent_bridge.proxy_dialogue as module

    forbidden = {"approve", "approval", "approved", "order", "orders",
                 "ordering", "permit", "permits", "permitted", "permission",
                 "permissions"}
    for name in dir(module):
        tokens = set(re.split(r"_+", name.lower()))
        assert not (tokens & forbidden), "模块存在审批/交易/许可类成员：%s" % name

    source = inspect.getsource(module)
    assert re.search(r"\bdef\s+\w*(approv|order|permit)", source, re.I) is None
    assert re.search(r"^\s*(?:import|from)\s+\w*(?:approv|order|permit)",
                     source, re.I | re.M) is None


# ---- dispatch #9：落盘可取证（H5） ------------------------------------------

def test_ledger_roundtrip_is_atomic_reviewable_and_fails_loud(tmp_path):
    root = tmp_path / "ledger"
    gate, _, _, _ = build_gate(tmp_path, status_result=active_result())
    tid = gate.submit(make_inquiry())
    gate.route(tid)
    gate.answer(tid, seat_id=SEAT_TIANFENG,
                claims=[claim(as_of="1999-01-01T00:00:00+00:00",
                              source_ref="seat-self-claimed")], answered_at=T)

    # ① 写入范围隔离：只有注入根下的单文件，不碰真实数据目录
    assert sorted(p.name for p in root.iterdir()) == ["%s.json" % tid]

    # ② 跨实例（跨进程同构）复核：另一个 gate 凭 trace_id 读同一台账
    other = ProxyDialogueGate(FakeSeatPort("x"),
                              FakeStatusSource(no_writer_result()),
                              FileLedger(root), clock=lambda: T)
    record = other.review(tid)
    assert record["schema"] == SCHEMA
    assert record["trace_id"] == tid
    assert record["entry_notice"] == QS1_NO_DIALOGUE_CAPABILITY_NOTICE
    assert record["inquiry"]["text"] == "田丰，QS1 现在什么状态？"
    assert record["route"]["seat_id"] == SEAT_TIANFENG

    # ③ 复核记录与呈现完全一致（逐字段，含引用）
    presented = gate.present(tid)
    stored = record["answer"]
    assert stored["status"] == presented.status == ANSWERED_BY_PROXY
    assert stored["proxy_for"] == presented.proxy_for == "QS1"
    assert stored["seat_id"] == presented.seat_id == SEAT_TIANFENG
    assert stored["answered_at"] == iso(presented.answered_at)
    assert stored["body"] == presented.body
    assert stored["inquiry_id"] == presented.inquiry_id
    assert stored["trace_id"] == presented.trace_id
    assert len(stored["status_refs"]) == len(presented.status_refs) == 1
    sref = stored["status_refs"][0]
    ref = presented.status_refs[0]
    assert sref["claim_text"] == ref.claim_text == CLAIM_TEXT
    assert sref["as_of"] == ref.as_of == POINT_AS_OF
    assert sref["source_ref"] == ref.source_ref == POINT_REF

    # ④ 损坏/半条/trace 不符/顶层非对象：读者显式报错，不静默回落
    path = root / ("%s.json" % tid)
    good = path.read_bytes()
    path.write_bytes(b'{"trace_id": "pd-x", "answer": {"bo')  # 半条
    with pytest.raises(LedgerError):
        other.review(tid)
    with pytest.raises(LedgerError):
        gate.present(tid)
    path.write_bytes(good[:len(good) // 2])  # 截断的有效 JSON
    with pytest.raises(LedgerError):
        other.review(tid)
    path.write_bytes(b'["not", "a", "dict"]')  # 顶层非对象
    with pytest.raises(LedgerError):
        other.review(tid)
    path.write_text('{"trace_id": "pd-someone-else"}', encoding="utf-8")
    with pytest.raises(LedgerError):
        other.review(tid)

    # ⑤ 缺失：未落盘=未发生，显式报错
    path.unlink()
    with pytest.raises(LedgerError):
        other.review(tid)


# ---- H6：诚实空态 -----------------------------------------------------------

def test_entry_notice_marks_qs1_without_dialogue_capability(tmp_path):
    gate, _, _, _ = build_gate(tmp_path)
    tid = gate.submit(make_inquiry())
    record = gate.review(tid)
    assert record["entry_notice"] == QS1_NO_DIALOGUE_CAPABILITY_NOTICE
    assert "无对话能力" in record["entry_notice"]
    assert "代行" in record["entry_notice"]
    assert "只读影子" in record["entry_notice"]
    # 不出现假在线/假历史/演示值话术
    blob = json.dumps(record, ensure_ascii=False)
    for fake in ("QS1 在线", "已上线", "演示值", "模拟数据", "假按钮"):
        assert fake not in blob


# ---- 前置状态守卫（顺序强制，RuntimeError 不静默） ---------------------------

def test_prerequisite_order_is_enforced_with_runtime_errors(tmp_path):
    gate, _, _, _ = build_gate(tmp_path)
    unknown = "pd-" + "a" * 32
    with pytest.raises(RuntimeError):
        gate.route(unknown)
    with pytest.raises(RuntimeError):
        gate.answer(unknown, seat_id=SEAT_TIANFENG, answered_at=T)

    tid = gate.submit(make_inquiry())
    with pytest.raises(RuntimeError):
        gate.answer(tid, seat_id=SEAT_TIANFENG, answered_at=T)  # 未分流

    gate.route(tid)
    gate.answer(tid, seat_id=SEAT_TIANFENG, claims=(), answered_at=T)
    with pytest.raises(RuntimeError):
        gate.answer(tid, seat_id=SEAT_TIANFENG, claims=(), answered_at=T)


def test_future_timestamps_are_rejected(tmp_path):
    gate, _, _, _ = build_gate(tmp_path)
    future = T + timedelta(hours=1)
    with pytest.raises(ValueError):
        gate.submit(make_inquiry(asked_at=future))
    tid = gate.submit(make_inquiry())
    gate.route(tid)
    with pytest.raises(ValueError):
        gate.answer(tid, seat_id=SEAT_TIANFENG, answered_at=future)


def test_unsafe_trace_id_and_bad_inputs_are_rejected(tmp_path):
    gate, _, _, _ = build_gate(tmp_path)
    for bad in ["../escape", "a/b", "", "x" * 65, ".", "..", "pd x"]:
        with pytest.raises(ValueError):
            gate.submit(make_inquiry(trace_id=bad))
    with pytest.raises(ValueError):
        gate.submit(ProxyInquiry(inquiry_id="", text="t", trace_id=None,
                                 asked_at=T))
    with pytest.raises(ValueError):
        gate.submit(ProxyInquiry(inquiry_id="inq", text="", trace_id=None,
                                 asked_at=T))
    with pytest.raises(ValueError):
        gate.submit(ProxyInquiry(inquiry_id="inq", text="t", trace_id=None,
                                 asked_at=datetime(2026, 10, 19, 2)))  # naive
    with pytest.raises(ValueError):
        gate.review("../etc/passwd")

    # 重复 trace_id 入站即拒（一问一 trace）
    tid = gate.submit(make_inquiry())
    with pytest.raises(ValueError):
        gate.submit(make_inquiry(inquiry_id="inq-2", trace_id=tid))


def test_seat_channel_failures_are_not_delivered_and_retryable(tmp_path):
    seat = FlakySeatPort()
    ledger = FileLedger(tmp_path / "ledger")
    gate = ProxyDialogueGate(seat, FakeStatusSource(no_writer_result()),
                             ledger, clock=lambda: T)
    tid = gate.submit(make_inquiry())
    gate.route(tid)

    # 席位通道异常 → not_delivered（视为未发生，不呈现，未落定可重试）
    answer = gate.answer(tid, seat_id=SEAT_TIANFENG, claims=(), answered_at=T)
    assert answer.status == NOT_DELIVERED
    assert gate.present(tid) is None
    assert gate.review(tid)["answer"] is None

    # 空答复同样 not_delivered
    seat.fail = False
    empty_gate = ProxyDialogueGate(EmptySeatPort(),
                                   FakeStatusSource(no_writer_result()),
                                   FileLedger(tmp_path / "ledger2"),
                                   clock=lambda: T)
    tid2 = empty_gate.submit(make_inquiry())
    empty_gate.route(tid2)
    assert empty_gate.answer(tid2, seat_id=SEAT_TIANFENG, claims=(),
                             answered_at=T).status == NOT_DELIVERED

    # 通道恢复后可重答并落定
    settled = gate.answer(tid, seat_id=SEAT_TIANFENG, claims=(), answered_at=T)
    assert settled.status == ANSWERED_BY_PROXY
    assert gate.present(tid) is not None
