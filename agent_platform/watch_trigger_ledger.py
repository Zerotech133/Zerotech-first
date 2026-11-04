# -*- coding: utf-8 -*-
"""watch trigger 独立账本（IFS v1 F3c；WO-IFS-TRIGGER-LEDGER-20261104）。

补齐 IFS F3 的 ``trigger_id`` 缺口：``watch_plan → trigger → confirmation`` 独立落账，
把「盯盘触发」变成可审计的一等实体，而不是直接由 adapter 建确认后无痕迹。

ID 契约（与 docs/standards/2026-11-03_ID_CHAIN_v1.md §3.7 对齐）
----------------------------------------------------------------
``trigger_id = trg-<yyyymmdd>-<hex6>``
- 日期取触发时刻（本地时区，``YYYYMMDD``）；
- ``hex6`` = SHA256(``watch_plan_id|quote_snapshot``) 前 6 位小写十六进制；
- 同 ``watch_plan_id`` + 同 ``quote_snapshot`` + 同一天 → **确定性同 ID**（可复现）；
- 账本层再按 ``watch_plan_id`` 幂等：重复触发返回既有事件，不重复落行。

账本（trigger_event/v1，JSONL）
-------------------------------
每行一条::

    {
      "schema": "trigger_event/v1", "schema_version": 1,
      "trigger_id": "trg-20261104-0f1e2d",
      "watch_plan_id": "wp-...", "handoff_id": "...", "decision_id": "...",
      "confirmation_id": "CFM-...",
      "quote_snapshot": {...} | null,
      "created_at": "2026-11-04T...+08:00",
      "paper_only": true, "not_a_trade": true
    }

五 ID 链：``decision_id → handoff_id → watch_plan_id → trigger_id → confirmation_id``
（缺失上游显式 ``null``，不拿 report_id/run_id 冒充）。

只读/写边界
-----------
- **无生产默认写入**：``ledger_path()`` 只认显式 ``path`` 或环境变量
  ``WATCH_TRIGGER_LEDGER_PATH``；两者皆无 → ``TriggerLedgerError``（不落盘）；
- 唯一写操作 = 向显式账本 append 一行 / 把 ``trigger_id`` 写回 confirmation JSON；
  不执行、不下单、不写 paper 账；
- 测试全程 tmp 路径，不创建真实 ``agent_platform/data/watch_triggers.jsonl``。

接线（Lead）
------------
``watch_trigger_adapter.trigger_watch_plan`` / ``watch_plan_consumer.trigger_plan``
在显式传入 ``trigger_ledger=`` 或设置 env 后自动落账并把 ``trigger_id`` 写进
confirmation；未接线时保持旧行为（只建 pending 确认，不落 trigger 账本）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "agent_platform" / "data" / "watch_triggers.jsonl"
LEDGER_ENV = "WATCH_TRIGGER_LEDGER_PATH"

SCHEMA = "trigger_event/v1"
SCHEMA_VERSION = 1
TRIGGER_ID_RE = re.compile(r"^trg-\d{8}-[0-9a-f]{6}$")
EXECUTION_FIELDS = (
    "order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account",
)


class TriggerLedgerError(ValueError):
    """trigger 账本不可继续（携带机器可读 code）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── 基础工具 ─────────────────────────────────────────────────────────
def _now_iso(now: Any = None) -> str:
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


def _canonical(value: Any) -> str:
    """稳定 JSON 串（用于确定性 ID；不可序列化 → 空串）。"""
    if value in (None, "", [], {}):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def ledger_path(path: Optional[str] = None) -> Optional[Path]:
    """账本路径：显式 path > WATCH_TRIGGER_LEDGER_PATH > None（无生产默认写入）。"""
    if path:
        return Path(path)
    env = str(os.environ.get(LEDGER_ENV) or "").strip()
    return Path(env) if env else None


def require_ledger_path(path: Optional[str] = None) -> Path:
    target = ledger_path(path)
    if target is None:
        raise TriggerLedgerError(
            "trigger_ledger_path_required",
            "trigger 账本需显式 path 或环境变量 %s（本模块不提供生产默认写入）" % LEDGER_ENV,
        )
    return target


