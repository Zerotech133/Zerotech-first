# -*- coding: utf-8 -*-
"""D1 交棒回执（P1，2026-11-01 WO-D1-HANDOFF-20261101；v2 2026-11-03 WO-IFS-HANDOFF-20261103）。

D1 研究完成后把结论交 WA（盯盘）或 TR（模拟执行）；本模块只落**交棒回执**，
不生成订单、不执行、不接实盘。目标方确认（ack）仅表示“收到/拒收”，不表示已交易。

schema 演进（向后兼容）
- ``d1_handoff/v1``：code/target/action/rationale/buy_zone/stop/trigger/evidence；
- ``d1_handoff/v2``：新增 ``watch_plans[]``，把「盯盘要盯什么」结构化落库：
  ``plan_id/seat/method/conditions/expires_at/status``。v1 旧回执（无该字段）
  仍可读、可 ack、可 summary；新回执默认 v2（无 watch_plans 时为空列表）。
- 只登记计划，不执行；watch_plan 的消费/触发在 ``watch_plan_consumer``。
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "d1_handoff/v2"
SCHEMA_V1 = "d1_handoff/v1"
SUPPORTED_SCHEMAS = (SCHEMA_V1, SCHEMA)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "agent_platform" / "data" / "d1_handoffs.jsonl"
LEDGER_ENV = "D1_HANDOFF_LEDGER"
TARGETS = ("WA1", "WA2", "WA3", "TR1", "TR2", "TR3", "WATCH_TOWER")
ACTIONS = ("watch", "trade", "no_trade")
ACK_STATUS = ("received", "rejected", "handled")
REQUIRED = ("code", "target", "action", "rationale")

# ── watch_plans[]（d1_handoff/v2）────────────────────────────────────
WATCH_SEATS = ("WA1", "WA2", "WA3", "WATCH_TOWER")
WATCH_PLAN_REQUIRED = ("plan_id", "seat", "method", "conditions", "expires_at")
WATCH_PLAN_ALLOWED = WATCH_PLAN_REQUIRED + ("status",)
PLAN_STATUS_INPUT = ("planned",)  # 生产方只能登记 planned；armed/expired 由消费器状态机维护
PLAN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
MAX_WATCH_PLANS = 16
MAX_CONDITIONS_CHARS = 4000
EXECUTION_FIELDS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


class D1HandoffError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_receipt_id(now=None):
    dt = now or datetime.now()
    return "d1h-" + dt.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)


def _clip(v, n):
    return str(v or "").strip()[:n]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ledger_from_env():
    """显式覆盖回执账本路径（测试/多环境用）；未设置返回 None（走默认）。"""
    value = str(os.environ.get(LEDGER_ENV) or "").strip()
    return value or None


def _iso(value, field):
    """ISO8601 归一（接受尾部 Z）；非法抛 D1HandoffError。"""
    text = str(value or "").strip()
    if not text:
        raise D1HandoffError("missing_%s" % field, "%s 必填" % field)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise D1HandoffError("bad_%s" % field, "%s 必须是 ISO8601 时间: %r" % (field, text)) from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def _has_execution_field(value):
    """conditions 内出现执行字段即拒绝（paper-only 护栏）。"""
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if str(key).lower() in EXECUTION_FIELDS and item not in (None, "", [], {}):
            return key
    return None


def _validate_conditions(plan_id, conditions):
    if isinstance(conditions, dict):
        if not conditions:
            raise D1HandoffError("bad_conditions", "watch_plan %s 的 conditions 不能为空" % plan_id)
        for key in conditions:
            if not _clip(key, 128):
                raise D1HandoffError("bad_conditions", "watch_plan %s 的 conditions 键必须非空" % plan_id)
        bad_key = _has_execution_field(conditions)
        if bad_key:
            raise D1HandoffError(
                "execution_field_forbidden",
                "watch_plan %s 的 conditions 不得携带执行字段 %r" % (plan_id, bad_key),
            )
        payload = copy.deepcopy(conditions)
    elif isinstance(conditions, list):
        if not conditions:
            raise D1HandoffError("bad_conditions", "watch_plan %s 的 conditions 不能为空" % plan_id)
        normalized = []
        for item in conditions:
            if not isinstance(item, dict):
                raise D1HandoffError(
                    "bad_conditions", "watch_plan %s 的 conditions 列表元素必须是 object" % plan_id
                )
            bad_key = _has_execution_field(item)
            if bad_key:
                raise D1HandoffError(
                    "execution_field_forbidden",
                    "watch_plan %s 的 conditions 不得携带执行字段 %r" % (plan_id, bad_key),
                )
            normalized.append(copy.deepcopy(item))
        payload = normalized
    else:
        raise D1HandoffError(
            "bad_conditions", "watch_plan %s 的 conditions 必须是 object 或 object 列表" % plan_id
        )
    try:
        size = len(json.dumps(payload, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise D1HandoffError("bad_conditions", "watch_plan %s 的 conditions 无法 JSON 序列化" % plan_id) from exc
    if size > MAX_CONDITIONS_CHARS:
        raise D1HandoffError(
            "bad_conditions", "watch_plan %s 的 conditions 过大（%d > %d 字符）" % (plan_id, size, MAX_CONDITIONS_CHARS)
        )
    return payload


def validate_watch_plans(plans, target=None, action=None):
    """校验 watch_plans[] 并归一（status 仅允许 planned；时间归一 ISO）。"""
    if plans is None:
        return []
    if not isinstance(plans, (list, tuple)):
        raise D1HandoffError("bad_watch_plans", "watch_plans 必须是 list")
    if len(plans) > MAX_WATCH_PLANS:
        raise D1HandoffError(
            "bad_watch_plans", "watch_plans 最多 %d 条（收到 %d）" % (MAX_WATCH_PLANS, len(plans))
        )
    is_watch = str(action or "").lower() == "watch" or str(target or "").upper() in WATCH_SEATS
    seen = set()
    out = []
    for index, raw in enumerate(plans):
        where = "watch_plans[%d]" % index
        if not isinstance(raw, dict):
            raise D1HandoffError("bad_watch_plan", "%s 必须是 object" % where)
        unknown = sorted(str(key) for key in raw if key not in WATCH_PLAN_ALLOWED)
        if unknown:
            raise D1HandoffError(
                "bad_watch_plan", "%s 含未知字段: %s" % (where, ", ".join(unknown))
            )
        for key in WATCH_PLAN_REQUIRED:
            if key not in raw:
                raise D1HandoffError("missing_%s" % key, "%s 缺必填字段 %s" % (where, key))
        plan_id = _clip(raw.get("plan_id"), 64)
        if not plan_id or not PLAN_ID_RE.match(plan_id):
            raise D1HandoffError(
                "bad_plan_id", "%s 的 plan_id 非法（字母/数字开头，允许 . _ : -）: %r" % (where, plan_id)
            )
        if plan_id in seen:
            raise D1HandoffError("duplicate_plan_id", "watch_plans 重复 plan_id: %s" % plan_id)
        seen.add(plan_id)
        seat = _clip(raw.get("seat"), 16).upper()
        if seat not in WATCH_SEATS:
            raise D1HandoffError(
                "bad_plan_seat", "%s 的 seat 必须是 %s 之一" % (where, "/".join(WATCH_SEATS))
            )
        method = _clip(raw.get("method"), 64)
        if not method:
            raise D1HandoffError("missing_method", "%s 的 method 必填" % where)
        conditions = _validate_conditions(plan_id, raw.get("conditions"))
        expires_at = _iso(raw.get("expires_at"), "expires_at")
        status = _clip(raw.get("status") or "planned", 16).lower()
        if status not in PLAN_STATUS_INPUT:
            raise D1HandoffError(
                "bad_plan_status",
                "%s 的 status 只能是 planned（armed/expired 由消费器维护，收到 %r）" % (where, status),
            )
        out.append(
            {
                "plan_id": plan_id,
                "seat": seat,
                "method": method,
                "conditions": conditions,
                "expires_at": expires_at,
                "status": status,
            }
        )
    if out and not is_watch:
        raise D1HandoffError(
            "watch_plans_require_watch", "watch_plans 只能随 action=watch 或 target=WA*/WATCH_TOWER 提交"
        )
    return out


def schema_version(row):
    """行/请求的 schema 版本归一：v2 原样，其余（含旧行无 schema）视为 v1。"""
    value = _clip((row or {}).get("schema"), 32) if isinstance(row, dict) else ""
    return value if value in SUPPORTED_SCHEMAS else SCHEMA_V1


def watch_plans_of(row):
    """读回执里的 watch_plans（旧 v1 行返回 []；不发明数据）。"""
    if not isinstance(row, dict):
        return []
    plans = row.get("watch_plans")
    return [dict(item) for item in plans] if isinstance(plans, list) else []


def validate_handoff(payload):
    if not isinstance(payload, dict):
        raise D1HandoffError("bad_body", "body 必须是 object")
    declared = _clip(payload.get("schema"), 32)
    if declared and declared not in SUPPORTED_SCHEMAS:
        raise D1HandoffError("unsupported_schema", "不支持的 schema: %r（支持: %s）" % (declared, ", ".join(SUPPORTED_SCHEMAS)))
    for k in REQUIRED:
        if not _clip(payload.get(k), 200):
            raise D1HandoffError("missing_%s" % k, "%s 必填" % k)
    target = _clip(payload.get("target"), 16).upper()
    if target not in TARGETS:
        raise D1HandoffError("bad_target", "target 必须是 %s 之一" % "/".join(TARGETS))
    action = _clip(payload.get("action"), 16).lower()
    if action not in ACTIONS:
        raise D1HandoffError("bad_action", "action 必须是 %s 之一" % "/".join(ACTIONS))
    if action == "trade" and target not in ("TR1", "TR2", "TR3"):
        raise D1HandoffError("bad_target_action", "trade 只能交 TR1/TR2/TR3")
    if action == "watch" and target not in ("WA1", "WA2", "WA3", "WATCH_TOWER"):
        raise D1HandoffError("bad_target_action", "watch 只能交 WA1/WA2/WA3/WATCH_TOWER")
    if payload.get("paper_only") is False:
        raise D1HandoffError("not_paper", "只允许 paper_only=true")
    for key in EXECUTION_FIELDS:
        if key in payload and payload.get(key) not in (None, "", [], {}):
            raise D1HandoffError("execution_field_forbidden", "handoff 不得携带执行字段 %r" % key)
    buy_zone = payload.get("buy_zone")
    if buy_zone is not None:
        if not isinstance(buy_zone, (list, tuple)) or len(buy_zone) != 2:
            raise D1HandoffError("bad_buy_zone", "buy_zone 必须是 [low, high]")
        lo, hi = _f(buy_zone[0]), _f(buy_zone[1])
        if lo is None or hi is None or lo > hi:
            raise D1HandoffError("bad_buy_zone", "buy_zone 数值非法")
        buy_zone = [lo, hi]
    raw_plans = payload.get("watch_plans")
    if declared == SCHEMA_V1 and raw_plans not in (None, []):
        raise D1HandoffError("schema_mismatch", "schema=d1_handoff/v1 不得携带 watch_plans（请用 v2）")
    watch_plans = validate_watch_plans(raw_plans, target=target, action=action)
    version = declared or (SCHEMA if raw_plans is not None or "watch_plans" in payload else SCHEMA)
    return {
        "schema": version,
        "receipt_id": _clip(payload.get("receipt_id"), 40) or new_receipt_id(),
        "code": _clip(payload.get("code"), 32),
        "name": _clip(payload.get("name"), 64),
        "decision_id": _clip(payload.get("decision_id"), 40),
        "target": target,
        "action": action,
        "rationale": _clip(payload.get("rationale"), 1000),
        "buy_zone": buy_zone,
        "stop_loss": _f(payload.get("stop_loss")),
        "trigger": _clip(payload.get("trigger"), 200),
        "evidence": [_clip(x, 300) for x in (payload.get("evidence") or [])][:20],
        "watch_plans": watch_plans,
        "created_by": "D1",
        "created_at": _clip(payload.get("created_at"), 40) or _now(),
        "status": "pending_ack",
        "ack": None,
        "paper_only": True,
        "not_a_trade": True,
    }


def _ledger_path(ledger=None):
    """账本路径：显式参数 > 环境变量 D1_HANDOFF_LEDGER > 默认（生产）。"""
    return Path(ledger or ledger_from_env() or DEFAULT_LEDGER)


def read_handoffs(ledger=None):
    path = _ledger_path(ledger)
    rows = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_all(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def append_handoff(payload, ledger=None):
    row = validate_handoff(payload)
    path = _ledger_path(ledger)
    rows = read_handoffs(path)
    if any(r.get("receipt_id") == row["receipt_id"] for r in rows):
        raise D1HandoffError("duplicate_receipt", "receipt_id 已存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return row


def ack_handoff(receipt_id, by, status="received", note="", ledger=None):
    if status not in ACK_STATUS:
        raise D1HandoffError("bad_ack_status", "ack status 必须是 %s" % "/".join(ACK_STATUS))
    path = _ledger_path(ledger)
    rows = read_handoffs(path)
    hit = None
    for r in rows:
        if r.get("receipt_id") == receipt_id:
            hit = r
            break
    if hit is None:
        raise D1HandoffError("receipt_not_found", "receipt 不存在")
    if hit.get("ack"):
        raise D1HandoffError("already_acked", "receipt 已确认")
    hit["ack"] = {"by": _clip(by, 32), "status": status,
                  "note": _clip(note, 300), "acked_at": _now()}
    hit["status"] = "acked"
    _write_all(path, rows)
    return hit


def latest_for(code, ledger=None):
    code = _clip(code, 32)
    for r in reversed(read_handoffs(ledger)):
        if r.get("code") == code:
            return r
    return None


def watch_plans_from_handoffs(ledger=None):
    """只读展平回执里的 watch_plans（附回执溯源字段；不写任何状态机）。"""
    out = []
    for row in read_handoffs(ledger):
        for plan in watch_plans_of(row):
            item = dict(plan)
            item.update(
                {
                    "receipt_id": row.get("receipt_id"),
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "decision_id": row.get("decision_id"),
                    "handoff_schema": schema_version(row),
                }
            )
            out.append(item)
    return out


def summary(ledger=None):
    rows = read_handoffs(ledger)
    pending = [r for r in rows if r.get("status") == "pending_ack"]
    plans = watch_plans_from_handoffs(ledger)
    return {
        "schema": "d1_handoff_summary/v1",
        "schema_version": SCHEMA,
        "total": len(rows),
        "pending_ack": len(pending),
        "acked": len([r for r in rows if r.get("status") == "acked"]),
        "v1_rows": len([r for r in rows if schema_version(r) == SCHEMA_V1]),
        "v2_rows": len([r for r in rows if schema_version(r) == SCHEMA]),
        "watch_plans_total": len(plans),
        "watch_plans_planned": len([p for p in plans if p.get("status") == "planned"]),
        "latest": rows[-1] if rows else None,
        "paper_only": True,
    }
