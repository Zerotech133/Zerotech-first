# -*- coding: utf-8 -*-
"""OSLINK 代理对话只读影子·门控契约（合同 v1 隔离最小子集，2026-10-23，待签）。

背景（D1 裁定 + 合同 v1）：
  老田 2026-10-23 裁定"执行 QS1 对话三选一，建议 A+C"：本批不给 QS1
  独立对话能力；老田的量化问询由现役席位代行。合同 v1（A+C·待签）规定：
  分流默认仅田丰（归口）单路径，转诸葛亮（建议面）的规则待归口席签认；
  答复必须携带代行标识，缺标识不得呈现；引 QS1 状态必带 as_of+source_ref，
  状态点不可得时失败关闭答"无真实状态"，不得以估计值/历史值/演示值填充。

本模块定义合同 v1 的**隔离最小可实现子集**（只读影子，G0-G2 范围）：
  - 端口抽象：席位应答、QS1 状态点读取、落盘三端口由调用方注入；本树没有
    quant_sector/status_point.py，不跨树 import，真实绑定留到接线期（W2/W3）。
  - 单路径路由：route 恒返田丰；TRANSFER_TO_ADVISOR_PENDING_SIGNING 显式
    标注转派规则未签认，route 永不返回 zhugeliang。
  - 失败关闭：任一 StatusClaim 经状态源不可解析（no_source/no_writer/stale/
    损坏/as_of 缺失/端口异常）→ 整体 no_status_source，对应 claim 文本以
    诚实文案替换；席位自称的 as_of/source_ref 不被采信，呈现引用值一律取自
    状态源解析结果。
  - 标识结构保证：proxy_for=QS1 + seat_id + answered_at 由 gate 附加，不信
    席位自称；席位原始 body 不落盘、不被单独呈现（H1/H2）。
  - 原子落盘：按 trace_id 一文件一记录，tmp + os.replace；review 凭 trace_id
    跨进程复核；未落盘=未发生（not_delivered）。

字段语义：
  ProxyInquiry   问询（inquiry_id/text/trace_id/asked_at；trace_id 缺省时
                 由 submit 生成，pd- 前缀）
  StatusClaim    席位答复中对 QS1 状态的一处引用（claim_text 必填；as_of/
                 source_ref 为席位自称值，gate 不采信、不比对、不入呈现）
  ProxyAnswer    代行答复呈现单元（inquiry_id/trace_id/status/proxy_for/
                 seat_id/answered_at/body/status_refs；其中 proxy_for/
                 seat_id/answered_at 三个标识字段一律由 gate 填写，
                 席位只能提供 body 与 claims）
  台账记录       schema/trace_id/entry_notice/inquiry/route/answer/written_at

诚实边界：
  - 本模块不启动任何服务、不发送任何消息、不读取市场数据、不虚构 QS1 活动。
  - 不定义也不 import 任何审批/交易/许可入口（H4 结构性保证）。
  - emit 类写"当前诚实状态"的职责属于 status_point 契约；本模块只记录
    "QS1 无对话能力，由归口席代行"这一事实，不声称 QS1 具备任何能力。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Tuple
import logging

SCHEMA = "agent-bridge-proxy-dialogue/v1"

# 代行标识：恒为 QS1（司马懿）；席位身份与 answered_at 由 gate 附加
PROXY_FOR = "QS1"

# 席位 ID（合同代行席：田丰=归口面，诸葛亮=建议面）
SEAT_TIANFENG = "tianfeng"
SEAT_ZHUGELIANG = "zhugeliang"

# 转诸葛亮（建议面）的分流规则未签认 → 不启用；route 永不返回 zhugeliang
TRANSFER_TO_ADVISOR_PENDING_SIGNING = True

# 答复状态词表（合同第四节，封闭，不新增；不得扩展出"通过/正常"类结论性取值）
ANSWERED_BY_PROXY = "answered_by_proxy"    # 代行席已答复，标识与引用齐全
NO_STATUS_SOURCE = "no_status_source"      # 涉 QS1 状态但状态点不可得（失败关闭）
REFUSED_MISFIT = "refused_misfit"          # 越域/错席位，明示转交未代答
NOT_DELIVERED = "not_delivered"            # 未落盘或未取得（视为未发生）
ANSWER_STATES = frozenset({
    ANSWERED_BY_PROXY, NO_STATUS_SOURCE, REFUSED_MISFIT, NOT_DELIVERED,
})

# 可呈现状态：answered_by_proxy 与 no_status_source 是诚实答复；其余不得呈现
PRESENTABLE_STATES = frozenset({ANSWERED_BY_PROXY, NO_STATUS_SOURCE})

# 状态源词表（沿用 status_point 契约的封闭词表；两本账不合并）
SOURCE_NO_SOURCE = "no_source"    # 文件缺失或不可读（不是"没有事"，是"没有证据"）
SOURCE_NO_WRITER = "no_writer"    # 文件在，但写入者声明当前无运行时
SOURCE_ACTIVE = "active"          # 有新近状态点
SOURCE_STALE = "stale"            # 状态点过期（读者降级）
SOURCE_STATES = frozenset({
    SOURCE_NO_SOURCE, SOURCE_NO_WRITER, SOURCE_ACTIVE, SOURCE_STALE,
})

# 诚实文案（H3/H6/H7）：失败关闭替换文本、拒绝文本、未交付文本、入口空态标注
NO_STATUS_TEXT = "QS1 当前无真实状态可引"
REFUSAL_TEXT = ("错席位/越域作答：未代答，按合同 H7 明示转交，"
                "由归口席（田丰）处理；本答复不呈现。")
NOT_DELIVERED_TEXT = "答复未落盘或未取得：视为未发生（not_delivered），不呈现。"
QS1_NO_DIALOGUE_CAPABILITY_NOTICE = ("QS1 无对话能力，由归口席（田丰）代行；"
                                     "本链路为只读影子，不切换生产对话流。")

# 默认台账目录（运行时数据，不入 VCS；测试经构造参数/环境变量注入临时路径）
DEFAULT_LEDGER_DIR = Path(
    os.environ.get(
        "OSLINK_PROXY_DIALOGUE_LEDGER_DIR",
        str(Path(__file__).resolve().parents[2]
            / "agent_platform" / "data" / "agent_bridge" / "proxy_dialogue"),
    )
)

# trace_id 安全字符集（防路径穿越：只允许单段文件名）
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class LedgerError(RuntimeError):
    """台账读写显式故障：缺失/损坏/半条/不可写，绝不静默回落空态。"""


@dataclass(frozen=True)
class ProxyInquiry:
    """问询：老田经唯一主 OS 入口发起的量化领域问题（不做意图改写）。"""
    inquiry_id: str
    text: str
    trace_id: Optional[str]
    asked_at: datetime


@dataclass(frozen=True)
class StatusClaim:
    """席位答复中对 QS1 状态的一处引用。

    as_of/source_ref 是席位自称值：gate 不采信、不与状态源比对、不进入
    呈现；呈现引用中的 as_of/source_ref 一律取自状态源解析结果（H3）。
    """
    claim_text: str
    as_of: Optional[str] = None
    source_ref: Optional[str] = None


@dataclass(frozen=True)
class ProxyAnswer:
    """代行答复（呈现单元）：标识由 gate 附加，席位原始 body 永不被单独呈现。"""
    inquiry_id: str
    trace_id: str
    status: str
    proxy_for: str
    seat_id: str
    answered_at: datetime
    body: str
    status_refs: Tuple[StatusClaim, ...] = ()


class SeatAnswerPort(Protocol):
    """席位应答入口（接线期绑定真实席位通道；本模块只经此端口取答复）。

    实现方注意：返回的 body 是"席位原始答复"，未经 gate 加标识，不得直接
    呈现（H1/H2）；gate 会附加 proxy_for/seat_id/answered_at 后才可呈现。
    """

    def answer(self, inquiry: ProxyInquiry, *,
               proxy_context: Mapping[str, Any]) -> str:
        """输入问询与代行上下文，输出席位原始答复 body。"""
        ...


class StatusSourcePort(Protocol):
    """QS1 状态点读取端口（对应 status_point 契约的只读归集，W2 接线后绑定）。

    实现方返回映射（参照 status_point.read_status_point 结构）：
      state   no_source | no_writer | active | stale（或缺省/损坏→按不可解析）
      point   dict | None（active 时应含 as_of/source_ref）
      reason  str（不可得原因，可选）
    gate 只认 state=active 且 point 的 as_of 可解析、source_ref 非空；
    stale 视为不可引（过期观测不得以历史值填充当前答复）。
    """

    def read_status(self, *, claim: StatusClaim) -> Mapping[str, Any]:
        """按 claim 定向解析（全局状态源实现可忽略 claim 返回当前状态点）。"""
        ...


class LedgerPort(Protocol):
    """落盘端口（原子写 tmp+os.replace；读者永不会读到半条记录）。"""

    def save(self, record: Mapping[str, Any]) -> Path:
        """全量原子写入某 trace_id 的记录，返回落盘路径。"""
        ...

    def load(self, trace_id: str) -> Dict[str, Any]:
        """按 trace_id 读回完整记录；缺失/损坏显式报错，不静默回落。"""
        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(text: Any) -> Optional[datetime]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _require_aware(moment: Any, field_name: str) -> datetime:
    if not isinstance(moment, datetime):
        raise ValueError("%s 必须是 datetime" % field_name)
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("%s 必须带时区（naive 时刻不得入账）" % field_name)
    return moment


def _reject_future(moment: datetime, field_name: str, now: datetime) -> None:
    if moment > now:
        raise ValueError("%s 晚于当前时刻（%s > %s）：拒绝预填未来时点"
                         % (field_name, _iso(moment), _iso(now)))


def _new_trace_id() -> str:
    return "pd-%s" % uuid.uuid4().hex


def _check_trace_id(trace_id: Any) -> str:
    if not isinstance(trace_id, str) or not _TRACE_ID_RE.match(trace_id):
        raise ValueError("trace_id 非法（%r）：须为 1-64 位字母/数字/_- 单段"
                         % (trace_id,))
    return trace_id


def _answer_to_dict(answer: ProxyAnswer) -> Dict[str, Any]:
    return {
        "inquiry_id": answer.inquiry_id,
        "trace_id": answer.trace_id,
        "status": answer.status,
        "proxy_for": answer.proxy_for,
        "seat_id": answer.seat_id,
        "answered_at": _iso(answer.answered_at),
        "body": answer.body,
        "status_refs": [{"claim_text": c.claim_text, "as_of": c.as_of,
                         "source_ref": c.source_ref}
                        for c in answer.status_refs],
    }


def _answer_from_dict(data: Mapping[str, Any]) -> ProxyAnswer:
    """从台账记录还原答复；任何结构异常显式报错，不静默回落。"""
    for key in ("inquiry_id", "trace_id", "status", "proxy_for",
                "seat_id", "body"):
        if not isinstance(data.get(key), str):
            raise LedgerError("答复记录字段 %s 缺失或非字符串：按损坏处理" % key)
    answered_at = _parse_iso(data.get("answered_at"))
    if answered_at is None:
        raise LedgerError("答复记录 answered_at 缺失或不可解析：按损坏处理")
    raw_refs = data.get("status_refs")
    if raw_refs is None:
        raw_refs = ()
    if not isinstance(raw_refs, (list, tuple)):
        raise LedgerError("答复记录 status_refs 不是数组：按损坏处理")
    refs = []
    for item in raw_refs:
        if not isinstance(item, Mapping):
            raise LedgerError("status_refs 条目不是对象：按损坏处理")
        text = item.get("claim_text")
        if not isinstance(text, str):
            raise LedgerError("status_refs.claim_text 缺失或非字符串：按损坏处理")
        as_of = item.get("as_of")
        source_ref = item.get("source_ref")
        for value in (as_of, source_ref):
            if value is not None and not isinstance(value, str):
                raise LedgerError("status_refs.as_of/source_ref 须为字符串或 "
                                  "null：按损坏处理")
        refs.append(StatusClaim(claim_text=text, as_of=as_of,
                                source_ref=source_ref))
    return ProxyAnswer(
        inquiry_id=data["inquiry_id"], trace_id=data["trace_id"],
        status=data["status"], proxy_for=data["proxy_for"],
        seat_id=data["seat_id"], answered_at=answered_at,
        body=data["body"], status_refs=tuple(refs))


class FileLedger:
    """按 trace_id 一文件一记录的原子台账（参考 status_point 落盘模式）。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_LEDGER_DIR

    @property
    def root(self) -> Path:
        return self._root

    def _target(self, trace_id: str) -> Path:
        _check_trace_id(trace_id)
        return self._root / ("%s.json" % trace_id)

    def save(self, record: Mapping[str, Any]) -> Path:
        trace_id = _check_trace_id(record.get("trace_id"))
        payload = dict(record)
        payload.setdefault("schema", SCHEMA)
        target = self._root / ("%s.json" % trace_id)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=".proxy_dialogue.",
                suffix=".tmp")
        except OSError as exc:
            raise LedgerError("台账不可写（%s：%s）：显式报错，不静默"
                              % (target, exc)) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, indent=1)
                         + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except (OSError, TypeError, ValueError) as exc:
            try:
                os.unlink(tmp_name)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/agent_bridge/proxy_dialogue.py", exc)
            raise LedgerError("台账写入失败（%s：%s）：显式报错，不静默"
                              % (target, exc)) from exc
        return target

    def load(self, trace_id: str) -> Dict[str, Any]:
        target = self._target(trace_id)
        if not target.exists():
            raise LedgerError("台账记录缺失（%s）：未落盘=未发生，"
                              "不得推断为无事或已通" % target)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise LedgerError("台账记录损坏/半条（%s：%s）：显式报错，"
                              "不静默回落空态"
                              % (type(exc).__name__, exc)) from exc
        if not isinstance(raw, dict):
            raise LedgerError("台账记录顶层不是对象（%s）：结构不符契约，"
                              "按损坏处理" % target)
        if raw.get("trace_id") != trace_id:
            raise LedgerError("台账记录 trace_id 不符（%r != %r）：按损坏处理"
                              % (raw.get("trace_id"), trace_id))
        return raw


