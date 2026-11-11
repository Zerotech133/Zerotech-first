# -*- coding: utf-8 -*-
"""watch_plan 消费器（IFS-HANDOFF，2026-11-03 WO-IFS-HANDOFF-20261103）。

D1 交棒回执 v2 的 ``watch_plans[]`` → 持久化 watch_plan 状态机 → 触发 pending 人工确认。
**只登记/只建确认，不执行、不下单、不接实盘**；paper-only。

状态机
------
``planned``（回执登记）→ ``armed``（消费器落盘待盯）→ ``triggered``（已建 pending 确认）；
``planned/armed`` → ``expired``（过期）；
``cancelled`` 预留（人工取消）。

幂等
----
- key = ``plan_id``：同一 plan_id 重复消费不会重复建，返回 ``duplicates``；
- 同 plan_id 但来自不同 receipt → **fail-closed 冲突**（不覆盖，记入 errors）；
- 触发按 ``report_id="D1H-<plan_id>"`` 走 ``trading_bot_flow.create_confirmation`` 天然幂等；
  已 triggered 的 plan 再次触发只返回既有确认（idempotent=True），不新建、不执行。

数据
----
plan store 默认 ``agent_platform/data/watch_plans.jsonl``；显式路径/环境变量
``D1_WATCH_PLAN_LEDGER`` 优先（测试全程用临时目录，不写真实账本）。
trigger 独立账本（IFS F3c）由 ``watch_trigger_ledger`` 管理：仅显式 ``trigger_ledger``
或 ``WATCH_TRIGGER_LEDGER_PATH`` 配置时写入，未配置不产生生产写入。
"""
from __future__ import annotations

import logging
import argparse
import copy
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_trigger_adapter as adapter  # noqa: E402

SCHEMA = "watch_plan/v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN_LEDGER = ROOT / "agent_platform" / "data" / "watch_plans.jsonl"
PLAN_LEDGER_ENV = "D1_WATCH_PLAN_LEDGER"
TRIGGER_LEDGER_ENV = "WATCH_TRIGGER_LEDGER_PATH"
PLAN_STATUS = ("planned", "armed", "expired", "triggered", "cancelled")
TRIGGERABLE_STATUS = ("armed",)


class WatchPlanError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now_iso(now=None):
    if now is None:
        return datetime.now().astimezone().replace(microsecond=0).isoformat()
    if isinstance(now, datetime):
        dt = now if now.tzinfo else now.astimezone()
        return dt.replace(microsecond=0).isoformat()
    text = str(now).strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.replace(microsecond=0).isoformat()


def _parse_iso(text):
    candidate = str(text or "").strip()
    if not candidate:
        raise WatchPlanError("bad_expires_at", "expires_at 缺失")
    candidate = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    parsed = datetime.fromisoformat(candidate)
    return parsed if parsed.tzinfo else parsed.astimezone()


def plan_ledger_path(path=None):
    """plan store 路径：显式 path > 环境变量 > 默认。"""
    return Path(path or os.environ.get(PLAN_LEDGER_ENV) or DEFAULT_PLAN_LEDGER)


# ── store 读写（原子写，JSONL）───────────────────────────────────────
def _load_store(path):
    target = Path(path)
    if not target.exists():
        return []
    plans = []
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise WatchPlanError("plan_ledger_unreadable", "读 watch_plan 账本失败: %s" % exc) from exc
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            raise WatchPlanError(
                "plan_ledger_corrupt", "watch_plan 账本第 %d 行不是合法 JSON" % line_no
            ) from exc
        if not isinstance(item, dict) or not item.get("plan_id"):
            raise WatchPlanError(
                "plan_ledger_corrupt", "watch_plan 账本第 %d 行缺 plan_id" % line_no
            )
        plans.append(item)
    return plans


def _write_store(path, plans):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(plans, key=lambda item: str(item.get("plan_id") or ""))
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        for item in ordered:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(target)


# ── 回执 → plan 记录 ─────────────────────────────────────────────────
def build_plan_record(row, plan, now=None):
    """把回执 row 里的单条 watch_plan 归一成持久化记录（不判断幂等）。"""
    stamp = _now_iso(now)
    expires = _parse_iso(plan.get("expires_at"))
    status = "expired" if expires <= _parse_iso(stamp) else "armed"
    return {
        "schema": SCHEMA,
        "plan_id": plan["plan_id"],
        "seat": plan["seat"],
        "method": plan["method"],
        "conditions": copy.deepcopy(plan.get("conditions")),
        "expires_at": plan["expires_at"],
        "status": status,
        "source": {
            "handoff_schema": H.schema_version(row),
            "receipt_id": row.get("receipt_id"),
            "code": row.get("code"),
            "name": row.get("name"),
            "decision_id": row.get("decision_id"),
            "target": row.get("target"),
            "action": row.get("action"),
            "rationale": row.get("rationale"),
            "trigger": row.get("trigger"),
            "buy_zone": row.get("buy_zone"),
            "stop_loss": row.get("stop_loss"),
        },
        "armed_at": stamp if status == "armed" else None,
        "created_at": stamp,
        "updated_at": stamp,
        "triggered_at": None,
        "confirmation_id": None,
        "paper_only": True,
        "not_a_trade": True,
    }


