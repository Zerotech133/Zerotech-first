# -*- coding: utf-8 -*-
"""W-Q1 事件回执账本（WO-QT-20261020-007，缺口 W-Q1 升级）。

定纲（老田 2026-10-20）：许褚信号产生后，投研团队与交易室团队**并行独立
响应**——两团队订阅同一信号事件，互不等待、互不感知顺序；投研收悉、操盘
收悉、投研结论、操盘动作是**四种不同回执**，各自独立入账。收悉（ack）
只证明「收到」，不证明「开过会」或「改过计划」——投递成功≠内容产出
（现有 RESEARCH_REVIEW 投递成功曾被误读为投研已开会，本模块从 API 语义
上堵死这条误读路径）。

四类回执（receipt_kind）：
  - research_ack        投研收悉（ack 类：只记收悉，禁止携带结论载荷）
  - trader_ack          操盘收悉（ack 类：同上）
  - research_conclusion 投研结论（实质类：必须携带结论载荷）
  - trader_action       操盘动作（实质类：必须携带动作载荷）

存储选型：append-only jsonl（与 signal_rejections.jsonl 同风格）——
  ① 与 -005 拒单账风格一致，审计与人工核对可用行工具直接看；
  ② 天然 append-only，账本层零 UPDATE/DELETE 语义；
  ③ 不引入 sqlite 依赖，跨团队消费（006 会议账、司马懿对外出口）只读
     即可。防改写用「逐条自校验 sha256 + verify_integrity」做篡改检测
     （改写→显式拒绝），弥补 jsonl 无触发器保护的缺口。

并行响应语义：
  - record_receipt() 任意时序入账，四类回执无顺序依赖、无串行等待；
  - timeline(signal_id) 返回该信号四类回执时间线（审计用）；
  - completeness(signal_id) 报告已收/未收回执类型（供司马懿对外汇报与
    超时告警用，只读不阻塞、不推断、不催办）。

幂等口径（选「拒绝」非「去重」）：同 (signal_id, receipt_kind, actor)
重复入账 → 显式抛 DuplicateReceiptError。理由：重复入账多半是生产方
bug 或事件重放，静默去重会掩盖事故；且原回执已在账，去重不新增信息，
append-only 审计口径下显式拒绝更符合 closed on failure。

投递≠内容（API 语义硬约束）：
  - ack 类回执 payload 必须为空——收到载荷即报错（ack_payload_forbidden），
    防止「投递成功冒充开会/改计划」；
  - 实质类回执 payload 必须为非空 dict——无内容的结论/动作不收
    （payload_required）。

signal_id 契约：与 signal_consumer（WO-QT-20261020-005）normalize_signal
的 signal_id 校验同规则（只许字母数字与 _.:-，禁路径注入）。采用常量
镜像而非 import 复用——-005 未导出该正则常量，且 signal_consumer 依赖
链上有 WO-006 在途改动模块，本模块保持零依赖 stdlib-only，契约对齐由
单测做源级断言（见 test_event_receipts.py）。

失败语义（closed on failure）：字段缺失/未知回执类型/signal_id 非法/
ack 带载荷/实质回执无载荷/账本被改写 → 显式抛 ReceiptError 族异常，
禁静默降级；校验失败不写任何行。

未接线说明（汇报口径）：本模块是回执账本层——真实订阅分发总线（发布方
=信号池 P7，当前未持久化）不存在，许褚触发链为 offline shadow；谁在
何时调用 record_receipt 由后续工单接线（等 -006 合并后另开小单接
006 会议账消费 research_conclusion 载荷）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path

RECEIPT_SCHEMA_VERSION = 1

# 四类回执（定纲：四种不同回执，投研/交易室各两条）
RESEARCH_ACK = "research_ack"
TRADER_ACK = "trader_ack"
RESEARCH_CONCLUSION = "research_conclusion"
TRADER_ACTION = "trader_action"

RECEIPT_KINDS = (RESEARCH_ACK, TRADER_ACK, RESEARCH_CONCLUSION, TRADER_ACTION)
ACK_KINDS = frozenset({RESEARCH_ACK, TRADER_ACK})
SUBSTANTIVE_KINDS = frozenset({RESEARCH_CONCLUSION, TRADER_ACTION})

# signal_id 契约（镜像 signal_consumer.normalize_signal 的规则：禁路径注入）
SIGNAL_ID_PATTERN = r"[A-Za-z0-9_.:-]+"

# 默认回执账文件（append-only jsonl；测试注入临时路径）
DEFAULT_RECEIPT_LOG = Path(__file__).resolve().parent.parent / "data" / "signal_receipts.jsonl"

# 回执条目固定字段（其余字段一律不收，防夹带）
_ENTRY_FIELDS = ("ts", "signal_id", "receipt_kind", "actor", "payload",
                 "plan_version", "combo_id", "entry_sha256",
                 "receipt_schema_version")


class ReceiptError(Exception):
    """回执非法或账本异常（closed on failure）。携带 reason_code 供上游归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class DuplicateReceiptError(ReceiptError):
    """幂等冲突：同 (signal_id, receipt_kind, actor) 已入账（拒绝，不静默去重）。"""


