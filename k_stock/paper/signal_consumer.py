# -*- coding: utf-8 -*-
"""E-3 盯盘信号点消费链（WO-QT-20261020-005，计划 v2 §2.3 修正#2 收紧口径）。

把「合法信号点记录」接进组合跟单链：给定一条含池印记+触发证据的信号点，
在指定 combo（combo_id）账本产生带 combo_id 的执行（pending 单→既有撮合），
或按契约/persona 显式拒单。拒单与未响应一律留痕（append-only jsonl，
老田定纲：未交易样本保全）。

收紧口径（计划 v2 §2.3）：
  - 信号池只收**触发事件**——全量股票池/荀攸分档/watchlist 不得冒充信号
    点进跟单链（判别规则见 normalize_signal 的 kind 负面清单，契约 §4）；
  - 每条信号可回放触发证据：池快照印记 (pool_id, pool_version)（复用
    WO-QT-20261020-004 decision_snapshot.normalize_pool_stamp 校验）+
    触发时价格 + 触发规则 id+版本 + 时效 valid_until；
  - 治理口径（老田定纲）：许褚信号同时供投研/交易室并行响应（订阅同一
    事件），≠许褚越级外发；信号带证据进跟单链后 append-only 不可篡改
    （本模块只新增消费记录，不 update 不 delete 任何既有账本行）。

落单路径（最小侵入）：信号点规范化后合成单票 run 文件（run 文件全文含
信号证据，随决策快照 run_content 留档），复用 engine.exec_run 的既有守卫
（幂等 ref/replan/时间旅行/风控/快照 fail-visible）与 -003 combo 注册表、
-004 pool_stamp 透传——本模块不新写任何账本/风控逻辑。

失败语义（closed on failure，全部先留痕再显式抛 SignalRejected）：
  - 字段缺失/kind 负面清单/action 非法/证据缺失/池印记非法/时效非法
    → SignalPointError（normalize_signal）；
  - 已过期（valid_until < 消费时刻）/ persona 置信门槛不达 / combo 未注册
    → consume_signal 内显式拒单；
  - 落单调用返回零订单（如陈旧产物旁路未授权、幂等重放）→ 未响应留痕。

未接线说明（工单要求汇报口径）：许褚真实触发→信号池（P7）→本消费链的
桥接点当前不存在——P6 池快照未持久化、许褚触发链为 offline shadow
（watch_dispatch 侧为他人领地，本模块只读引用其契约文档），桥接属后续
工单（等 P6 持久化+许褚触发链落地）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import combo_registry
import decision_snapshot

SIGNAL_SCHEMA_VERSION = 1

# 信号点 kind：只认触发事件（负面清单见 NEGATIVE_KINDS，契约 §4）
SIGNAL_KIND_TRIGGER = "trigger_event"
NEGATIVE_KINDS = frozenset(
    {
        "full_pool",
        "pool_dump",  # 全量股票池/全池导出
        "xunyou_tier",  # 荀攸 T1/T2 分档提醒
        "watchlist",  # watchlist 本身
    }
)

# 信号点必填字段（契约 §3）：缺一即非法（closed on failure）
SIGNAL_REQUIRED = (
    "signal_id",
    "kind",
    "stock",
    "action",
    "confidence",
    "rule_id",
    "rule_version",
    "triggered_at",
    "valid_until",
    "evidence",
    "pool_stamp",
)

# 触发时证据必填键：当时价格（可回放锚点之一；池快照引用=pool_stamp，
# 触发规则=rule_id+rule_version，均为顶层字段——契约 §3）
EVIDENCE_REQUIRED = ("trigger_price",)

ACTIONS = ("BUY", "SELL")

# 默认拒单/未响应留痕文件（append-only jsonl；测试注入临时路径）
DEFAULT_REJECTION_LOG = (
    Path(__file__).resolve().parent.parent / "data" / "signal_rejections.jsonl"
)

# 消费策略名（orders.strategy 列；与 ai_team/manual/stop_loss 并列）
STRATEGY_SIGNAL_FOLLOW = "signal_follow"


class SignalPointError(Exception):
    """信号点记录非法（契约 §3/§4 校验失败）。携带 reason_code 供留痕。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class SignalRejected(Exception):
    """信号点被拒收（已留痕）。携带 reason_code 与留痕条目。"""

    def __init__(self, reason_code: str, message: str, entry: dict | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.entry = entry


def _canonical_digest(record) -> str:
    """原始记录指纹（拒单留痕用：让留痕行可对应到被拒的那条记录原文）。"""
    try:
        blob = json.dumps(record, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        blob = repr(record)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_signal(record) -> dict:
    """按契约 §3/§4 校验信号点记录，返回规范化副本（pool_stamp 已规范）。

    任何不合法直接抛 SignalPointError（closed on failure，禁静默降级）。
    负面清单判别（契约 §4）：kind 为显式声明字段，只认 trigger_event；
    kind 命中 NEGATIVE_KINDS（全量池/荀攸分档/watchlist）→ 拒收；kind
    缺失或未知值同样拒收（禁「不声明就混进来」）。"""
    if not isinstance(record, dict):
        raise SignalPointError(
            "record_invalid", f"信号点记录须为 dict: {type(record).__name__}"
        )
    for k in SIGNAL_REQUIRED:
        if k not in record:
            raise SignalPointError(
                "missing_field", f"信号点缺必填字段 {k}（契约 §3）: {sorted(record)}"
            )
    kind = record["kind"]
    if kind in NEGATIVE_KINDS:
        raise SignalPointError(
            "negative_list",
            f"负面清单命中：kind={kind} 不是触发事件，不得冒充信号点进跟单链（契约 §4）",
        )
    if kind != SIGNAL_KIND_TRIGGER:
        raise SignalPointError(
            "kind_invalid",
            f"kind 只认 {SIGNAL_KIND_TRIGGER!r}（closed on failure），收到 {kind!r}",
        )
    signal_id = record["signal_id"]
    if not isinstance(signal_id, str) or not signal_id.strip():
        raise SignalPointError(
            "signal_id_invalid", f"signal_id 须为非空字符串: {signal_id!r}"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", signal_id):
        # signal_id 要进 run 文件名（幂等键载体），只许安全字符（禁路径注入）
        raise SignalPointError(
            "signal_id_invalid", f"signal_id 只许字母数字与 _.:- : {signal_id!r}"
        )
    stock = str(record["stock"] or "").strip()
    if not stock:
        raise SignalPointError("stock_invalid", "stock 须为非空标的代码")
    action = str(record["action"] or "").upper()
    if action not in ACTIONS:
        raise SignalPointError(
            "action_invalid", f"action 只认 BUY/SELL，收到 {record['action']!r}"
        )
    conf = record["confidence"]
    if (
        not isinstance(conf, (int, float))
        or isinstance(conf, bool)
        or not 0 <= float(conf) <= 1
    ):
        raise SignalPointError("confidence_invalid", f"confidence 须在 [0,1]: {conf!r}")
    for k in ("rule_id", "rule_version"):
        if not isinstance(record[k], str) or not record[k].strip():
            raise SignalPointError(
                "rule_invalid", f"{k} 须为非空字符串（触发规则 id+版本，契约 §3）"
            )
    for k in ("triggered_at", "valid_until"):
        if not isinstance(record[k], str) or not record[k].strip():
            raise SignalPointError(
                "time_invalid", f"{k} 须为非空 ISO 时刻文本（契约 §3 时效）"
            )
    try:
        datetime.fromisoformat(record["valid_until"])
    except ValueError as e:
        raise SignalPointError(
            "valid_until_invalid",
            f"valid_until 不是可解析 ISO 时刻: {record['valid_until']!r}",
        ) from e
    evidence = record["evidence"]
    if not isinstance(evidence, dict) or not evidence:
        raise SignalPointError(
            "evidence_missing", "evidence 须为非空 dict（触发时证据，契约 §3）"
        )
    for k in EVIDENCE_REQUIRED:
        v = evidence.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise SignalPointError(
                "evidence_missing", f"evidence 缺必填键 {k} 或非法: {v!r}"
            )
    try:
        pool_stamp = decision_snapshot.normalize_pool_stamp(record["pool_stamp"])
    except decision_snapshot.PoolStampError as e:
        raise SignalPointError(
            "pool_stamp_invalid", f"池快照印记非法（契约 -004 §6）: {e}"
        ) from e
    out = {k: record[k] for k in record}
    out.update(
        stock=stock, action=action, confidence=float(conf), pool_stamp=pool_stamp
    )
    return out


def append_rejection(log_path: Path | str, entry: dict) -> dict:
    """拒单/未响应留痕：append-only 追加一行 jsonl（不 update 不 delete）。"""
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _rejection_entry(
    signal_id: str,
    combo_id: str,
    reason_code: str,
    reason: str,
    record=None,
    kind: str = "rejection",
) -> dict:
    e = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "signal_id": signal_id,
        "combo_id": combo_id,
        "reason_code": reason_code,
        "reason": reason,
        "record_sha256": _canonical_digest(record) if record is not None else None,
        "signal_schema_version": SIGNAL_SCHEMA_VERSION,
    }
    return e


def consume_signal(
    engine,
    record,
    combo_id: str,
    registry_path: Path | str | None = None,
    account_id: str | None = None,
    rejection_log: Path | str | None = None,
    now: datetime | None = None,
    plan_version: dict | None = None,
) -> dict:
    """消费一条信号点：在 combo 账本落单（带 combo_id+证据快照），或显式拒单。

    返回 {"status": "placed"|"no_order_placed", "out": <exec_run 产物>,
    "run_path": <合成 run 文件>, "rejection": <留痕条目|None>}。
    拒单路径抛 SignalRejected（先留痕后抛，closed on failure 显式拒收）。

    - combo 校验复用 combo_registry（未注册→拒收留痕，closed on failure）；
    - 池印记复用 -004 normalize_pool_stamp（无效→拒收留痕，closed on failure）；
    - persona 拒单：BUY conf < combo conf_floor → 拒收留痕（该卖还得卖：
      SELL 不受 persona 门槛限制，与 exec_run 口径一致）；
    - 时效：valid_until < 消费时刻 → 拒收留痕（过期信号不进跟单链）；
    - 账本 = account_id（缺省=combo_id，即「组合账本」口径——同一条信号
      消费到不同组合账本互不串味，ref 幂等键按 @账户 分段）。
    """
    log_path = (
        Path(rejection_log) if rejection_log is not None else DEFAULT_REJECTION_LOG
    )
    aid = account_id or combo_id

    def reject(code: str, msg: str, sid: str = "", rec=None) -> SignalRejected:
        entry = append_rejection(
            log_path, _rejection_entry(sid, combo_id, code, msg, rec)
        )
        return SignalRejected(code, msg, entry)

    # ① 契约校验（字段/负面清单/证据/池印记，closed on failure）
    try:
        sig = normalize_signal(record)
    except SignalPointError as e:
        sid = record.get("signal_id", "") if isinstance(record, dict) else ""
        raise reject(e.reason_code, str(e), sid, record) from e

    # ② 时效：过期信号拒收
    now = now or datetime.now()
    if datetime.fromisoformat(sig["valid_until"]) < now:
        raise reject(
            "expired",
            f"信号已过期：valid_until {sig['valid_until']} < 消费时刻 {now.isoformat(timespec='seconds')}",
            sig["signal_id"],
            record,
        )

    # ③ combo 注册表解析（未注册→拒收留痕）
    try:
        reg = combo_registry.load_registry(
            registry_path or combo_registry.default_registry_path()
        )
        entry = combo_registry.resolve(reg, combo_id)
    except combo_registry.ComboRegistryError as e:
        raise reject(
            "combo_invalid", f"组合注册表解析失败: {e}", sig["signal_id"], record
        ) from e

    # ④ persona 拒单（BUY 受 conf_floor 约束；SELL 该卖还得卖）
    floor = combo_registry.persona_conf_floor(entry)
    if sig["action"] == "BUY" and sig["confidence"] < floor:
        raise reject(
            "persona_conf_floor",
            f"persona 拒单：conf {sig['confidence']} < 组合 {combo_id} "
            f"门槛 {floor}（persona={entry['quad']['persona']['id']}）",
            sig["signal_id"],
            record,
        )

    # ⑤ 合成单票 run 文件（run 全文含信号证据 → 随快照 run_content 留档）
    signal_stamp = {k: v for k, v in sig.items() if k != "pool_stamp"}
    run_name = f"signal_{sig['signal_id']}.json"
    run_path = engine.db_path.parent / run_name
    payload = [
        {
            "stock": sig["stock"],
            "asof": sig["triggered_at"],
            "signal": signal_stamp,
            "outputs": {
                "D1": {"action": sig["action"], "confidence": sig["confidence"]}
            },
        }
    ]
    run_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ⑥ 复用 exec_run 全守卫落单：combo_id+pool_stamp+signal_stamp 透传，
    #    快照（combo 印记+池印记+信号证据）与落单同事务（fail-visible）
    out = engine.exec_run(
        run_path,
        strategy=STRATEGY_SIGNAL_FOLLOW,
        account_id=aid,
        combo_id=combo_id,
        registry_path=registry_path,
        pool_stamp=sig["pool_stamp"],
        signal_stamp=signal_stamp,
        plan_version=plan_version,
    )
    if out.get("orders"):
        return {"status": "placed", "out": out, "run_path": run_path, "rejection": None}
    # ⑦ 未响应留痕（定纲：未交易样本保全——幂等重放/陈旧旁路未授权等）
    why = "; ".join(f"{s.get('stock')}:{s.get('why')}" for s in out.get("skipped", []))
    entry = append_rejection(
        log_path,
        _rejection_entry(
            sig["signal_id"],
            combo_id,
            "no_order_placed",
            f"落单零订单，未响应留痕: {why}",
            record,
            kind="no_response",
        ),
    )
    return {
        "status": "no_order_placed",
        "out": out,
        "run_path": run_path,
        "rejection": entry,
    }