def plans_from_handoff(handoff):
    """只读解析：回执（raw payload 或已落账 row）→ (row, plan_records)。非法回执 fail-closed。"""
    if not isinstance(handoff, dict):
        raise WatchPlanError("handoff_required", "handoff 必须是 object")
    try:
        row = H.validate_handoff(handoff)
    except H.D1HandoffError as exc:
        raise WatchPlanError("handoff_invalid", "handoff 非法（%s）: %s" % (exc.code, exc)) from exc
    return row, [build_plan_record(row, plan) for plan in row.get("watch_plans") or []]


def consume_handoff(handoff, path=None, now=None):
    """handoff → armed/expired watch_plan；幂等（plan_id 唯一），冲突 fail-closed。"""
    row, incoming = plans_from_handoff(handoff)
    target = plan_ledger_path(path)
    store = _load_store(target)
    by_id = {str(item.get("plan_id")): item for item in store}
    stamp = _now_iso(now)
    result = {
        "schema": SCHEMA,
        "receipt_id": row.get("receipt_id"),
        "code": row.get("code"),
        "created": 0,
        "armed": 0,
        "expired": 0,
        "duplicates": 0,
        "updated": 0,
        "errors": [],
        "plans": [],
    }
    changed = False
    for plan in incoming:
        plan_id = plan["plan_id"]
        expires = _parse_iso(plan.get("expires_at"))
        expired = expires <= _parse_iso(stamp)
        existing = by_id.get(plan_id)
        if existing is not None:
            same_receipt = str(existing.get("source", {}).get("receipt_id") or "") == str(
                row.get("receipt_id") or ""
            )
            if not same_receipt:
                result["errors"].append(
                    {
                        "plan_id": plan_id,
                        "code": "plan_id_conflict",
                        "message": "plan_id 已被其他回执占用，拒绝覆盖",
                        "existing_receipt_id": existing.get("source", {}).get("receipt_id"),
                    }
                )
                continue
            result["duplicates"] += 1
            if expired and existing.get("status") in ("planned", "armed"):
                existing["status"] = "expired"
                existing["updated_at"] = stamp
                changed = True
                result["updated"] += 1
                result["expired"] += 1
            elif not expired and existing.get("status") == "planned":
                existing["status"] = "armed"
                existing["armed_at"] = existing.get("armed_at") or stamp
                existing["updated_at"] = stamp
                changed = True
                result["updated"] += 1
            result["plans"].append(existing)
            continue
        record = build_plan_record(row, plan, now=now)
        record["status"] = "expired" if expired else "armed"
        record["armed_at"] = None if expired else record.get("armed_at")
        by_id[plan_id] = record
        store.append(record)
        result["plans"].append(record)
        result["created"] += 1
        changed = True
        if expired:
            result["expired"] += 1
        else:
            result["armed"] += 1
    if changed:
        _write_store(target, store)
    return result


def consume_ledger(ledger=None, path=None, now=None):
    """批量消费回执账本（只读回执）；逐条幂等消费。"""
    target = plan_ledger_path(path)
    rows = H.read_handoffs(ledger)
    summary = {
        "schema": SCHEMA,
        "receipts": len(rows),
        "created": 0,
        "armed": 0,
        "expired": 0,
        "duplicates": 0,
        "updated": 0,
        "errors": [],
        "consumed": [],
    }
    for row in rows:
        try:
            result = consume_handoff(row, path=target, now=now)
        except WatchPlanError as exc:
            summary["errors"].append(
                {
                    "receipt_id": row.get("receipt_id"),
                    "code": exc.code,
                    "message": str(exc)[:300],
                }
            )
            continue
        for key in ("created", "armed", "expired", "duplicates", "updated"):
            summary[key] += result[key]
        summary["errors"].extend(result["errors"])
        if result["created"] or result["updated"] or result["duplicates"]:
            summary["consumed"].append(
                {"receipt_id": result["receipt_id"], "plans": len(result["plans"])}
            )
    return summary


# ── 只读查询 / 过期维护 ──────────────────────────────────────────────
def list_plans(path=None, status=None):
    plans = _load_store(plan_ledger_path(path))
    if status:
        wanted = str(status).lower()
        plans = [item for item in plans if item.get("status") == wanted]
    return plans


