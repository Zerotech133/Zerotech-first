# -*- coding: utf-8 -*-
"""盯盘告警运行时账本（watch_alert/v1；WO-WATCH-ALERT-20261104）。

职责：把「盯盘触发/规则命中」固化为一条可审计告警事件，并做确定性去重与
可替换的分发回执占位；MVP **默认 dry-run、默认不发送**，不接 Feishu/真实通知。

契约（watch_alert/v1）
----------------------
事件字段（恰好 14 个，闭集）::

    {
      "schema": "watch_alert/v1",
      "alert_id": "<sha256 64hex>",
      "trace_id": "<sha256 64hex>",
      "dedupe_key": "<sha256 64hex>",
      "plan_id": "wp-...", "symbol": "600519.SH", "action": "alert",
      "observed": "12.34", "observed_bucket": "12.34",
      "threshold": "12.00",
      "quote_as_of": "2026-11-04T05:30:00Z",
      "created_at": "2026-11-04T05:31:00Z",
      "paper_only": true, "not_a_trade": true
    }

- ``observed``/``threshold``：十进制字符串（``Decimal`` 规范化；禁 NaN/Inf）；事件闭集，
  额外字段/执行字段（order/qty/quantity/broker/execute/trade_id/fill/account）一律拒收；
- ``quote_as_of``/``created_at``：UTC ISO8601（``...Z``）；``created_at`` 缺省=当前 UTC；
- ``paper_only`` / ``not_a_trade``：必须 ``true``（MVP 只读研究层；false → 拒收）。

ID 契约（确定性、可复现）
------------------------
- ``dedupe_key = sha256("plan_id|symbol|action|observed_bucket")``（64hex 小写）；
- ``trace_id   = sha256("watch_alert_trace/v1|" + dedupe_key)``；
- ``alert_id   = sha256(canonical({dedupe_key, observed, threshold, quote_as_of}))``；
- ``observed_bucket`` 缺省 = ``observed`` 按 ``bucket_decimals``（默认 2）ROUND_HALF_UP
  量化后的十进制字符串；也可显式传入更粗的 bucket 做「同档去重」。
去重规则：**账本内同 dedupe_key 只写一行**（append-only，不覆盖）；重复命中返回
``status="duplicate"`` 且 ``dispatch.skipped``，不再发送。

写边界（默认零写）
------------------
- 只有显式 ledger path（参数 ``ledger_path=`` 或环境变量 ``WATCH_ALERT_LEDGER_PATH``）
  才 append JSONL；无 path 或 ``dry_run=True`` → 只返回事件/回执，不落盘（默认 dry-run）；
- 坏账本 fail-closed（不静默跳过、不覆盖）：抛 ``WatchAlertError("ledger_corrupt")``；
- 写失败 fail-visible（``ledger_write_failed``）。

分发（dispatch）
---------------
``dispatch(event, sink=None)``：``sink=None`` → ``{"status": "not_sent", ...}``（MVP 默认，
不接任何通道）；测试可注入 sink 观察调用；sink 异常 → ``{"status": "failed"}`` 回执。
本模块不 import requests/urllib/httpx/feishu，不打印/保存任何 token，不写生产数据。

接线（Lead/后续单）
-------------------
- 触发源（``watch_trigger_adapter`` / ``watch_plan_consumer`` / 盯盘任务）在显式传入
  ``ledger_path=`` 或设置 env 后调用 ``emit_event()``；未接线时保持旧行为（不落告警账本、
  不发送）；
- Feishu/真实分发前置条件：L2 审批（凭据/目标白名单/限频/回执对账/去重 TTL/回滚演练），
  另单立项；本模块只交付 dry-run + not_sent 的运行时骨架。
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
# 仅作文档语义：本模块不提供生产默认写入（必须显式 path/env）
DEFAULT_LEDGER = ROOT / "agent_platform" / "data" / "watch_alerts.jsonl"
LEDGER_ENV = "WATCH_ALERT_LEDGER_PATH"

SCHEMA = "watch_alert/v1"
SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[0-9a-f]{64}$")
TEXT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
BUCKET_DECIMALS = 2
EXECUTION_FIELDS = (
    "order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account",
)
ALERT_FIELDS = frozenset(
    {
        "schema", "alert_id", "trace_id", "dedupe_key", "plan_id", "symbol",
        "action", "observed", "observed_bucket", "threshold", "quote_as_of",
        "created_at", "paper_only", "not_a_trade",
    }
)


class WatchAlertError(ValueError):
    """告警账本不可继续（携带机器可读 code；fail-visible）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── 基础工具 ─────────────────────────────────────────────────────────