class ReceiptLedgerTampered(ReceiptError):
    """账本被改写：某行内容与其自校验 sha256 不符（append-only 被破坏）。"""


# 跨线程入账互斥（check-then-append 临界区）；跨进程并发为已知边界，
# 见契约文档「并行语义与边界」节。
_APPEND_LOCK = threading.Lock()


def validate_signal_id(signal_id) -> str:
    """signal_id 契约校验（对齐 -005：非空 + 安全字符集）。closed on failure。"""
    if not isinstance(signal_id, str) or not signal_id.strip():
        raise ReceiptError("signal_id_invalid", f"signal_id 须为非空字符串: {signal_id!r}")
    if not re.fullmatch(SIGNAL_ID_PATTERN, signal_id):
        raise ReceiptError("signal_id_invalid",
                           f"signal_id 只许字母数字与 _.:- （对齐 -005 契约）: {signal_id!r}")
    return signal_id


def _entry_digest(entry: dict) -> str:
    """条目自校验指纹（不含 entry_sha256 自身；改写检测的锚点）。"""
    blob = json.dumps({k: v for k, v in entry.items() if k != "entry_sha256"},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _check_receipt(signal_id, receipt_kind, actor, payload,
                   plan_version, combo_id) -> dict:
    """入账前全量校验，返回规范化字段。任何不合法抛 ReceiptError。"""
    validate_signal_id(signal_id)
    if receipt_kind not in RECEIPT_KINDS:
        raise ReceiptError("receipt_kind_invalid",
                           f"回执类型只认四类 {list(RECEIPT_KINDS)}: {receipt_kind!r}")
    if not isinstance(actor, str) or not actor.strip():
        raise ReceiptError("actor_invalid", f"actor（角色 id）须为非空字符串: {actor!r}")
    # 投递≠内容：ack 只记收悉，禁止携带结论载荷（防「投递成功冒充开会」）
    if receipt_kind in ACK_KINDS:
        if payload:
            raise ReceiptError(
                "ack_payload_forbidden",
                f"ack 类回执（{receipt_kind}）只记收悉，禁止携带载荷（投递≠内容）: {payload!r}")
    # 实质回执必须有内容：无载荷的结论/动作不收（防「空口办结」）
    else:
        if not isinstance(payload, dict) or not payload:
            raise ReceiptError(
                "payload_required",
                f"实质回执（{receipt_kind}）必须携带非空 dict 载荷（结论/动作详情）")
    out = {"payload": dict(payload) if payload else None,
           "plan_version": None, "combo_id": None}
    for k, v in (("plan_version", plan_version), ("combo_id", combo_id)):
        if v is not None:
            if not isinstance(v, str) or not v.strip():
                raise ReceiptError(f"{k}_invalid", f"{k} 须为非空字符串或 None: {v!r}")
            out[k] = v
    return out


def _append_line(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_ledger(log_path: Path) -> list[dict]:
    """整本读入并逐行校验（行损坏/被改写 → 显式抛错，closed on failure）。"""
    if not log_path.exists():
        return []
    entries = []
    with log_path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError as err:
                raise ReceiptError("ledger_corrupt",
                                   f"回执账第 {lineno} 行不是合法 JSON（closed on failure）") from err
            if not isinstance(e, dict) or "entry_sha256" not in e:
                raise ReceiptError("ledger_corrupt",
                                   f"回执账第 {lineno} 行缺 entry_sha256（closed on failure）")
            if _entry_digest(e) != e["entry_sha256"]:
                raise ReceiptLedgerTampered(
                    "ledger_tampered",
                    f"回执账第 {lineno} 行内容与其自校验 sha256 不符——append-only 被改写（closed on failure）")
            entries.append(e)
    return entries


def record_receipt(log_path: Path | str, signal_id: str, receipt_kind: str,
                   actor: str, payload: dict | None = None,
                   plan_version: str | None = None, combo_id: str | None = None,
                   ts: str | None = None) -> dict:
    """入账一条回执（append-only，任意时序，无顺序依赖）。

    幂等：同 (signal_id, receipt_kind, actor) 重复入账 → DuplicateReceiptError
    （不写行、不改账）。校验失败不写任何行（closed on failure）。
    返回入账条目（含自校验 entry_sha256）。
    """
    fields = _check_receipt(signal_id, receipt_kind, actor, payload,
                            plan_version, combo_id)
    if ts is None:
        ts = datetime.now().isoformat(timespec="seconds")
    else:
        if not isinstance(ts, str) or not ts.strip():
            raise ReceiptError("ts_invalid", f"ts 须为 ISO 时刻文本: {ts!r}")
        try:
            datetime.fromisoformat(ts)
        except ValueError as err:
            raise ReceiptError("ts_invalid", f"ts 不是可解析 ISO 时刻: {ts!r}") from err
    entry = {"ts": ts, "signal_id": signal_id, "receipt_kind": receipt_kind,
             "actor": actor, "payload": fields["payload"],
             "plan_version": fields["plan_version"], "combo_id": fields["combo_id"],
             "receipt_schema_version": RECEIPT_SCHEMA_VERSION}
    entry["entry_sha256"] = _entry_digest(entry)
    p = Path(log_path)
    with _APPEND_LOCK:
        for e in _read_ledger(p):
            if (e.get("signal_id") == signal_id
                    and e.get("receipt_kind") == receipt_kind
                    and e.get("actor") == actor):
                raise DuplicateReceiptError(
                    "duplicate_receipt",
                    f"回执已入账（幂等拒绝）: signal={signal_id} kind={receipt_kind} actor={actor} "
                    f"原条目 ts={e.get('ts')}——重复入账疑似生产方 bug 或事件重放，不静默去重")
        _append_line(p, entry)
    return entry


def timeline(log_path: Path | str, signal_id: str) -> list[dict]:
    """某信号的四类回执时间线（按 ts 升序，审计用）。只读，不改账。"""
    validate_signal_id(signal_id)
    entries = [e for e in _read_ledger(Path(log_path)) if e.get("signal_id") == signal_id]
    entries.sort(key=lambda e: (datetime.fromisoformat(e["ts"]),))
    return entries


def completeness(log_path: Path | str, signal_id: str) -> dict:
    """某信号的回执完整度：已收/未收回执类型（只读，不阻塞、不推断、不催办）。

    供司马懿对外汇报与超时告警用。注意口径：received 只统计「类型出现过」，
    ack 出现≠实质回执出现（收悉≠办结，语义区分由四类分立保证）。
    """
    validate_signal_id(signal_id)
    seen = {e["receipt_kind"] for e in _read_ledger(Path(log_path))
            if e.get("signal_id") == signal_id}
    received = [k for k in RECEIPT_KINDS if k in seen]
    missing = [k for k in RECEIPT_KINDS if k not in seen]
    return {"signal_id": signal_id, "received": received, "missing": missing,
            "received_count": len(received), "total": len(RECEIPT_KINDS)}


def verify_integrity(log_path: Path | str) -> int:
    """全账完整性校验：任一行被改写 → ReceiptLedgerTampered。

    append-only 的防改写收口（jsonl 无 sqlite 触发器可用，用逐条自校验
    sha256 做篡改检测）。返回校验通过的条目数。
    """
    return len(_read_ledger(Path(log_path)))