@dataclass
class _Session:
    """gate 进程内会话态（台账记录以磁盘为准，此处只做顺序守卫）。"""
    inquiry: ProxyInquiry
    record: Dict[str, Any]
    routed_seat: Optional[str] = None
    answered: bool = False


class ProxyDialogueGate:
    """代理对话只读影子门控（合同 v1 隔离最小子集）。

    流程顺序强制（合同第二节）：submit → route → answer →（ledger）→
    review；跳段即 RuntimeError，不静默 pass。呈现只认 ledger 中的记录
    （落盘才可呈现，H5）。
    """

    def __init__(self, seat_port: SeatAnswerPort,
                 status_source: StatusSourcePort, ledger: LedgerPort, *,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self._seat = seat_port
        self._status = status_source
        self._ledger = ledger
        self._clock = clock or _utc_now
        self._sessions: Dict[str, _Session] = {}

    # ---- 流程①：问询入站 -------------------------------------------------

    def submit(self, inquiry: ProxyInquiry) -> str:
        """问询入站：记录 text/asked_at/trace_id，落台账，返回 trace_id。

        不做意图改写；asked_at 晚于当前时刻即拒（拒未来时点）；trace_id
        缺省时生成（pd- 前缀），重复入站即拒。
        """
        if type(inquiry) is not ProxyInquiry:
            raise TypeError("inquiry 必须是 ProxyInquiry")
        if not isinstance(inquiry.inquiry_id, str) or not inquiry.inquiry_id:
            raise ValueError("inquiry_id 必须是非空字符串")
        if not isinstance(inquiry.text, str) or not inquiry.text:
            raise ValueError("问询文本必须是非空字符串")
        asked_at = _require_aware(inquiry.asked_at, "asked_at")
        _reject_future(asked_at, "asked_at", self._clock())
        trace_id = inquiry.trace_id
        if trace_id is None:
            trace_id = _new_trace_id()
        trace_id = _check_trace_id(trace_id)
        if trace_id in self._sessions:
            raise ValueError("trace_id 重复入站（%s）：一问一 trace，不得覆盖"
                             % trace_id)
        settled = ProxyInquiry(inquiry_id=inquiry.inquiry_id,
                               text=inquiry.text, trace_id=trace_id,
                               asked_at=asked_at)
        record: Dict[str, Any] = {
            "schema": SCHEMA,
            "trace_id": trace_id,
            "entry_notice": QS1_NO_DIALOGUE_CAPABILITY_NOTICE,
            "inquiry": {"inquiry_id": settled.inquiry_id,
                        "text": settled.text, "trace_id": trace_id,
                        "asked_at": _iso(asked_at)},
            "route": None,
            "answer": None,
            "written_at": _iso(self._clock()),
        }
        self._ledger.save(record)
        self._sessions[trace_id] = _Session(inquiry=settled, record=record)
        return trace_id

    # ---- 流程②：分流 ------------------------------------------------------

    def route(self, trace_id: str) -> str:
        """分流：默认单路径到田丰（归口）。

        转诸葛亮（建议面）的规则未签认（TRANSFER_TO_ADVISOR_PENDING_SIGNING
        为 True），本方法永不返回 zhugeliang；产品/架构/方案比较类问询同样
        只走田丰，不猜。重复路由幂等返回同一席位。
        """
        session = self._session(trace_id)
        if session.routed_seat is not None:
            return session.routed_seat
        seat = SEAT_TIANFENG
        session.record["route"] = {
            "seat_id": seat,
            "routed_at": _iso(self._clock()),
            "transfer_to_advisor_pending_signing":
                TRANSFER_TO_ADVISOR_PENDING_SIGNING,
        }
        session.record["written_at"] = _iso(self._clock())
        self._ledger.save(session.record)
        session.routed_seat = seat
        return seat

    # ---- 流程③④：代行答复 -------------------------------------------------

    def answer(self, trace_id: str, *, seat_id: str,
               claims: Sequence[StatusClaim] = (),
               answered_at: Optional[datetime] = None) -> ProxyAnswer:
        """代行答复入 gate：校验席位、解析状态引用、附加标识、原子落盘。

        - seat_id 必须等于路由席位，否则 refused_misfit：不得呈现，席位端口
          不被调用，会话落定禁绕答（H7）；
        - 每个 StatusClaim 经 StatusSourcePort 解析，任一不可解析 → 整体
          no_status_source，对应 claim 文本以 NO_STATUS_TEXT 替换（失败关闭，
          禁估计值/历史值/演示值填充）；
        - proxy_for=QS1 + seat_id + answered_at 由 gate 附加，不信席位自称；
        - 席位原始 body 不落盘、不单独呈现；取答复失败或落盘失败 →
          not_delivered（视为未发生，可重试）。
        """
        session = self._session(trace_id)
        if session.answered:
            raise RuntimeError("该问询已答复落定（trace_id=%s）：一问一答，"
                               "不得改答复（含拒绝后绕答）" % trace_id)
        if session.routed_seat is None:
            raise RuntimeError("问询未分流（trace_id=%s）：顺序强制，route "
                               "之前不得作答" % trace_id)
        moment = _require_aware(
            answered_at if answered_at is not None else self._clock(),
            "answered_at")
        _reject_future(moment, "answered_at", self._clock())
        inquiry_id = session.inquiry.inquiry_id
        if seat_id != session.routed_seat:
            refusal = ProxyAnswer(
                inquiry_id=inquiry_id, trace_id=trace_id,
                status=REFUSED_MISFIT, proxy_for=PROXY_FOR, seat_id=seat_id,
                answered_at=moment, body=REFUSAL_TEXT, status_refs=())
            if not self._persist(session, refusal):
                return self._not_delivered(session, moment, inquiry_id,
                                           trace_id)
            return refusal
        try:
            raw_body = self._seat.answer(session.inquiry, proxy_context={
                "proxy_for": PROXY_FOR,
                "seat_id": session.routed_seat,
                "trace_id": trace_id,
            })
        except Exception:
            return self._not_delivered(session, moment, inquiry_id, trace_id)
        if not isinstance(raw_body, str) or not raw_body.strip():
            return self._not_delivered(session, moment, inquiry_id, trace_id)
        items = tuple(claims)
        for item in items:
            if type(item) is not StatusClaim:
                raise TypeError("claims 条目必须是 StatusClaim")
            if not isinstance(item.claim_text, str) or not item.claim_text:
                raise ValueError("StatusClaim.claim_text 必须是非空字符串")
        body = raw_body
        refs = []
        unresolved = False
        for item in items:
            resolved = self._resolve_claim(item)
            if resolved is None:
                unresolved = True
                body = body.replace(item.claim_text, NO_STATUS_TEXT)
                refs.append(StatusClaim(claim_text=NO_STATUS_TEXT))
            else:
                refs.append(StatusClaim(claim_text=item.claim_text,
                                        as_of=resolved["as_of"],
                                        source_ref=resolved["source_ref"]))
        proxy_answer = ProxyAnswer(
            inquiry_id=inquiry_id, trace_id=trace_id,
            status=NO_STATUS_SOURCE if unresolved else ANSWERED_BY_PROXY,
            proxy_for=PROXY_FOR, seat_id=session.routed_seat,
            answered_at=moment, body=body, status_refs=tuple(refs))
        if not self._persist(session, proxy_answer):
            return self._not_delivered(session, moment, inquiry_id, trace_id)
        return proxy_answer

    # ---- 流程⑤⑥：落盘与复核 ----------------------------------------------

    def present(self, trace_id: str) -> Optional[ProxyAnswer]:
        """呈现入口：仅可呈现状态的答复返回；其余（未答复/拒绝/未交付）None。

        呈现记录自台账读取而非内存，证明"落盘才可呈现"（H5）。
        """
        stored = self.review(trace_id).get("answer")
        if not isinstance(stored, Mapping):
            return None
        if stored.get("status") not in PRESENTABLE_STATES:
            return None
        return _answer_from_dict(stored)

    def review(self, trace_id: str) -> Dict[str, Any]:
        """凭 trace_id 跨进程复核：返回台账完整记录（会话/路由/答复/引用）。

        读取自落盘文件；缺失/损坏/半条/trace_id 不符显式 LedgerError，
        不静默回落（H5）。
        """
        return self._ledger.load(trace_id)

    # ---- 内部 -------------------------------------------------------------

    def _session(self, trace_id: str) -> _Session:
        _check_trace_id(trace_id)
        session = self._sessions.get(trace_id)
        if session is None:
            raise RuntimeError("问询未入站（trace_id=%s）：无会话记录，不得"
                               "路由或作答（顺序强制：submit→route→answer）"
                               % trace_id)
        return session

    def _resolve_claim(self, claim: StatusClaim) -> Optional[Dict[str, str]]:
        """经状态源解析一处引用；不可解析返回 None（失败关闭，不估计不造数）。"""
        try:
            result = self._status.read_status(claim=claim)
        except Exception:
            return None
        if not isinstance(result, Mapping):
            return None
        if result.get("state") != SOURCE_ACTIVE:
            return None
        point = result.get("point")
        if not isinstance(point, Mapping):
            return None
        as_of = _parse_iso(point.get("as_of"))
        if as_of is None:
            return None
        source_ref = point.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            return None
        return {"as_of": _iso(as_of), "source_ref": source_ref}

    def _persist(self, session: _Session, answer: ProxyAnswer) -> bool:
        """答复落定：原子落盘。成功返回 True；落盘失败回滚内存记录并返回
        False（磁盘上无此答复，调用方按 not_delivered 处理，视为未发生）。"""
        session.record["answer"] = _answer_to_dict(answer)
        session.record["written_at"] = _iso(self._clock())
        try:
            self._ledger.save(session.record)
        except LedgerError:
            session.record["answer"] = None
            return False
        session.answered = True
        return True

    def _not_delivered(self, session: _Session, moment: datetime,
                       inquiry_id: str, trace_id: str) -> ProxyAnswer:
        """未落盘/未取得：不呈现、不改写已落盘记录、会话未落定可重试。"""
        return ProxyAnswer(
            inquiry_id=inquiry_id, trace_id=trace_id, status=NOT_DELIVERED,
            proxy_for=PROXY_FOR, seat_id=session.routed_seat or "",
            answered_at=moment, body=NOT_DELIVERED_TEXT, status_refs=())