def get_plan(plan_id, path=None):
    for item in _load_store(plan_ledger_path(path)):
        if str(item.get("plan_id")) == str(plan_id):
            return item
    return None


def expire_due(path=None, now=None):
    """把到期未触发的 planned/armed 置为 expired（幂等）；返回变更列表。"""
    target = plan_ledger_path(path)
    stamp = _now_iso(now)
    store = _load_store(target)
    changed = []
    for item in store:
        if item.get("status") not in ("planned", "armed"):
            continue
        try:
            expires = _parse_iso(item.get("expires_at"))
        except (WatchPlanError, ValueError):
            continue
        if expires <= _parse_iso(stamp):
            item["status"] = "expired"
            item["updated_at"] = stamp
            changed.append(item)
    if changed:
        _write_store(target, store)
    return changed


# ── 触发 → pending 确认（绝不执行）──────────────────────────────────
def _trigger_ids(plan, confirmation=None):
    """五 ID 链视图：decision → handoff → watch_plan → trigger → confirmation。"""
    plan = plan if isinstance(plan, dict) else {}
    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    conf = confirmation if isinstance(confirmation, dict) else {}
    return {
        "decision_id": source.get("decision_id") or None,
        "handoff_id": source.get("receipt_id") or None,
        "watch_plan_id": plan.get("plan_id") or None,
        "trigger_id": conf.get("trigger_id") or plan.get("trigger_id") or None,
        "confirmation_id": conf.get("confirmation_id") or plan.get("confirmation_id") or None,
    }


def _ledger_util(trigger_ledger=None):
    """trigger 账本模块与显式路径（未接线 → (None, None)，不产生写入）。"""
    try:
        from agent_platform import watch_trigger_ledger as ledger
    except ImportError:
        return None, None
    return ledger, ledger.ledger_path(trigger_ledger)


def trigger_plan(plan_id, path=None, data_dir=None, now=None, trigger_ledger=None):
    """watch_plan（必须 armed 且未过期）→ pending 人工确认；不执行/不下单。

    IFS F3c：显式 ``trigger_ledger``/env 配置后落 trigger 独立账本并把 trigger_id
    写回 confirmation；未配置时保持旧行为（不落 trigger 账本）。
    """
    target = plan_ledger_path(path)
    store = _load_store(target)
    plan = None
    for item in store:
        if str(item.get("plan_id")) == str(plan_id):
            plan = item
            break
    if plan is None:
        raise WatchPlanError("plan_not_found", "watch_plan 不存在: %s" % plan_id)
    ledger_mod, ledger_target = _ledger_util(trigger_ledger)
    if plan.get("status") == "triggered":
        confirmation = None
        try:
            from agent_platform import trading_bot_flow as flow

            for item in flow.list_confirmations(data_dir=data_dir):
                if item.get("confirmation_id") == plan.get("confirmation_id"):
                    confirmation = item
                    break
        except Exception:  # noqa: BLE001 - 只读取证失败不改变幂等结论
            confirmation = None
        trigger_id = plan.get("trigger_id")
        if ledger_mod is not None and ledger_target is not None:
            existing = ledger_mod.find_by_plan(plan_id, path=ledger_target)
            if existing is not None:
                trigger_id = trigger_id or existing.get("trigger_id")
                if not plan.get("trigger_id"):
                    plan["trigger_id"] = trigger_id
                    plan["updated_at"] = _now_iso(now)
                    _write_store(target, store)
                if confirmation is not None and trigger_id and not confirmation.get("trigger_id"):
                    try:
                        confirmation = ledger_mod.attach_to_confirmation(
                            confirmation, trigger_id, data_dir=data_dir
                        )
                    except Exception:  # noqa: BLE001 - 幂等分支只读补齐失败不影响结论
                        logging.getLogger(__name__).exception("swallowed exception at line 387")
                        pass
        return {
            "schema": SCHEMA,
            "plan": plan,
            "confirmation": confirmation
            or {"confirmation_id": plan.get("confirmation_id"), "status": "already_triggered"},
            "trigger_id": trigger_id,
            "ids": _trigger_ids(plan, confirmation),
            "idempotent": True,
            "executes": False,
        }
    if plan.get("status") not in TRIGGERABLE_STATUS:
        raise WatchPlanError(
            "plan_not_armed", "watch_plan %s 状态为 %r，只有 armed 可触发" % (plan_id, plan.get("status"))
        )
    stamp = _now_iso(now)
    try:
        expires = _parse_iso(plan.get("expires_at"))
    except (WatchPlanError, ValueError) as exc:
        raise WatchPlanError("bad_expires_at", "watch_plan %s 的 expires_at 非法" % plan_id) from exc
    if expires <= _parse_iso(stamp):
        plan["status"] = "expired"
        plan["updated_at"] = stamp
        _write_store(target, store)
        raise WatchPlanError("plan_expired", "watch_plan %s 已过期，不再触发" % plan_id)
    confirmation = adapter.trigger_watch_plan(
        plan, data_dir=data_dir, trigger_ledger=trigger_ledger, now=now
    )
    plan["status"] = "triggered"
    plan["confirmation_id"] = confirmation.get("confirmation_id")
    plan["trigger_id"] = confirmation.get("trigger_id")
    plan["triggered_at"] = stamp
    plan["updated_at"] = stamp
    _write_store(target, store)
    return {
        "schema": SCHEMA,
        "plan": plan,
        "confirmation": confirmation,
        "trigger_id": plan.get("trigger_id"),
        "ids": _trigger_ids(plan, confirmation),
        "idempotent": False,
        "executes": False,
    }


