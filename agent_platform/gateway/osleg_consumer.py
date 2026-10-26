# -*- coding: utf-8 -*-
"""OSLINK-W4 8795 消费者 worker 骨架（K-QUANT-20261026-OSLEG-CONSUMER）。

定位
----
离线、隔离、可审计的「gateway inbox → 归口消费」worker 骨架：扫描隔离
gateway store 的 ``inbox/*.json`` → 分类 → 默认 dry-run 只出计划；``--run``
才执行 claim → consume(answered_by="A0") → answer 摘要（不发送）→
deliver(channel="synthetic")。状态迁移与证据门槛（answered_by/answer_ref、
channel/delivered_ref、answered 后才能 deliver）完全复用
``agent_platform/gateway/inbox_gateway.py`` 的公开 API，**不改其源码、不新增
第二套语义**；本模块只做消费编排 + 幂等台账。

分类（三档，缺一不算过）
------------------------
- ``eligible``：``source ∈ {e2e, synthetic, probe}`` 且 ``kind ∈
  {task_request, question, feedback}``，且文件名与记录 id 一致、``state=inbox``。
- ``protected``：可解析但按红线不得消费 —— ① 历史真单
  ``lg-5917d4c4357b`` **硬编码永不消费**（判定优先于 source 白名单，防真单被
  合成数据误伤）；② 非白名单 source；③ 未登记 kind；④ 非 inbox 状态残留。
  每条都带 ``reason``，只读不动。
- ``malformed``：坏 JSON / 非对象 / 缺 id、kind、source、payload / 文件名与
  记录 id 不一致 / 不可读 —— 只标记隔离，**不移动、不删除、不改写**。

红灯边界（代码内不提供绕过口）
------------------------------
- **不写真实 store**：``--run`` 前硬闸拒绝 ``agent_platform/bridge_inbox``
  及其祖先/子目录；本模块不提供默认 store，根目录必须由调用方显式传入。
- **历史真单永不消费**：``PROTECTED_MSG_IDS`` 常量，测试断言其原地字节不变。
- **默认 dry-run**：只读扫描，不建目录、不写台账、不动 gateway 状态。
- **不发送**：answer 摘要只落隔离根 ``osleg_consumer/answers/``；交付标签恒为
  ``channel="synthetic"``；不 import 飞书 / HTTP / 网络栈。
- **不启服务**：无 socket / HTTP server / 无限循环；不启动 8795，单轮扫描后返回。

执行路径（``--run``）::

    claim（台账写前记录）→ answer 摘要落盘（作为 answer_ref 的真实证据文件，
    避免"来源字符串假核验"）→ inbox_gateway.consume(answered_by="A0") →
    inbox_gateway.deliver(channel="synthetic", delivered_ref="synthetic:<id>:<digest>")

幂等台账
--------
``<store_root>/osleg_consumer/ledger.json``（原子写，schema
``osleg-consumer-ledger/v1``）：按 msg_id 记录 claimed/answer_prepared/answered/
delivered 四步与状态；重复运行命中 ``status=done`` 直接跳过，不重复消费。
崩溃恢复：已 claim 未 done 的单先只读对账 gateway receipt —— 若答复归属不是
``A0`` 或 answer_ref 不是本消费者登记值，失败关闭（不夺他人答复）；否则补剩余
步骤，gateway 侧 consume/deliver 自身幂等（首次戳记获胜）。

与契约层的关系
--------------
契约 v1.0（``docs/plans/2026-10-25_OSLINK-W4_..._草案.md``）的 accepted /
report_id / trace_id 关联规则归归口契约层与 8795 接线单；本模块是 **gateway
投递层消费者骨架**，只对 source/kind 做闸门。business payload 缺
``report_id``/``trace_id`` 时在计划里记 ``warnings`` 但**不擅自回填默认值**，
是否阻断留待 8795 接线单核定（见本单未决点）。

CLI::

    python agent_platform/gateway/osleg_consumer.py --store-root <隔离目录>          # dry-run
    python agent_platform/gateway/osleg_consumer.py --store-root <隔离目录> --run    # 执行
    ... --limit N   # 本轮最多尝试 N 条新单（已 done 不计数）

退出码：0 = 本轮无失败；1 = 配置/隔离闸/台账错误或存在 failed 单。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    # gateway 包内部用绝对名导入（gateway.envelope），平台根必须在 sys.path。
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402

__all__ = [
    "ANSWERED_BY",
    "DELIVER_CHANNEL",
    "ELIGIBLE_KINDS",
    "ELIGIBLE_SOURCES",
    "PROTECTED_MSG_IDS",
    "P2B_APPROVED_MSG_IDS",
    "REAL_STORE",
    "ConsumerRefused",
    "main",
    "run_once",
    "scan",
]

# ---------------------------------------------------------------- 常量 / 闸门
REAL_STORE = _PLATFORM / "bridge_inbox"  # 生产入箱目录，本模块任何情况不得写

# gateway 投递层 kind 词表（inbox_gateway.INBOX_KINDS）；未登记 kind 失败关闭。
ELIGIBLE_KINDS = frozenset({"task_request", "question", "feedback"})
# 只消费合成/自检来源；真实来源（feishu_comm/qs1/external 等）一律 protected。
ELIGIBLE_SOURCES = frozenset({"e2e", "synthetic", "probe"})

# 历史真单白名单：2026-10-21 真实个股咨询工单，永不自动消费/迁移/补答。
# 判定优先于 source 白名单——即使有人把它伪造成 synthetic 也不得消费。
PROTECTED_MSG_IDS = frozenset({"lg-5917d4c4357b"})

# P2b 批准（老田 2026-10-26 22:4x）：真实 bridge_inbox 上仅允许消费这一张自测票。
P2B_APPROVED_MSG_IDS = frozenset({"lg-060c67fbf89a"})

# 归口执行身份仍按 Wo-FS-157/契约口径写 A0；本骨架不接真实归口席位。
ANSWERED_BY = "A0"
DELIVER_CHANNEL = "synthetic"  # 只打标签，不发送

CONSUMER_DIRNAME = "osleg_consumer"
LEDGER_FILENAME = "ledger.json"
LEDGER_SCHEMA = "osleg-consumer-ledger/v1"
PLAN_SCHEMA = "osleg-consumer-plan/v1"
RUN_SCHEMA = "osleg-consumer-run/v1"
ANSWER_SCHEMA = "osleg-consumer-answer/v1"

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ConsumerRefused(ValueError):
    """隔离红线 / 台账不可信 / 证据冲突：调用方必须失败关闭。"""


# ---------------------------------------------------------------- 小工具
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_within(child, parent) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _consumer_dir(root: Path) -> Path:
    return root / CONSUMER_DIRNAME


def _ledger_path(root: Path) -> Path:
    return _consumer_dir(root) / LEDGER_FILENAME


def _answers_dir(root: Path) -> Path:
    return _consumer_dir(root) / "answers"


def _require_store_root(store_root) -> Path:
    """显式 store 根必须已存在（不替调用方猜真实目录、不擅自建根）。"""
    try:
        root = Path(store_root)
    except TypeError as exc:
        raise ConsumerRefused("store root 非法：%r" % (store_root,)) from exc
    if not root.exists() or not root.is_dir():
        raise ConsumerRefused("store root 不存在或不是目录：%s" % root)
    return root.resolve()


def _assert_isolated(root: Path, allow_real_store: bool = False,
                     only_msg_ids=None) -> None:
    """写入硬闸：拒绝真实 bridge_inbox；P2b 批准窗口仅放行指定自测票。"""
    real = REAL_STORE.resolve()
    if _is_within(root, real) or _is_within(real, root):
        if not allow_real_store:
            raise ConsumerRefused(
                "隔离红线：拒绝在真实 bridge_inbox 上执行 --run（%s）" % root)
        ids = set(only_msg_ids or ())
        if not ids or not ids <= P2B_APPROVED_MSG_IDS:
            raise ConsumerRefused(
                "P2b 真实 bridge_inbox 仅允许 --only-msg 指定批准自测票 %s（收到 %r）"
                % (sorted(P2B_APPROVED_MSG_IDS), sorted(ids)))


def _bind_store(root: Path) -> None:
    """每个 gateway 动作前强制绑定隔离 store，杜绝误落真实 bridge_inbox。"""
    inbox_gateway.set_store(root)


def _read_json(path: Path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, "unreadable_%s" % type(exc).__name__
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not_utf8"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "bad_json"
    return data, None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp-%s" % (path.name, uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                  indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- 分类
def _classify_file(path: Path):
    """读单条 inbox 记录并分类：返回 (category, entry)。只读，不写、不移动。"""
    file_name = path.name
    rec, error = _read_json(path)
    if error is not None:
        return "malformed", {"file": file_name, "reason": error}
    if not isinstance(rec, dict):
        return "malformed", {"file": file_name, "reason": "not_object"}

    raw_id = rec.get("id")
    if isinstance(raw_id, str) and raw_id in PROTECTED_MSG_IDS:
        # 历史真单优先判定：无论 source/kind 是什么，永不动。
        return "protected", {
            "file": file_name,
            "msg_id": raw_id,
            "reason": "historical_real_order",
            "source": str(rec.get("source") or ""),
            "kind": str(rec.get("kind") or ""),
        }

    missing = [key for key in ("id", "kind", "source", "payload")
               if key not in rec]
    if missing:
        return "malformed", {"file": file_name,
                             "reason": "missing_fields:%s" % ",".join(missing)}

    msg_id, kind, source, payload = (rec["id"], rec["kind"], rec["source"],
                                     rec["payload"])
    if not isinstance(msg_id, str) or not _ID_RE.match(msg_id):
        return "malformed", {"file": file_name, "reason": "bad_id"}
    if path.stem != msg_id:
        return "malformed", {"file": file_name, "msg_id": msg_id,
                             "reason": "id_filename_mismatch"}
    if not isinstance(kind, str) or not kind.strip():
        return "malformed", {"file": file_name, "msg_id": msg_id,
                             "reason": "bad_kind"}
    if not isinstance(source, str) or not source.strip():
        return "malformed", {"file": file_name, "msg_id": msg_id,
                             "reason": "bad_source"}
    if not isinstance(payload, dict):
        return "malformed", {"file": file_name, "msg_id": msg_id,
                             "reason": "bad_payload"}

    state = rec.get("state", "inbox")
    if state != "inbox":
        return "protected", {"file": file_name, "msg_id": msg_id,
                             "reason": "state_not_inbox:%s" % state,
                             "source": source, "kind": kind}
    if source not in ELIGIBLE_SOURCES:
        return "protected", {"file": file_name, "msg_id": msg_id,
                             "reason": "source_not_whitelisted",
                             "source": source, "kind": kind}
    if kind not in ELIGIBLE_KINDS:
        return "protected", {"file": file_name, "msg_id": msg_id,
                             "reason": "kind_not_allowed",
                             "source": source, "kind": kind}

    report_id = payload.get("report_id")
    trace_id = payload.get("trace_id")
    warnings = []
    if not isinstance(report_id, str) or not report_id.strip():
        warnings.append("report_id_missing")
    if not isinstance(trace_id, str) or not trace_id.strip():
        warnings.append("trace_id_missing")
    return "eligible", {
        "file": file_name,
        "msg_id": msg_id,
        "kind": kind,
        "source": source,
        "submitted_at": str(rec.get("submitted_at") or ""),
        "payload_digest": _payload_digest(payload),
        "report_id": report_id if isinstance(report_id, str) else "",
        "trace_id": trace_id if isinstance(trace_id, str) else "",
        "warnings": warnings,
    }


def scan(store_root, only_msg_ids=None) -> dict:
    """只读扫描：分类 + 幂等台账视图（已 done 的单标 already_done）。

    dry-run 语义：不建目录、不写台账、不动 gateway 状态；台账损坏时失败关闭
    （不能证明幂等就不出执行计划）。
    only_msg_ids：P2b 窗口只把指定自测票列为 eligible，其余 eligible 转 protected 展示。
    """
    only = set(only_msg_ids or ())
    root = _require_store_root(store_root)
    plan = {
        "schema": PLAN_SCHEMA,
        "mode": "dry-run",
        "ok": True,
        "read_only": True,
        "store_root": str(root),
        "generated_at": _now_iso(),
        "inbox_present": False,
        "eligible": [],
        "protected": [],
        "malformed": [],
    }
    inbox = root / "inbox"
    if inbox.is_dir():
        plan["inbox_present"] = True
        ledger = _load_ledger(root)
        entries = ledger["entries"]
        for path in sorted(inbox.glob("*.json"), key=lambda p: p.name):
            category, item = _classify_file(path)
            if category == "eligible" and only and item.get("msg_id") not in only:
                category = "protected"
                item["reason"] = "not_in_p2b_allowlist"
            if category == "eligible":
                recorded = entries.get(item["msg_id"])
                item["already_done"] = bool(
                    isinstance(recorded, dict)
                    and recorded.get("status") == "done")
                plan["eligible"].append(item)
            else:
                plan[category].append(item)
    plan["counts"] = {
        "eligible": len(plan["eligible"]),
        "protected": len(plan["protected"]),
        "malformed": len(plan["malformed"]),
        "already_done": sum(1 for item in plan["eligible"]
                            if item.get("already_done")),
    }
    return plan


# ---------------------------------------------------------------- 幂等台账
def _empty_ledger() -> dict:
    return {"schema": LEDGER_SCHEMA, "updated_at": "", "entries": {}}


def _load_ledger(root: Path) -> dict:
    path = _ledger_path(root)
    if not path.exists():
        return _empty_ledger()
    data, error = _read_json(path)
    if error is not None:
        raise ConsumerRefused("台账不可读，拒绝执行：%s（%s）" % (path.name, error))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise ConsumerRefused("台账结构损坏，拒绝执行：%s" % path.name)
    if data.get("schema") not in (None, LEDGER_SCHEMA):
        raise ConsumerRefused("台账 schema 不识别：%r" % (data.get("schema"),))
    return data


def _save_ledger(root: Path, ledger: dict) -> None:
    ledger["schema"] = LEDGER_SCHEMA
    ledger["updated_at"] = _now_iso()
    _write_json(_ledger_path(root), ledger)


def _append_step(entry: dict, name: str, **extra) -> None:
    step = {"step": name, "at": _now_iso()}
    step.update(extra)
    entry.setdefault("steps", []).append(step)
    entry["updated_at"] = step["at"]


def _has_step(entry: dict, name: str) -> bool:
    return any(isinstance(step, dict) and step.get("step") == name
               for step in entry.get("steps") or [])


def _answer_ref(msg_id: str) -> str:
    return "%s/answers/%s.json" % (CONSUMER_DIRNAME, msg_id)


def _delivered_ref(msg_id: str, payload_digest: str) -> str:
    return "synthetic:%s:%s" % (msg_id, payload_digest[:12])


# ---------------------------------------------------------------- answer 摘要
def _ensure_answer_evidence(root: Path, item: dict, answer_ref: str) -> Path:
    """落合成 answer 摘要（不发送）；先于 consume 落盘，作 answer_ref 的真实证据。

    已存在且绑定同一 msg_id + payload 摘要 → 原样复用（保证重跑字节不变）；
    绑定不一致 → 失败关闭（不允许换包冒充答复证据）。
    """
    path = _answers_dir(root) / ("%s.json" % item["msg_id"])
    if path.exists():
        doc, error = _read_json(path)
        if error is not None or not isinstance(doc, dict):
            raise ConsumerRefused("answer 证据不可读：%s（%s）"
                                  % (path.name, error or "not_object"))
        if (doc.get("msg_id") != item["msg_id"]
                or doc.get("payload_digest") != item["payload_digest"]
                or doc.get("answer_ref") != answer_ref):
            raise ConsumerRefused("answer 证据绑定冲突，拒绝复用：%s"
                                  % item["msg_id"])
        return path
    doc = {
        "schema": ANSWER_SCHEMA,
        "msg_id": item["msg_id"],
        "kind": item["kind"],
        "source": item["source"],
        "report_id": item.get("report_id") or "",
        "trace_id": item.get("trace_id") or "",
        "payload_digest": item["payload_digest"],
        "answered_by": ANSWERED_BY,
        "answer_ref": answer_ref,
        "synthetic": True,
        "sent": False,
        "delivery_channel": "not_sent",
        "note": "离线合成答复摘要；本消费者不发送任何消息，"
                "deliver 仅打 synthetic 标签",
        "created_at": _now_iso(),
    }
    _write_json(path, doc)
    return path


# ---------------------------------------------------------------- 执行
def _receipt_sync(root: Path, msg_id: str) -> dict:
    """只读对账 gateway 现状（崩溃恢复用；不写任何东西）。"""
    _bind_store(root)
    env = inbox_gateway.receipt(msg_id)
    data = (env.get("data") or {}) if env.get("ok") else {}
    return {
        "state": str(data.get("state") or "not_found"),
        "answered": bool(data.get("answered")),
        "delivered": bool(data.get("delivered")),
        "record": data.get("record") or {},
    }


def _gateway_error(env) -> str:
    error = env.get("error") or {}
    return str(error.get("code") or "gateway_failed")


def _fail_entry(root: Path, ledger: dict, entry: dict, status: str,
                error: str) -> dict:
    """把失败写进台账（可重试），但不伪装已办；返回失败结果。"""
    entry["status"] = "failed"
    entry["last_error"] = error
    _append_step(entry, "failed", error=error)
    _save_ledger(root, ledger)
    return {"msg_id": entry.get("msg_id") or "", "status": "failed",
            "error": error, "gateway_state": status}


def _consume_one(root: Path, item: dict, ledger: dict) -> dict:
    msg_id = item["msg_id"]
    answer_ref = _answer_ref(msg_id)
    delivered_ref = _delivered_ref(msg_id, item["payload_digest"])

    entry = ledger["entries"].get(msg_id)
    if not isinstance(entry, dict):
        # claim：写前台账先落状态，崩溃后可按 steps 续跑。
        entry = {
            "msg_id": msg_id,
            "kind": item["kind"],
            "source": item["source"],
            "payload_digest": item["payload_digest"],
            "report_id": item.get("report_id") or "",
            "trace_id": item.get("trace_id") or "",
            "status": "claimed",
            "claimed_at": _now_iso(),
            "answer_ref": answer_ref,
            "delivered_ref": delivered_ref,
            "steps": [],
        }
        ledger["entries"][msg_id] = entry
        _append_step(entry, "claimed")
        _save_ledger(root, ledger)
    if entry.get("status") == "done":
        return {"msg_id": msg_id, "status": "already_done"}

    # 崩溃恢复：先只读对账；他人答复/异证据一律失败关闭，不夺权、不覆盖。
    current = _receipt_sync(root, msg_id)
    answered_already = False
    delivered_already = False
    if current["state"] == "processed":
        record = current["record"]
        owner = str(record.get("answered_by") or "")
        recorded_ref = str(record.get("answer_ref") or "")
        if owner and owner != ANSWERED_BY:
            return _fail_entry(root, ledger, entry, current["state"],
                               "answer_owner_conflict:%s" % owner)
        if recorded_ref and recorded_ref != answer_ref:
            return _fail_entry(root, ledger, entry, current["state"],
                               "answer_ref_conflict")
        answered_already = current["answered"]
        delivered_already = current["delivered"]

    # 1) 合成 answer 摘要先落盘（answer_ref 指向真实证据文件，非假来源字符串）。
    _ensure_answer_evidence(root, item, answer_ref)
    if not _has_step(entry, "answer_prepared"):
        _append_step(entry, "answer_prepared", answer_ref=answer_ref)
        _save_ledger(root, ledger)

    # 2) consume（answered_by=A0；gateway 首次戳记获胜）。
    if not answered_already:
        _bind_store(root)
        env = inbox_gateway.consume(msg_id, answered_by=ANSWERED_BY,
                                    answer_ref=answer_ref)
        if not env.get("ok"):
            return _fail_entry(root, ledger, entry, "consume",
                               "consume_failed:" + _gateway_error(env))
        data = env.get("data") or {}
        if data.get("state") != "processed":
            return _fail_entry(root, ledger, entry,
                               str(data.get("state") or "unknown"),
                               "consume_state:%s" % data.get("state"))
        _append_step(entry, "answered", answered_by=ANSWERED_BY,
                     answer_ref=answer_ref,
                     replayed=bool(data.get("replayed")))
        _save_ledger(root, ledger)
    elif not _has_step(entry, "answered"):
        _append_step(entry, "answered", answered_by=ANSWERED_BY,
                     answer_ref=answer_ref, replayed=True, reconciled=True)
        _save_ledger(root, ledger)

    # 3) deliver（channel 仅标签，绝不真投递）。
    if not delivered_already:
        _bind_store(root)
        env = inbox_gateway.deliver(msg_id, channel=DELIVER_CHANNEL,
                                    delivered_ref=delivered_ref)
        if not env.get("ok"):
            return _fail_entry(root, ledger, entry, "deliver",
                               "deliver_failed:" + _gateway_error(env))
        data = env.get("data") or {}
        if data.get("state") != "processed":
            return _fail_entry(root, ledger, entry,
                               str(data.get("state") or "unknown"),
                               "deliver_state:%s" % data.get("state"))
        _append_step(entry, "delivered", channel=DELIVER_CHANNEL,
                     delivered_ref=delivered_ref,
                     replayed=bool(data.get("replayed")))
    if not _has_step(entry, "delivered"):
        _append_step(entry, "delivered", channel=DELIVER_CHANNEL,
                     delivered_ref=delivered_ref, reconciled=True)
    entry["status"] = "done"
    entry["done_at"] = _now_iso()
    _save_ledger(root, ledger)
    return {
        "msg_id": msg_id,
        "status": "done",
        "steps": len(entry["steps"]),
        "report_id": entry.get("report_id") or "",
        "trace_id": entry.get("trace_id") or "",
        "answer_ref": answer_ref,
        "delivered_ref": delivered_ref,
        "channel": DELIVER_CHANNEL,
        "answered_by": ANSWERED_BY,
    }


def run_once(store_root, limit=None, allow_real_store=False,
             only_msg_ids=None) -> dict:
    """执行一轮消费（claim → consume → answer 摘要 → deliver）。可重复调用。

    limit：本轮最多尝试新消费 N 条；已 done 的单不计数，也不重复消费。
    allow_real_store/only_msg_ids：仅 P2b 批准窗口使用（真实 bridge_inbox 指定自测票）。
    """
    root = _require_store_root(store_root)
    ids = tuple(only_msg_ids or ())
    if ids and not set(ids) <= P2B_APPROVED_MSG_IDS:
        raise ConsumerRefused(
            "only_msg_ids 含非批准自测票：%r" % sorted(set(ids) - P2B_APPROVED_MSG_IDS))
    _assert_isolated(root, allow_real_store=allow_real_store, only_msg_ids=ids)
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ConsumerRefused("limit 必须是非负整数：%r" % (limit,))

    plan = scan(root, only_msg_ids=ids)
    ledger = _load_ledger(root)
    result = {
        "schema": RUN_SCHEMA,
        "mode": "run",
        "ok": True,
        "store_root": str(root),
        "started_at": _now_iso(),
        "limit": limit,
        "consumed": [],
        "already_done": [],
        "failed": [],
        "protected": plan["protected"],
        "malformed": plan["malformed"],
    }
    if allow_real_store:
        result["mode"] = "run_p2b"
        result["only_msg_ids"] = list(ids)
    attempted = 0
    for item in plan["eligible"]:
        if limit is not None and attempted >= limit:
            break
        recorded = ledger["entries"].get(item["msg_id"])
        if isinstance(recorded, dict) and recorded.get("status") == "done":
            result["already_done"].append({"msg_id": item["msg_id"],
                                           "status": "already_done"})
            continue
        attempted += 1
        try:
            outcome = _consume_one(root, item, ledger)
        except ConsumerRefused:
            raise  # 隔离/台账/证据冲突：失败关闭，整轮中断
        except OSError as exc:
            outcome = {"msg_id": item["msg_id"], "status": "failed",
                       "error": "io_error:%s" % exc}
        if outcome.get("status") == "done":
            result["consumed"].append(outcome)
        elif outcome.get("status") == "already_done":
            result["already_done"].append(outcome)
        else:
            result["failed"].append(outcome)
    result["finished_at"] = _now_iso()
    result["ok"] = not result["failed"]
    result["counts"] = {
        "eligible": len(plan["eligible"]),
        "consumed": len(result["consumed"]),
        "already_done": len(result["already_done"]),
        "failed": len(result["failed"]),
        "protected": len(result["protected"]),
        "malformed": len(result["malformed"]),
        "attempted": attempted,
    }
    return result


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    """CLI：默认 dry-run（只出计划）；--run 才执行；--limit N 限本轮条数。"""
    parser = argparse.ArgumentParser(
        prog="osleg_consumer",
        description="OSLINK-W4 8795 消费者 worker 骨架（默认 dry-run，隔离 store）")
    parser.add_argument("--store-root", required=True,
                        help="隔离 gateway store 根目录（含 inbox/、processed/）")
    parser.add_argument("--run", action="store_true",
                        help="执行消费（默认只出计划；拒绝真实 bridge_inbox）")
    parser.add_argument("--limit", type=int, default=None,
                        help="本轮最多尝试新消费 N 条（已 done 不计数）")
    parser.add_argument("--allow-real-store", action="store_true",
                        help="P2b 批准窗口：允许真实 bridge_inbox（必须配 --only-msg 批准自测票）")
    parser.add_argument("--only-msg", action="append", default=None,
                        help="P2b 仅消费指定自测票（可重复；必须 ⊆ 批准清单）")
    args = parser.parse_args(argv)
    try:
        if args.run:
            out = run_once(args.store_root, limit=args.limit,
                           allow_real_store=args.allow_real_store,
                           only_msg_ids=args.only_msg)
        else:
            out = scan(args.store_root)
    except ConsumerRefused as exc:
        out = {"ok": False,
               "mode": "run" if args.run else "dry-run",
               "store_root": str(args.store_root),
               "error": str(exc)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.allow_real_store and not args.run:
        return 1
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