def new_trigger_id(watch_plan_id: str, quote_snapshot: Any = None,
                   now: Any = None) -> str:
    """确定性 trigger_id：trg-<yyyymmdd>-<sha256(plan|snapshot)[:6]>。"""
    plan_id = str(watch_plan_id or "").strip()
    if not plan_id:
        raise TriggerLedgerError("watch_plan_id_required", "watch_plan_id 必填")
    stamp = _now_iso(now)
    day = stamp[0:4] + stamp[5:7] + stamp[8:10]
    digest = hashlib.sha256(
        ("%s|%s" % (plan_id, _canonical(quote_snapshot))).encode("utf-8")
    ).hexdigest()[:6]
    return "trg-%s-%s" % (day, digest)


def _require_id(value: Any, code: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TriggerLedgerError(code, "%s 必填" % field)
    return text


# ── 账本读写 ─────────────────────────────────────────────────────────
def load_events(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取账本（只读）；坏行 fail-closed，不静默丢账。"""
    target = ledger_path(path)
    if target is None or not target.is_file():
        return []
    events: List[Dict[str, Any]] = []
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise TriggerLedgerError("trigger_ledger_unreadable", "读取 trigger 账本失败: %s" % exc) from exc
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise TriggerLedgerError(
                "trigger_ledger_corrupt", "trigger 账本第 %d 行不是合法 JSON" % line_no
            ) from exc
        if not isinstance(event, dict) or not event.get("trigger_id"):
            raise TriggerLedgerError(
                "trigger_ledger_corrupt", "trigger 账本第 %d 行缺 trigger_id" % line_no
            )
        events.append(event)
    return events


def _append_event(event: Mapping[str, Any], path: Optional[str] = None) -> Path:
    target = require_ledger_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with io.open(target, "a", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return target


def find_by_plan(watch_plan_id: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """按 watch_plan_id 查既有 trigger 事件（取最后一条，与 append-only 一致）。"""
    plan_id = str(watch_plan_id or "").strip()
    if not plan_id:
        return None
    hit = None
    for event in load_events(path):
        if str(event.get("watch_plan_id")) == plan_id:
            hit = event
    return hit


def find_by_confirmation(confirmation_id: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    confirmation_id = str(confirmation_id or "").strip()
    if not confirmation_id:
        return None
    hit = None
    for event in load_events(path):
        if str(event.get("confirmation_id")) == confirmation_id:
            hit = event
    return hit


# ── 事件构造/校验 ────────────────────────────────────────────────────
def build_event(
    *,
    watch_plan_id: str,
    confirmation_id: str,
    handoff_id: Any = None,
    decision_id: Any = None,
    trigger_id: Optional[str] = None,
    quote_snapshot: Any = None,
    now: Any = None,
) -> Dict[str, Any]:
    """构造一条 trigger_event/v1（不写盘）。上游缺失显式 null，不造数。"""
    plan_id = _require_id(watch_plan_id, "watch_plan_id_required", "watch_plan_id")
    conf_id = _require_id(confirmation_id, "confirmation_id_required", "confirmation_id")
    event = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "trigger_id": trigger_id or new_trigger_id(plan_id, quote_snapshot, now=now),
        "watch_plan_id": plan_id,
        "handoff_id": str(handoff_id).strip() or None if handoff_id is not None else None,
        "decision_id": str(decision_id).strip() or None if decision_id is not None else None,
        "confirmation_id": conf_id,
        "quote_snapshot": quote_snapshot if isinstance(quote_snapshot, (dict, list)) else None,
        "created_at": _now_iso(now),
        "paper_only": True,
        "not_a_trade": True,
    }
    for key in EXECUTION_FIELDS:
        if key in event and event.get(key) not in (None, "", [], {}):
            raise TriggerLedgerError(
                "execution_field_forbidden", "trigger 事件不得携带执行字段 %r" % key
            )
    return event


def validate_event(event: Mapping[str, Any]) -> List[str]:
    """校验 trigger_event/v1，返回错误列表（空=合法）。"""
    errors: List[str] = []
    if not isinstance(event, Mapping):
        return ["trigger event 必须是 object"]
    if event.get("schema") != SCHEMA:
        errors.append("schema 必须是 %s" % SCHEMA)
    trigger_id = str(event.get("trigger_id") or "")
    if not TRIGGER_ID_RE.match(trigger_id):
        errors.append("trigger_id 非法（需 trg-YYYYMMDD-6hex）: %r" % trigger_id)
    for field in ("watch_plan_id", "confirmation_id"):
        if not str(event.get(field) or "").strip():
            errors.append("%s 必填" % field)
    for field in EXECUTION_FIELDS:
        if field in event and event.get(field) not in (None, "", [], {}):
            errors.append("不得携带执行字段 %r" % field)
    if event.get("paper_only") is not True:
        errors.append("paper_only 必须为 true")
    return errors


def chain(event: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """五 ID 链视图（缺失显式 null，不发明）。"""
    event = event if isinstance(event, Mapping) else {}
    return {
        "decision_id": event.get("decision_id") or None,
        "handoff_id": event.get("handoff_id") or None,
        "watch_plan_id": event.get("watch_plan_id") or None,
        "trigger_id": event.get("trigger_id") or None,
        "confirmation_id": event.get("confirmation_id") or None,
    }


# ── 落账（幂等）+ confirmation 回写 ─────────────────────────────────
def record_trigger(
    *,
    watch_plan_id: str,
    confirmation_id: str,
    handoff_id: Any = None,
    decision_id: Any = None,
    quote_snapshot: Any = None,
    trigger_id: Optional[str] = None,
    path: Optional[str] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """触发落账（一次 trigger 一行；同 plan 幂等返回既有事件）。

    返回 ``{"event": {...}, "trigger_id": ..., "idempotent": bool}``；
    无显式 path/env 时抛 ``TriggerLedgerError(trigger_ledger_path_required)``。
    """
    target = require_ledger_path(path)
    plan_id = _require_id(watch_plan_id, "watch_plan_id_required", "watch_plan_id")
    conf_id = _require_id(confirmation_id, "confirmation_id_required", "confirmation_id")
    existing = find_by_plan(plan_id, path=target)
    if existing is not None:
        if str(existing.get("confirmation_id")) != conf_id:
            raise TriggerLedgerError(
                "trigger_confirmation_conflict",
                "watch_plan %s 已绑定 confirmation %s（拒绝改为 %s）"
                % (plan_id, existing.get("confirmation_id"), conf_id),
            )
        return {"event": existing, "trigger_id": existing["trigger_id"], "idempotent": True}
    event = build_event(
        watch_plan_id=plan_id,
        confirmation_id=conf_id,
        handoff_id=handoff_id,
        decision_id=decision_id,
        trigger_id=trigger_id,
        quote_snapshot=quote_snapshot,
        now=now,
    )
    _append_event(event, path=target)
    return {"event": event, "trigger_id": event["trigger_id"], "idempotent": False}


def confirmation_path(confirmation_id: str, data_dir: Optional[str] = None) -> Path:
    """confirmation JSON 路径（与 trading_bot_flow 口径一致：data_dir > TRADING_BOT_DIR > 默认）。"""
    from agent_platform import trading_bot_flow as flow

    root = data_dir or os.environ.get("TRADING_BOT_DIR") or flow.DATA_DIR
    return Path(root) / "confirmations" / (str(confirmation_id) + ".json")


def attach_to_confirmation(
    confirmation: Mapping[str, Any],
    trigger_id: str,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """把 trigger_id 写回 confirmation JSON（保留既有四 ID/字段；不执行/不改 paper 账）。

    trading_bot_flow 的 v2 confirmation 是开放 dict（读入后原样回写），追加
    ``trigger_id`` 属兼容扩展；本函数只做一次原子读改写，测试/生产均落 data_dir。
    """
    try:
        confirmation = dict(confirmation)
    except (TypeError, ValueError) as exc:
        raise TriggerLedgerError("confirmation_required", "confirmation 必须是 object") from exc
    conf_id = _require_id(
        confirmation.get("confirmation_id"), "confirmation_id_required", "confirmation_id"
    )
    trigger_id = _require_id(trigger_id, "trigger_id_required", "trigger_id")
    if not TRIGGER_ID_RE.match(trigger_id):
        raise TriggerLedgerError("bad_trigger_id", "trigger_id 非法: %r" % trigger_id)
    target = confirmation_path(conf_id, data_dir)
    if not target.is_file():
        raise TriggerLedgerError(
            "confirmation_not_found", "confirmation 文件不存在: %s" % target
        )
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TriggerLedgerError(
            "confirmation_read_failed", "读取 confirmation 失败: %s" % exc
        ) from exc
    if not isinstance(record, dict):
        raise TriggerLedgerError("confirmation_read_failed", "confirmation JSON 必须为 object")
    if str(record.get("confirmation_id")) != conf_id:
        raise TriggerLedgerError("confirmation_mismatch", "confirmation_id 不一致")
    record["trigger_id"] = trigger_id  # 追加字段；既有 source_* 四 ID 原样保留
    tmp = str(target) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, str(target))
    confirmation["trigger_id"] = trigger_id
    return confirmation


# ── CLI（只读查看 / 显式落账自检）────────────────────────────────────
USAGE = """用法（只读优先；写操作必须显式 --path 或 WATCH_TRIGGER_LEDGER_PATH）:
    python -m agent_platform.watch_trigger_ledger --list --path <watch_triggers.jsonl> [--json]
    python -m agent_platform.watch_trigger_ledger --find-plan <plan_id> --path <...> [--json]

默认不写生产：未给 --path/env 时只报错退出码 2。"""


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="watch trigger 账本（只读/显式路径）", usage=USAGE)
    parser.add_argument("--list", action="store_true", dest="list_flag", help="只读列账")
    parser.add_argument("--find-plan", default="", help="按 watch_plan_id 查 trigger")
    parser.add_argument("--path", default="", help="账本路径（缺省读 WATCH_TRIGGER_LEDGER_PATH）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.list_flag and not args.find_plan:
        print(USAGE, file=sys.stderr)
        return 2
    if ledger_path(args.path or None) is None:
        print("[watch_trigger_ledger] ERROR: 只读查看也需显式 --path 或环境变量 %s"
              "（本模块不隐式读/写生产默认账本）" % LEDGER_ENV, file=sys.stderr)
        return 2
    try:
        if args.find_plan:
            event = find_by_plan(args.find_plan, path=args.path or None)
            payload = {"ok": True, "event": event, "found": event is not None,
                       "chain": chain(event) if event else None, "readonly": True}
        else:
            events = load_events(args.path or None)
            payload = {"ok": True, "count": len(events), "events": events,
                       "chains": [chain(e) for e in events], "readonly": True}
    except TriggerLedgerError as exc:
        print("[watch_trigger_ledger] ERROR: %s" % exc, file=sys.stderr)
        return 2 if exc.code == "trigger_ledger_path_required" else 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("[watch_trigger_ledger] %s" % json.dumps(payload, ensure_ascii=False)[:800])
    return 0


__all__ = [
    "DEFAULT_LEDGER",
    "EXECUTION_FIELDS",
    "LEDGER_ENV",
    "SCHEMA",
    "SCHEMA_VERSION",
    "TRIGGER_ID_RE",
    "TriggerLedgerError",
    "attach_to_confirmation",
    "build_event",
    "chain",
    "confirmation_path",
    "find_by_confirmation",
    "find_by_plan",
    "ledger_path",
    "load_events",
    "main",
    "new_trigger_id",
    "record_trigger",
    "require_ledger_path",
    "validate_event",
]


if __name__ == "__main__":
    sys.exit(main())