# ── CLI（只读查询 / 显式消费 / 显式触发）────────────────────────────
USAGE = """用法:
    python -m agent_platform.watch_plan_consumer --arm --ledger <d1_handoffs.jsonl> --plans <watch_plans.jsonl> [--json]
    python -m agent_platform.watch_plan_consumer --list [--status armed] --plans <plans.jsonl> [--json]
    python -m agent_platform.watch_plan_consumer --trigger <plan_id> --plans <plans.jsonl> --bot-dir <trading_bot_dir> [--trigger-ledger <watch_triggers.jsonl>] [--json]

默认路径：回执 agent_platform/data/d1_handoffs.jsonl、plan 账本 agent_platform/data/watch_plans.jsonl。
trigger 独立账本仅在 --trigger-ledger 或 env WATCH_TRIGGER_LEDGER_PATH 显式配置时写入。
只登记/只建 pending 确认，不执行；退出码 0=ok / 1=业务失败 / 2=用法错误。"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="watch_plan 消费器（D1 handoff v2 → armed plan → pending 确认；只读/paper-only）",
        usage=USAGE,
    )
    parser.add_argument("--arm", action="store_true", help="消费回执账本（幂等）")
    parser.add_argument("--list", action="store_true", dest="list_plans_flag", help="只读列 plan")
    parser.add_argument("--trigger", default="", metavar="PLAN_ID", help="触发指定 armed plan（建 pending 确认）")
    parser.add_argument("--ledger", default="", help="回执账本路径（默认 agent_platform/data/d1_handoffs.jsonl）")
    parser.add_argument("--plans", default="", help="plan 账本路径（默认 agent_platform/data/watch_plans.jsonl）")
    parser.add_argument("--status", default="", help="--list 过滤状态")
    parser.add_argument("--bot-dir", default="", help="trading_bot 数据目录（确认落盘位置，测试用临时目录）")
    parser.add_argument("--trigger-ledger", default="",
                        help="trigger 独立账本路径（显式 path；缺省读 WATCH_TRIGGER_LEDGER_PATH，不配置则不落账）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    actions = int(bool(args.arm)) + int(bool(args.list_plans_flag)) + int(bool(args.trigger))
    if actions != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if args.arm:
            payload = consume_ledger(ledger=args.ledger or None, path=args.plans or None)
        elif args.list_plans_flag:
            plans = list_plans(path=args.plans or None, status=args.status or None)
            payload = {"schema": SCHEMA, "plans": plans, "count": len(plans), "readonly": True}
        else:
            payload = trigger_plan(
                args.trigger, path=args.plans or None, data_dir=args.bot_dir or None,
                trigger_ledger=args.trigger_ledger or None,
            )
    except WatchPlanError as exc:
        payload = {"ok": False, "error": exc.code, "message": str(exc)[:300]}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "[watch_plan] ERROR: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - trigger/ledger 等业务错误统一 JSON 化（不吞栈外信息）
        payload = {"ok": False, "error": getattr(exc, "code", "trigger_failed"),
                   "message": str(exc)[:300]}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "[watch_plan] ERROR: %s" % exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("[watch_plan] %s" % json.dumps(payload, ensure_ascii=False)[:800])
    return 0


__all__ = [
    "DEFAULT_PLAN_LEDGER",
    "PLAN_LEDGER_ENV",
    "PLAN_STATUS",
    "SCHEMA",
    "TRIGGER_LEDGER_ENV",
    "WatchPlanError",
    "build_plan_record",
    "consume_handoff",
    "consume_ledger",
    "expire_due",
    "get_plan",
    "list_plans",
    "main",
    "plan_ledger_path",
    "plans_from_handoff",
    "trigger_plan",
]


if __name__ == "__main__":
    raise SystemExit(main())