def _canonical(value: Any) -> str:
    """稳定 JSON 串（确定性 ID 用；不可序列化 → str 兜底）。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return str(value)


def _utc_iso(value: Any, code: str, field: str) -> str:
    """解析时区感知 ISO8601 → 规范 UTC ``...Z``（无时区/坏值 fail-closed）。"""
    text = str(value or "").strip()
    if not text:
        raise WatchAlertError(code, "%s 必填（UTC ISO8601）" % field)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise WatchAlertError(code, "%s 非 ISO8601: %r" % (field, text)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WatchAlertError(code, "%s 必须带时区（UTC）: %r" % (field, text))
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _decimal_str(value: Any, code: str, field: str) -> str:
    """数值/十进制字符串 → 规范化十进制字符串（禁 NaN/Inf/float 科学计数）。"""
    if isinstance(value, bool) or value is None:
        raise WatchAlertError(code, "%s 必须是数值或十进制字符串" % field)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WatchAlertError(code, "%s 非有限数值" % field)
        text = repr(value)
    else:
        text = str(value).strip()
    if not DECIMAL_RE.match(text):
        raise WatchAlertError(code, "%s 非十进制字面量: %r" % (field, value))
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise WatchAlertError(code, "%s 非法十进制: %r" % (field, value)) from exc
    if not number.is_finite():
        raise WatchAlertError(code, "%s 非有限数值" % field)
    return _decimal_plain(number)


def _decimal_plain(number: Decimal) -> str:
    """Decimal → 无科学计数法的普通字符串（10.20 → "10.20"）。"""
    text = format(number, "f")
    if text in ("-0", "-0.0"):
        return "0"
    return text


def default_observed_bucket(observed: Any, decimals: int = BUCKET_DECIMALS) -> str:
    """observed → 默认去重档位（按 decimals 位小数 ROUND_HALF_UP 量化）。"""
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 8:
        raise WatchAlertError("bad_bucket_decimals", "bucket_decimals 必须是 0..8 的整数")
    observed_text = _decimal_str(observed, "invalid_observed", "observed")
    unit = Decimal(1).scaleb(-decimals)
    return _decimal_plain(Decimal(observed_text).quantize(unit, rounding=ROUND_HALF_UP))


def dedupe_key(plan_id: Any, symbol: Any, action: Any, observed_bucket: Any) -> str:
    """dedupe_key = sha256("plan_id|symbol|action|observed_bucket")（64hex）。"""
    parts = [str(part or "").strip() for part in (plan_id, symbol, action, observed_bucket)]
    if any(not part for part in parts):
        raise WatchAlertError(
            "dedupe_identity_required", "plan_id/symbol/action/observed_bucket 均必填")
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def new_ids(plan_id: Any, symbol: Any, action: Any, observed_bucket: Any,
            observed: Any, threshold: Any, quote_as_of: Any) -> Dict[str, str]:
    """确定性三 ID（dedupe_key / trace_id / alert_id；均 64hex 小写）。"""
    key = dedupe_key(plan_id, symbol, action, observed_bucket)
    return {
        "dedupe_key": key,
        "trace_id": hashlib.sha256(("watch_alert_trace/v1|" + key).encode("utf-8"))
        .hexdigest(),
        "alert_id": hashlib.sha256(
            _canonical(
                {
                    "dedupe_key": key,
                    "observed": str(observed),
                    "threshold": str(threshold),
                    "quote_as_of": str(quote_as_of),
                }
            ).encode("utf-8")
        ).hexdigest(),
    }


# ── 账本路径/读写 ────────────────────────────────────────────────────
def ledger_path(path: Optional[str] = None) -> Optional[Path]:
    """账本路径：显式 path > WATCH_ALERT_LEDGER_PATH > None（无生产默认写入）。"""
    if path:
        return Path(path)
    env = str(os.environ.get(LEDGER_ENV) or "").strip()
    return Path(env) if env else None


def require_ledger_path(path: Optional[str] = None) -> Path:
    target = ledger_path(path)
    if target is None:
        raise WatchAlertError(
            "ledger_path_required",
            "告警账本需显式 ledger path 或环境变量 %s（本模块不提供生产默认写入）"
            % LEDGER_ENV,
        )
    return target


def load_events(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取账本（只读）；坏行 fail-closed，不静默丢账/不覆盖。"""
    target = ledger_path(path)
    if target is None or not target.is_file():
        return []
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise WatchAlertError(
            "ledger_unreadable", "读取告警账本失败: %s" % exc) from exc
    events: List[Dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise WatchAlertError(
                "ledger_corrupt", "告警账本第 %d 行不是合法 JSON" % line_no) from exc
        if not isinstance(event, dict) or not event.get("dedupe_key"):
            raise WatchAlertError(
                "ledger_corrupt", "告警账本第 %d 行缺 dedupe_key" % line_no)
        events.append(event)
    return events


def _append_event(record: Mapping[str, Any], path: Optional[str] = None) -> Path:
    target = require_ledger_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with io.open(target, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise WatchAlertError(
            "ledger_write_failed", "写告警账本失败: %s" % exc) from exc
    return target


def find_event(key: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """按 dedupe_key 查既有告警（append-only：取最后一条）。"""
    wanted = str(key or "").strip()
    if not wanted:
        return None
    hit = None
    for event in load_events(path):
        if str(event.get("dedupe_key")) == wanted:
            hit = event
    return hit


# ── 事件构造/校验 ────────────────────────────────────────────────────
def build_event(
    *,
    plan_id: str,
    symbol: str,
    action: str,
    observed: Any,
    threshold: Any,
    quote_as_of: Any,
    created_at: Any = None,
    observed_bucket: Any = None,
    bucket_decimals: int = BUCKET_DECIMALS,
) -> Dict[str, Any]:
    """构造一条 watch_alert/v1（不写盘、不发送；缺省 created_at=当前 UTC）。"""
    plan_text = _require_text_id(plan_id, "plan_id")
    symbol_text = _require_text_id(symbol, "symbol")
    action_text = _require_action(action)
    observed_text = _decimal_str(observed, "invalid_observed", "observed")
    threshold_text = _decimal_str(threshold, "invalid_threshold", "threshold")
    quote_text = _utc_iso(quote_as_of, "invalid_quote_as_of", "quote_as_of")
    created_text = (_utc_iso(created_at, "invalid_created_at", "created_at")
                    if created_at is not None else _now_utc())
    if observed_bucket is None:
        bucket = default_observed_bucket(observed_text, decimals=bucket_decimals)
    else:
        bucket = str(observed_bucket).strip()
        if not bucket:
            raise WatchAlertError("invalid_observed_bucket", "observed_bucket 非空")
        # 显式 bucket 也规范化为十进制字符串，保证同档去重稳定
        bucket = _decimal_str(bucket, "invalid_observed_bucket", "observed_bucket")
    ids = new_ids(plan_text, symbol_text, action_text, bucket,
                  observed_text, threshold_text, quote_text)
    return {
        "schema": SCHEMA,
        "alert_id": ids["alert_id"],
        "trace_id": ids["trace_id"],
        "dedupe_key": ids["dedupe_key"],
        "plan_id": plan_text,
        "symbol": symbol_text,
        "action": action_text,
        "observed": observed_text,
        "observed_bucket": bucket,
        "threshold": threshold_text,
        "quote_as_of": quote_text,
        "created_at": created_text,
        "paper_only": True,
        "not_a_trade": True,
    }


def _require_text_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WatchAlertError("%s_required" % field, "%s 必填" % field)
    if not TEXT_ID_RE.match(text):
        raise WatchAlertError("bad_%s" % field, "%s 非法（[A-Za-z0-9._:-]{1,128}）: %r"
                              % (field, value))
    return text


def _require_action(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise WatchAlertError("action_required", "action 必填")
    if not ACTION_RE.match(text):
        raise WatchAlertError(
            "bad_action", "action 非法（[A-Za-z0-9._:-]{1,32}）: %r" % (value,))
    return text


def validate_event(event: Mapping[str, Any]) -> List[str]:
    """校验 watch_alert/v1，返回错误列表（空=合法；不做 I/O、不发送）。"""
    errors: List[str] = []
    if not isinstance(event, Mapping):
        return ["watch alert 必须是 object"]
    if set(event) != ALERT_FIELDS:
        missing = sorted(ALERT_FIELDS - set(event))
        extra = sorted(set(event) - ALERT_FIELDS)
        if missing:
            errors.append("缺字段: %s" % ",".join(missing))
        if extra:
            errors.append("多字段（闭集之外）: %s" % ",".join(extra))
    if event.get("schema") != SCHEMA:
        errors.append("schema 必须是 %s" % SCHEMA)
    for field in ("alert_id", "trace_id", "dedupe_key"):
        if not isinstance(event.get(field), str) or not ID_RE.match(event.get(field, "")):
            errors.append("%s 必须是 64hex 小写 sha256" % field)
    for field in ("plan_id", "symbol"):
        value = event.get(field)
        if not isinstance(value, str) or not TEXT_ID_RE.match(value):
            errors.append("%s 非法: %r" % (field, value))
    action = event.get("action")
    if not isinstance(action, str) or not ACTION_RE.match(action):
        errors.append("action 非法: %r" % (action,))
    for field in ("observed", "threshold", "observed_bucket"):
        value = event.get(field)
        if not isinstance(value, str) or not DECIMAL_RE.match(value):
            errors.append("%s 必须为十进制字符串: %r" % (field, value))
    for field in ("quote_as_of", "created_at"):
        try:
            _utc_iso(event.get(field), "invalid_" + field, field)
        except WatchAlertError as exc:
            errors.append(str(exc))
    if event.get("paper_only") is not True:
        errors.append("paper_only 必须为 true（MVP 只读研究层）")
    if event.get("not_a_trade") is not True:
        errors.append("not_a_trade 必须为 true（MVP 不交易）")
    for field in EXECUTION_FIELDS:
        if field in event:
            errors.append("不得携带执行字段 %r" % field)
    # ID 必须与字段自洽（防篡改/防手写错 ID）
    try:
        expected = new_ids(event.get("plan_id"), event.get("symbol"), event.get("action"),
                           event.get("observed_bucket"), event.get("observed"),
                           event.get("threshold"), event.get("quote_as_of"))
    except WatchAlertError:
        expected = None
    if expected is not None:
        for field, value in expected.items():
            if event.get(field) != value:
                errors.append("%s 与字段不自洽（应为 %s）" % (field, value))
    return errors


# ── 分发（MVP：默认 not_sent；可注入测试 sink；不接 Feishu）──────────
def dispatch(event: Mapping[str, Any], sink: Any = None) -> Dict[str, Any]:
    """分发一条告警；默认 sink=None → ``not_sent``（不接真实通道）。

    sink 是测试/后续通道注入点：``sink(event)`` 返回 mapping（含 status 更佳）或任意值；
    异常 → ``status="failed"`` 回执（不抛网络副作用）。本模块自身无网络能力。
    """
    if sink is None:
        return {
            "status": "not_sent",
            "attempted": False,
            "channel": None,
            "reason": "sink_not_configured（MVP 默认不接真实通道；dry-run 语义）",
        }
    try:
        outcome = sink(event)
    except Exception as exc:  # noqa: BLE001  通道失败要 fail-visible
        return {
            "status": "failed",
            "attempted": True,
            "channel": getattr(sink, "__name__", None),
            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
        }
    result = {
        "status": "sent",
        "attempted": True,
        "channel": getattr(sink, "__name__", None),
    }
    if isinstance(outcome, Mapping):
        result.update({str(key): outcome[key] for key in outcome})
        result["status"] = str(outcome.get("status") or "sent")
    return result


# ── 写账（去重）+ 分发回执 ──────────────────────────────────────────
def emit_event(
    event: Mapping[str, Any],
    *,
    ledger_path_arg: Optional[str] = None,
    dry_run: bool = False,
    sink: Any = None,
) -> Dict[str, Any]:
    """落账 + 分发回执（默认 dry-run / not_sent；同 dedupe_key 不重复写）。

    返回 ``{"ok", "status": dry_run|written|duplicate, "event", "ledger",
    "dispatch", "dedupe_key", "written", "duplicate"}``。
    """
    errors = validate_event(event)
    if errors:
        raise WatchAlertError("invalid_event", "；".join(errors))
    event = json.loads(json.dumps(dict(event), ensure_ascii=False))
    key = str(event["dedupe_key"])
    target = ledger_path(ledger_path_arg)
    if dry_run or target is None:
        return {
            "ok": True,
            "status": "dry_run",
            "event": event,
            "ledger": str(target) if target is not None else None,
            "dispatch": dispatch(event, sink),
            "dedupe_key": key,
            "written": False,
            "duplicate": False,
        }
    existing = find_event(key, path=str(target))
    if existing is not None:
        return {
            "ok": True,
            "status": "duplicate",
            "event": event,
            "ledger": str(target),
            "dispatch": {
                "status": "skipped",
                "attempted": False,
                "channel": None,
                "reason": "duplicate dedupe_key（账本已有同 key 事件，不重复写/不重发）",
                "existing_alert_id": existing.get("alert_id"),
            },
            "dedupe_key": key,
            "written": False,
            "duplicate": True,
        }
    receipt = dispatch(event, sink)
    record = dict(event)
    record["dispatch"] = receipt
    _append_event(record, path=str(target))
    return {
        "ok": True,
        "status": "written",
        "event": event,
        "ledger": str(target),
        "dispatch": receipt,
        "dedupe_key": key,
        "written": True,
        "duplicate": False,
    }


# ── CLI ─────────────────────────────────────────────────────────────
USAGE = """用法（默认 dry-run，零写；写入必须显式 --ledger 或 WATCH_ALERT_LEDGER_PATH）:
    python -m agent_platform.watch_alerts --event-json '{"plan_id":...}' [--ledger PATH] [--dry-run] [--json]
    python -m agent_platform.watch_alerts --event-file event.json --ledger PATH [--json]
    python -m agent_platform.watch_alerts --list --ledger PATH [--json]   # 只读列账

退出码: 0=成功（dry_run/written/duplicate/list）；2=输入/配置错误；1=账本读写错误。
本 CLI 不注入任何 dispatch sink：结果恒为 dispatch.status="not_sent"（不接 Feishu）."""


def _event_from_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise WatchAlertError("invalid_payload", "event-json 必须是 object")
    unknown = sorted(set(payload) - {
        "plan_id", "symbol", "action", "observed", "threshold", "quote_as_of",
        "created_at", "observed_bucket", "bucket_decimals",
    })
    if unknown:
        raise WatchAlertError(
            "invalid_payload", "event-json 含不支持的输入字段: %s" % ",".join(unknown))
    for required_field in ("plan_id", "symbol", "action", "observed",
                           "threshold", "quote_as_of"):
        if required_field not in payload:
            raise WatchAlertError(
                "invalid_payload", "event-json 缺字段: %s" % required_field)
    return build_event(
        plan_id=payload["plan_id"],
        symbol=payload["symbol"],
        action=payload["action"],
        observed=payload["observed"],
        threshold=payload["threshold"],
        quote_as_of=payload["quote_as_of"],
        created_at=payload.get("created_at"),
        observed_bucket=payload.get("observed_bucket"),
        bucket_decimals=payload.get("bucket_decimals", BUCKET_DECIMALS),
    )


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="盯盘告警账本（watch_alert/v1；默认 dry-run/not_sent）",
        usage=USAGE)
    parser.add_argument("--event-json", default="", help="内联事件 JSON")
    parser.add_argument("--event-file", default="", help="事件 JSON 文件路径")
    parser.add_argument("--ledger", default="", help="账本 JSONL 路径（或 env %s）"
                                                    % LEDGER_ENV)
    parser.add_argument("--dry-run", action="store_true",
                        help="强制 dry-run（即使给了 --ledger 也不写）")
    parser.add_argument("--list", action="store_true", dest="list_flag",
                        help="只读列账（需显式 --ledger/env）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list_flag:
        target = ledger_path(args.ledger or None)
        if target is None:
            print("[watch_alerts] ERROR: --list 需显式 --ledger 或环境变量 %s"
                  "（不隐式读生产默认账本）" % LEDGER_ENV, file=sys.stderr)
            return 2
        try:
            events = load_events(str(target))
        except WatchAlertError as exc:
            print("[watch_alerts] ERROR: %s" % exc, file=sys.stderr)
            return 1
        payload = {"ok": True, "count": len(events), "events": events,
                   "ledger": str(target), "readonly": True}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json
              else "[watch_alerts] count=%d ledger=%s" % (len(events), target))
        return 0

    if bool(args.event_json) == bool(args.event_file):
        print("[watch_alerts] ERROR: 需要且仅需要 --event-json 或 --event-file 之一",
              file=sys.stderr)
        return 2
    try:
        if args.event_json:
            payload = json.loads(args.event_json)
        else:
            payload = json.loads(Path(args.event_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("[watch_alerts] ERROR: 事件 JSON 不可解析: %s" % exc, file=sys.stderr)
        return 2
    try:
        event = _event_from_payload(payload)
        result = emit_event(event, ledger_path_arg=args.ledger or None,
                            dry_run=args.dry_run)
    except WatchAlertError as exc:
        print("[watch_alerts] ERROR: %s" % exc, file=sys.stderr)
        return 2 if exc.code in {
            "invalid_event", "invalid_payload", "plan_id_required", "symbol_required",
            "action_required", "bad_plan_id", "bad_symbol", "bad_action",
            "invalid_observed", "invalid_threshold", "invalid_observed_bucket",
            "invalid_quote_as_of", "invalid_created_at", "bad_bucket_decimals",
            "ledger_path_required",
        } else 1
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json
          else "[watch_alerts] status=%s dedupe_key=%s ledger=%s dispatch=%s"
               % (result["status"], result["dedupe_key"], result["ledger"],
                  (result["dispatch"] or {}).get("status")))
    return 0


__all__ = [
    "ALERT_FIELDS",
    "BUCKET_DECIMALS",
    "DEFAULT_LEDGER",
    "EXECUTION_FIELDS",
    "LEDGER_ENV",
    "SCHEMA",
    "SCHEMA_VERSION",
    "WatchAlertError",
    "build_event",
    "default_observed_bucket",
    "dedupe_key",
    "dispatch",
    "emit_event",
    "find_event",
    "ledger_path",
    "load_events",
    "main",
    "new_ids",
    "require_ledger_path",
    "validate_event",
]


if __name__ == "__main__":
    sys.exit(main())
