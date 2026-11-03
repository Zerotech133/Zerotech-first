# -*- coding: utf-8 -*-
"""盯盘条件评估引擎 MVP（IFS F3 / 盯盘完整系统；WO-WATCH-ENGINE-20261104）。

背景：D1 已有 ``watch_plan`` 状态机（``watch_plan_consumer``）、trigger adapter 与
trigger ledger，但**没有条件评估循环**——``trigger_plan`` 只把 plan 里的 price 当触发价
直接建确认。本模块补齐引擎层：读 armed watch_plan → 注入 quote → 按条件判断 → 命中后
调用 ``watch_plan_consumer.trigger_plan`` 建 **pending 人工确认**（不执行、不下单）。

只读/paper-only 护栏
--------------------
- 只读 plan ledger（``consumer.list_plans(status="armed")``），不直接改写；
- 唯一写动作 = 命中后调 ``consumer.trigger_plan(...)``（写 plan 状态 + trigger ledger +
  confirmation 文件，均为既有 F3 链路、pending 人工门，**绝不 decide/execute**）；
- **dry-run 零写**：不调 trigger_plan、不写 status/trigger ledger/confirmation；
- 默认 quote provider **无网络**：未注入 provider → status ``quote_provider_required``，
  不猜价格、不建确认；provider 由调用方注入（callable/mapping），CLI 用 ``--quotes-json``；
- update 非目标文件：不写 D1 decisions、paper 账、registry；不启停服务。

条件语义（``plan["conditions"]`` object）
----------------------------------------
字段：
- ``symbol`` / ``code``：标的（必填，用于取 quote）；
- ``action`` / ``side``：BUY/SELL（legacy 默认算子用），可选；
- ``operator`` / ``op``：显式算子，支持 ``>= <= > < == cross_above cross_below``
  （以及 ``between/in_range/range`` 区间，需配 price_min/price_max 或 range 数组）；
- ``price`` / ``trigger_price``：单点阈值（缺 operator 时按 legacy action 语义取默认）；
- ``price_min`` / ``price_max``（或 ``low``/``high``、``range``/``price_range``）：
  区间/单边上下界；
- ``quote_field``：取 quote 的价格字段（默认依次 last/price/close/latest/current/now/trade_price）；
- ``prev_field``：cross 用的前值字段（默认 prev/prev_price/previous/previous_price/prev_close）；
- ``max_age_s``：quote 时效上限（需 quote 带 ts/timestamp/time/quote_time/as_of，否则报
  ``quote_stale``，不猜）。

语义规则（无法判定 → ``condition_ambiguous``，不猜）：
1. 显式 operator + price → 单点比较；
2. 无 operator + price：BUY（含 买入/多/long）默认 ``<=``（回踩买），SELL（含 卖出/空/short）
   默认 ``>=``；action 缺失/未知 → ``condition_ambiguous``；
3. price_min & price_max（或 range）→ 区间 [min, max] 命中；显式 operator 与区间冲突 →
   ``condition_ambiguous``；
4. 仅 price_min → 默认 ``>=``（可显式覆盖）；仅 price_max → 默认 ``<=``（可显式覆盖）；
5. 同时出现 price 与 price_min/price_max：若 operator 为区间语义，price 仅作为
   adapter 确认价（``conditions.price`` 仍是 adapter 必填）；否则 ``condition_ambiguous``；
6. cross_above / cross_below 需要 quote 同时带当前价与前值；缺前值 → ``condition_ambiguous``；
7. range/cross 条件若 plan 无 adapter 认可的 ``price``，命中后无法在**不改
   consumer/adapter** 的前提下建确认 → 该轮显式 ``trigger_unsupported``（不假绿、不写）。

status JSON（``watch_engine_status/v1``）
----------------------------------------
每轮返回并可选原子写 ``--status <path>``（dry-run 永不写）：
``schema/ok/run_id/scanned/armed/waiting/triggered/errors/last_run/paper_only/executes=false``
+ ``dry_run/hits/would_trigger/expired/results[]/status_written/...``。
``armed`` = 本轮扫描的 armed plan 数；``waiting`` = 条件已评估但未命中的 plan 数；
``errors`` = 条件非法/quote 缺失/trigger 失败等 fail-visible 条数（condition_ambiguous 计入 errors）。

CLI::

    python -X utf8 -m agent_platform.watch_engine --once \
        --plan-ledger <watch_plans.jsonl> --trigger-ledger <watch_triggers.jsonl> \
        --data-dir <trading_bot_dir> --quotes-json <quotes.json> [--dry-run] \
        [--status <status.json>] [--json]

``--quotes-json`` 格式：``{symbol: quote}`` 或 ``{"quotes": {symbol: quote}}``；
quote 形如 ``{"last": 1490.0, "prev": 1510.0, "ts": "2026-11-04T10:00:00+08:00"}``
或纯数字。退出码：0=ok / 1=引擎/条件失败（ok=false） / 2=用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import watch_plan_consumer as consumer  # noqa: E402

SCHEMA = "watch_engine_status/v1"
STATUS_ENV = "WATCH_ENGINE_STATUS_PATH"
TRIGGER_LEDGER_ENV = "WATCH_TRIGGER_LEDGER_PATH"

OPERATORS = {
    ">=", "<=", ">", "<", "==",
    "cross_above", "cross_below", "between", "in_range", "range",
}
OP_ALIASES = {
    "ge": ">=", "gte": ">=", "le": "<=", "lte": "<=",
    "gt": ">", "lt": "<", "eq": "==", "=": "==",
    "crossabove": "cross_above", "cross_below": "cross_below",
    "crossbelow": "cross_below", "between": "between",
    "in_range": "in_range", "inrange": "in_range", "range": "range",
}
RANGE_OPERATORS = {"between", "in_range", "range"}
QUOTE_FIELDS = ("last", "price", "close", "latest", "current", "now", "trade_price")
PREV_FIELDS = ("prev", "prev_price", "previous", "previous_price", "prev_close")
TS_FIELDS = ("ts", "timestamp", "time", "quote_time", "as_of")
EXECUTION_FIELDS = (
    "order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account",
)
BUY_ACTIONS = {"BUY", "买入", "多", "LONG", "B"}
SELL_ACTIONS = {"SELL", "卖出", "空", "SHORT", "S"}


class WatchEngineError(ValueError):
    """引擎业务错误（code 机器可读）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── 小工具 ───────────────────────────────────────────────────────────
def _now_iso(now=None) -> str:
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


def _to_float(value) -> float:
    if isinstance(value, bool):
        raise WatchEngineError("condition_invalid", "数值字段不接受 bool")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError as exc:
            raise WatchEngineError("condition_invalid", "非数值: %r" % value) from exc
    raise WatchEngineError("condition_invalid", "非数值: %r" % (value,))


def new_run_id(now=None) -> str:
    stamp = _now_iso(now)
    compact = stamp[0:4] + stamp[5:7] + stamp[8:10] + stamp[11:13] + stamp[14:16] + stamp[17:19]
    digest = hashlib.sha256((stamp + "|" + str(os.getpid())).encode("utf-8")).hexdigest()[:6]
    return "wr-%s-%s" % (compact, digest)


def _norm_action(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    upper = text.upper()
    if upper in BUY_ACTIONS or text in BUY_ACTIONS:
        return "BUY"
    if upper in SELL_ACTIONS or text in SELL_ACTIONS:
        return "SELL"
    return upper


def _norm_operator(raw) -> str:
    if raw is None:
        return ""
    text = str(raw).strip().lower()
    if not text:
        return ""
    if text in OP_ALIASES:
        return OP_ALIASES[text]
    if text in OPERATORS:
        return text
    raise WatchEngineError("condition_invalid", "不支持的 operator: %r" % raw)


def _extract_numeric(mapping, fields, field=None):
    if field:
        if isinstance(mapping, dict) and field in mapping:
            return _to_float(mapping[field])
        return None
    for key in fields:
        if isinstance(mapping, dict) and key in mapping and mapping[key] is not None:
            try:
                return _to_float(mapping[key])
            except WatchEngineError:
                continue
    return None


def _quote_current(quote, field=None) -> float:
    """取 quote 当前价：数字 / dict(QUOTE_FIELDS 或指定 field) / 嵌套 quote/data。"""
    if isinstance(quote, dict):
        for nested_key in ("quote", "data", "ticker"):
            nested = quote.get(nested_key)
            if isinstance(nested, (dict, int, float)) and not isinstance(nested, bool):
                try:
                    return _quote_current(nested, field)
                except WatchEngineError:
                    pass
    value = _extract_numeric(quote, QUOTE_FIELDS, field)
    if value is None:
        raise WatchEngineError(
            "quote_invalid",
            "quote 缺可用价格字段（期望 %s 或 quote_field）" % (field or "/".join(QUOTE_FIELDS)),
        )
    return value


def _quote_prev(quote, field=None) -> float:
    if isinstance(quote, dict):
        for nested_key in ("quote", "data", "ticker"):
            nested = quote.get(nested_key)
            if isinstance(nested, dict):
                try:
                    return _quote_prev(nested, field)
                except WatchEngineError:
                    pass
    value = _extract_numeric(quote, PREV_FIELDS, field)
    if value is None:
        raise WatchEngineError(
            "condition_ambiguous",
            "cross 条件缺前值（期望 quote 带 %s 或 prev_field）" % (field or "/".join(PREV_FIELDS)),
        )
    return value


def _quote_ts(quote):
    if not isinstance(quote, dict):
        return None
    raw = None
    for key in TS_FIELDS:
        if quote.get(key) not in (None, ""):
            raw = quote[key]
            break
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        seconds = float(raw) / 1000.0 if float(raw) > 1e12 else float(raw)
        return datetime.fromtimestamp(seconds).astimezone()
    text = str(raw).strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


# ── 条件解析 ─────────────────────────────────────────────────────────
def parse_condition(plan) -> dict:
    """plan → 归一条件 spec；无法判定/非法 → WatchEngineError(condition_ambiguous|invalid)。"""
    if not isinstance(plan, dict):
        raise WatchEngineError("condition_invalid", "plan 必须是 object")
    conditions = plan.get("conditions")
    if not isinstance(conditions, dict):
        raise WatchEngineError(
            "condition_invalid", "conditions 必须是 object（list 暂不支持，adapter 仅接受 object）")
    symbol = str(conditions.get("symbol") or conditions.get("code") or "").strip()
    if not symbol:
        raise WatchEngineError("condition_ambiguous", "conditions 缺 symbol/code，无法取 quote")
    action = _norm_action(conditions.get("action") or conditions.get("side"))
    operator = _norm_operator(conditions.get("operator") or conditions.get("op"))
    quote_field = str(conditions.get("quote_field") or "").strip() or None
    prev_field = str(conditions.get("prev_field") or "").strip() or None
    max_age_s = conditions.get("max_age_s")
    if max_age_s is not None:
        try:
            max_age_s = float(max_age_s)
        except (TypeError, ValueError) as exc:
            raise WatchEngineError("condition_invalid", "max_age_s 必须为数值") from exc
        if max_age_s < 0:
            raise WatchEngineError("condition_invalid", "max_age_s 不能为负")

    raw_price = conditions.get("price")
    if raw_price is None:
        raw_price = conditions.get("trigger_price")
    price = None if raw_price is None else _to_float(raw_price)

    raw_min = conditions.get("price_min")
    if raw_min is None:
        raw_min = conditions.get("min_price")
    if raw_min is None:
        raw_min = conditions.get("low")
    raw_max = conditions.get("price_max")
    if raw_max is None:
        raw_max = conditions.get("max_price")
    if raw_max is None:
        raw_max = conditions.get("high")
    raw_range = conditions.get("range")
    if raw_range is None:
        raw_range = conditions.get("price_range")
    if raw_range is not None:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            raise WatchEngineError("condition_invalid", "range/price_range 必须是 [low, high]")
        if raw_min is not None or raw_max is not None:
            raise WatchEngineError("condition_ambiguous", "range 与 price_min/price_max 不能同时给")
        raw_min, raw_max = raw_range[0], raw_range[1]
    price_min = None if raw_min is None else _to_float(raw_min)
    price_max = None if raw_max is None else _to_float(raw_max)

    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    spec = {
        "symbol": symbol,
        "action": action,
        "operator": operator,
        "price": price,
        "price_min": price_min,
        "price_max": price_max,
        "quote_field": quote_field,
        "prev_field": prev_field,
        "max_age_s": max_age_s,
        "adapter_price": price,
        "plan_id": str(plan.get("plan_id") or ""),
        "seat": plan.get("seat"),
        "method": plan.get("method"),
        "code": source.get("code"),
    }
    has_range = price_min is not None and price_max is not None
    if has_range:
        if price_min > price_max:
            raise WatchEngineError("condition_invalid", "price_min 大于 price_max")
        if operator and operator not in RANGE_OPERATORS:
            raise WatchEngineError(
                "condition_ambiguous",
                "同时给 price_min/price_max 与比较算子 %r，无法判定区间/e单边口径" % operator)
        if price is not None and operator not in RANGE_OPERATORS:
            raise WatchEngineError(
                "condition_ambiguous",
                "price 与 price_min/price_max 同时给且 operator 非区间语义，无法判定阈值")
        spec["operator"] = operator or "between"
        spec["mode"] = "range"
        return spec
    if price_min is not None or price_max is not None:
        if price is not None:
            raise WatchEngineError("condition_ambiguous", "price 与 price_min/price_max 不能同时给（单点/单边二选一）")
        if operator and operator in RANGE_OPERATORS:
            op = operator
            if price_min is not None:
                spec["operator"] = op
                spec["mode"] = "range"
                spec["price_min"] = price_min
                spec["price_max"] = None
                spec["range_single"] = "min"
                return spec
            spec["operator"] = op
            spec["mode"] = "range"
            spec["price_min"] = None
            spec["price_max"] = price_max
            spec["range_single"] = "max"
            return spec
        if price_min is not None:
            spec["operator"] = operator or ">="
            spec["range_single"] = "min"
        else:
            spec["operator"] = operator or "<="
            spec["range_single"] = "max"
        spec["mode"] = "threshold"
        return spec
    if price is None:
        raise WatchEngineError(
            "condition_ambiguous",
            "conditions 无 price/price_min/price_max，无法判定触发条件（不猜）")
    if not operator:
        if action == "BUY":
            operator = "<="
        elif action == "SELL":
            operator = ">="
        else:
            raise WatchEngineError(
                "condition_ambiguous",
                "无 operator 且 action=%r 无 legacy 默认（BUY→<=，SELL→>=），无法判定" % action)
    if operator in RANGE_OPERATORS:
        raise WatchEngineError(
            "condition_invalid", "区间算子 %r 需要 price_min/price_max 或 range 数组" % operator)
    spec["operator"] = operator
    spec["mode"] = "threshold"
    return spec


# ── 条件评估 ─────────────────────────────────────────────────────────
def evaluate_condition(spec, quote, now=None) -> dict:
    """spec + quote → {hit, operator, observed, prev, threshold/range, ...}；不写盘。"""
    if not isinstance(spec, dict):
        raise WatchEngineError("condition_invalid", "spec 必须是 object")
    stamp = _now_iso(now)
    observed = _quote_current(quote, spec.get("quote_field"))
    out = {
        "operator": spec.get("operator"),
        "mode": spec.get("mode"),
        "observed": observed,
        "quote_field": spec.get("quote_field"),
        "hit": False,
    }
    age = None
    max_age_s = spec.get("max_age_s")
    if max_age_s is not None:
        ts = _quote_ts(quote)
        if ts is None:
            raise WatchEngineError(
                "quote_stale", "max_age_s 已配置但 quote 缺时间戳，无法校验时效（不猜）")
        age = (datetime.fromisoformat(stamp) - ts).total_seconds()
        if age < 0:
            age = 0.0
        out["quote_age_s"] = round(age, 3)
        if age > float(max_age_s):
            raise WatchEngineError(
                "quote_stale", "quote 超过时效（age=%.1fs > max_age_s=%.1fs）" % (age, float(max_age_s)))
    operator = spec.get("operator")
    if operator in ("cross_above", "cross_below"):
        threshold = spec.get("price")
        if threshold is None:
            threshold = (spec.get("price_min")
                         if spec.get("range_single") == "min" else spec.get("price_max"))
        if threshold is None:
            raise WatchEngineError("condition_invalid", "cross 条件缺阈值 price")
        prev = _quote_prev(quote, spec.get("prev_field"))
        out["prev"] = prev
        out["threshold"] = threshold
        if operator == "cross_above":
            out["hit"] = prev <= threshold and observed > threshold
        else:
            out["hit"] = prev >= threshold and observed < threshold
        out["reason"] = "%s: prev=%s threshold=%s observed=%s" % (
            operator, prev, threshold, observed)
        return out
    if operator in RANGE_OPERATORS:
        lo = spec.get("price_min")
        hi = spec.get("price_max")
        if lo is None and hi is None:
            raise WatchEngineError("condition_invalid", "区间条件缺上下界")
        if lo is not None:
            out["price_min"] = lo
        if hi is not None:
            out["price_max"] = hi
        out["hit"] = ((lo is None or observed >= lo) and (hi is None or observed <= hi))
        out["threshold"] = [lo, hi]
        out["reason"] = "%s: %s <= observed=%s <= %s" % (operator, lo, observed, hi)
        return out
    threshold = spec.get("price")
    if threshold is None:
        threshold = spec.get("price_min") if spec.get("range_single") == "min" else spec.get("price_max")
    if threshold is None:
        raise WatchEngineError("condition_invalid", "单点条件缺阈值")
    out["threshold"] = threshold
    if operator == ">=":
        out["hit"] = observed >= threshold
    elif operator == "<=":
        out["hit"] = observed <= threshold
    elif operator == ">":
        out["hit"] = observed > threshold
    elif operator == "<":
        out["hit"] = observed < threshold
    elif operator == "==":
        out["hit"] = observed == threshold
    else:
        raise WatchEngineError("condition_invalid", "不支持的 operator: %r" % operator)
    out["reason"] = "%s: observed=%s threshold=%s" % (operator, observed, threshold)
    return out


# ── quote provider（可注入；默认无网络）──────────────────────────────
def _resolve_provider(quote_provider, symbols):
    if quote_provider is None:
        raise WatchEngineError(
            "quote_provider_required",
            "未注入 quote provider（默认无网络、不猜价格）；请传 callable/mapping 或 --quotes-json")
    if isinstance(quote_provider, dict):
        return dict(quote_provider)
    if isinstance(quote_provider, Mapping):
        return dict(quote_provider)
    if callable(quote_provider):
        quotes = quote_provider(list(symbols))
    elif hasattr(quote_provider, "get_quotes") and callable(quote_provider.get_quotes):
        quotes = quote_provider.get_quotes(list(symbols))
    else:
        raise WatchEngineError(
            "quote_provider_invalid", "quote provider 必须是 callable/mapping 或实现 get_quotes()")
    if not isinstance(quotes, dict):
        raise WatchEngineError("quote_provider_invalid", "quote provider 必须返回 {symbol: quote}")
    return dict(quotes)


def load_quotes_json(path) -> dict:
    """离线 quote 文件 → {symbol: quote}（接受 {"quotes": {...}} 或直接 mapping）。"""
    target = Path(str(path))
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise WatchEngineError("quotes_unreadable", "读取 quotes 文件失败: %s" % exc) from exc
    except ValueError as exc:
        raise WatchEngineError("quotes_invalid", "quotes 文件不是合法 JSON: %s" % exc) from exc
    if isinstance(data, dict) and isinstance(data.get("quotes"), dict):
        data = data["quotes"]
    if not isinstance(data, dict):
        raise WatchEngineError("quotes_invalid", "quotes 必须是 {symbol: quote} mapping")
    return data


# ── status 输出（原子写；dry-run 不写）───────────────────────────────
def _previous_status(status_path):
    """读既有 status.json 的累计字段（tick_count/started_at）；缺失/坏文件不猜。"""
    if not status_path:
        return {}
    try:
        data = json.loads(Path(str(status_path)).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    prev = {}
    tick_count = data.get("tick_count")
    if isinstance(tick_count, int) and not isinstance(tick_count, bool):
        prev["tick_count"] = tick_count
    started_at = data.get("started_at")
    if isinstance(started_at, str) and started_at.strip():
        prev["started_at"] = started_at.strip()
    return prev


def write_status_atomic(status, path) -> bool:
    target = Path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(target)
    return True


def _finalize(status, status_path, dry_run):
    # API 兼容别名（WO-WATCH-WIRE-20261104）：心跳/tick 时间与引擎状态
    status["last_tick_at"] = status.get("last_tick_at") or _now_iso(None)
    status["heartbeat_at"] = status["last_tick_at"]
    status["engine_state"] = "running" if status.get("ok") else "error"
    status["status_written"] = False
    if status_path and not dry_run:
        try:
            write_status_atomic(status, status_path)
            status["status_written"] = True
        except OSError as exc:
            status["ok"] = False
            status["status_write_error"] = "%s: %s" % (type(exc).__name__, exc)
    elif status_path and dry_run:
        status["status_write_skipped"] = "dry_run"
    status["engine_state"] = "running" if status.get("ok") else "error"
    return status


# ── 引擎主循环（单轮）────────────────────────────────────────────────
def evaluate_once(plan_ledger=None, trigger_ledger=None, data_dir=None,
                  quote_provider=None, dry_run=False, status_path=None,
                  now=None, run_id=None, extra_status=None) -> dict:
    """单轮评估：armed plan → 条件 → 命中调 consumer.trigger_plan（pending 确认，不执行）。

    ``extra_status`` 供 runner 注入 mode/pid/interval_s/started_at/tick_count 等运行态字段
    （不改评估逻辑；核心字段不可覆盖，started_at/tick_count 两项除外）。
    """
    stamp = _now_iso(now)
    prev = _previous_status(status_path)
    tick_count = (int(prev["tick_count"]) + 1) if prev.get("tick_count") else 1
    status = {
        "schema": SCHEMA,
        "ok": True,
        "run_id": run_id or new_run_id(now),
        "last_run": stamp,
        "dry_run": bool(dry_run),
        "scanned": 0,
        "armed": 0,
        "waiting": 0,
        "triggered": 0,
        "errors": 0,
        "hits": 0,
        "would_trigger": 0,
        "expired": 0,
        "results": [],
        "plan_ledger": str(plan_ledger) if plan_ledger else str(consumer.plan_ledger_path()),
        "trigger_ledger_configured": bool(
            trigger_ledger or os.environ.get(TRIGGER_LEDGER_ENV)),
        "paper_only": True,
        "not_a_trade": True,
        "executes": False,
        # API 兼容别名（WO-WATCH-WIRE-20261104；评估字段保持不变）
        "engine_state": "running",
        "started_at": prev.get("started_at") or stamp,
        "last_tick_at": stamp,
        "heartbeat_at": stamp,
        "armed_count": 0,
        "tick_count": tick_count,
    }
    if isinstance(extra_status, dict):
        for key, value in extra_status.items():
            if key in ("started_at", "tick_count") or key not in status:
                status[key] = value
    try:
        plans = consumer.list_plans(path=plan_ledger, status="armed")
    except Exception as exc:  # noqa: BLE001 — plan ledger 不可读 → 显式 fail-visible
        status.update(ok=False, error="plan_ledger_error",
                      reason="%s: %s" % (type(exc).__name__, str(exc)[:200]))
        return _finalize(status, status_path, dry_run)
    status["scanned"] = len(plans)
    status["armed"] = len(plans)
    status["armed_count"] = len(plans)
    parsed = []
    expired = 0
    for plan in plans:
        try:
            spec = parse_condition(plan)
            expires_raw = plan.get("expires_at")
            if expires_raw:
                try:
                    expires = datetime.fromisoformat(
                        str(expires_raw)[:-1] + "+00:00"
                        if str(expires_raw).endswith("Z") else str(expires_raw))
                except ValueError:
                    expires = None
                if expires is not None:
                    if expires.tzinfo is None:
                        expires = expires.astimezone()
                    if expires <= datetime.fromisoformat(stamp):
                        expired += 1
                        status["results"].append({
                            "plan_id": plan.get("plan_id"), "symbol": spec["symbol"],
                            "status": "expired", "executes": False,
                            "reason": "plan 已过期（expires_at=%s），引擎不触发" % expires_raw,
                        })
                        continue
        except WatchEngineError as exc:
            status["errors"] += 1
            status["results"].append({
                "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
                "status": exc.code,
                "error": {"code": exc.code, "message": str(exc)[:300]},
                "executes": False,
            })
            continue
        parsed.append((plan, spec))
    status["expired"] = expired
    if not parsed:
        return _finalize(status, status_path, dry_run)
    symbols = sorted({spec["symbol"] for _, spec in parsed})
    try:
        quotes = _resolve_provider(quote_provider, symbols)
    except WatchEngineError as exc:
        status.update(ok=False, error=exc.code,
                      reason=str(exc)[:300],
                      requested_symbols=symbols)
        return _finalize(status, status_path, dry_run)
    except Exception as exc:  # noqa: BLE001 — provider 异常 fail-visible，不猜
        status.update(ok=False, error="quote_unavailable",
                      reason="%s: %s" % (type(exc).__name__, str(exc)[:200]),
                      requested_symbols=symbols)
        return _finalize(status, status_path, dry_run)
    for plan, spec in parsed:
        result = {
            "plan_id": plan.get("plan_id"),
            "seat": plan.get("seat"),
            "method": plan.get("method"),
            "symbol": spec["symbol"],
            "operator": spec["operator"],
            "mode": spec["mode"],
            "quote_field": spec.get("quote_field"),
            "condition": {
                "operator": spec["operator"],
                "price": spec.get("price"),
                "price_min": spec.get("price_min"),
                "price_max": spec.get("price_max"),
            },
            "executes": False,
        }
        quote = quotes.get(spec["symbol"])
        if quote is None:
            status["errors"] += 1
            result.update(status="quote_unavailable",
                          error={"code": "quote_unavailable",
                                 "message": "provider 未返回 %s 的 quote" % spec["symbol"]})
            status["results"].append(result)
            continue
        try:
            evaluation = evaluate_condition(spec, quote, now=now)
        except WatchEngineError as exc:
            status["errors"] += 1
            result.update(status=exc.code,
                          error={"code": exc.code, "message": str(exc)[:300]})
            status["results"].append(result)
            continue
        result.update(observed=evaluation.get("observed"),
                      prev=evaluation.get("prev"),
                      threshold=evaluation.get("threshold"),
                      quote_age_s=evaluation.get("quote_age_s"),
                      reason=evaluation.get("reason"))
        if not evaluation.get("hit"):
            status["waiting"] += 1
            result["status"] = "waiting"
            status["results"].append(result)
            continue
        status["hits"] += 1
        result["hit"] = True
        if dry_run:
            status["would_trigger"] += 1
            result["status"] = "would_trigger"
            status["results"].append(result)
            continue
        if spec.get("adapter_price") is None:
            status["errors"] += 1
            result.update(status="trigger_unsupported",
                          error={"code": "trigger_unsupported",
                                 "message": "条件命中但 plan 无 adapter 可用的单点 price；"
                                            "区间/cross 条件请补 price 作为确认价"
                                            "（本单不改 consumer/adapter）"})
            status["results"].append(result)
            continue
        try:
            triggered = consumer.trigger_plan(
                plan.get("plan_id"),
                path=plan_ledger,
                data_dir=data_dir,
                trigger_ledger=trigger_ledger,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 — 触发失败 fail-visible，不吞
            status["errors"] += 1
            code = getattr(exc, "code", "trigger_failed")
            result.update(status="trigger_failed",
                          error={"code": code, "message": str(exc)[:300]})
            status["results"].append(result)
            continue
        status["triggered"] += 1
        confirmation = triggered.get("confirmation") or {}
        result.update(
            status="triggered",
            confirmation_id=confirmation.get("confirmation_id"),
            trigger_id=triggered.get("trigger_id"),
            ids=triggered.get("ids"),
            confirmation_status=confirmation.get("status"),
            paper_only=True,
        )
        status["results"].append(result)
    return _finalize(status, status_path, dry_run)


def run(**kwargs) -> dict:
    """evaluate_once 别名（便于调度器/接线调用）。"""
    return evaluate_once(**kwargs)


# ── CLI ──────────────────────────────────────────────────────────────
USAGE = """用法:
    python -X utf8 -m agent_platform.watch_engine --once \\
        --plan-ledger <watch_plans.jsonl> --trigger-ledger <watch_triggers.jsonl> \\
        --data-dir <trading_bot_dir> --quotes-json <quotes.json> \\
        [--dry-run] [--status <status.json>] [--json]

条件语义见 docs/plans/2026-11-04_盯盘完整系统_引擎_v1.md；
默认 quote provider 无网络；命中只建 pending 人工确认，绝不下单/实盘。"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="盯盘条件评估引擎 MVP（armed watch_plan → 条件 → pending 确认；只读/paper-only）",
        usage=USAGE,
    )
    parser.add_argument("--once", action="store_true", help="单轮评估（MVP 默认单轮）")
    parser.add_argument("--plan-ledger", default="", help="plan 账本路径（缺省 consumer 默认/env）")
    parser.add_argument("--trigger-ledger", default="", help="trigger 账本路径（显式/env；不配则不落 trigger 账）")
    parser.add_argument("--data-dir", default="", help="trading_bot 数据目录（confirmation 落盘位置）")
    parser.add_argument("--quotes-json", default="", help="离线 quotes JSON（{symbol: quote}）")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写（不建确认/不写 status/ledger）")
    parser.add_argument("--status", default="", help="status JSON 输出路径（dry-run 不写）")
    parser.add_argument("--json", action="store_true", help="stdout 输出完整 status JSON")
    args = parser.parse_args(argv)
    provider = None
    try:
        if args.quotes_json:
            provider = load_quotes_json(args.quotes_json)
        status = evaluate_once(
            plan_ledger=args.plan_ledger or None,
            trigger_ledger=args.trigger_ledger or None,
            data_dir=args.data_dir or None,
            quote_provider=provider,
            dry_run=bool(args.dry_run),
            status_path=args.status or None,
        )
    except WatchEngineError as exc:
        status = {"schema": SCHEMA, "ok": False, "error": exc.code,
                  "reason": str(exc)[:300], "paper_only": True, "executes": False}
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print("[watch_engine] ok=%s run_id=%s scanned=%s armed=%s waiting=%s "
              "triggered=%s errors=%s dry_run=%s"
              % (status.get("ok"), status.get("run_id"), status.get("scanned"),
                 status.get("armed"), status.get("waiting"), status.get("triggered"),
                 status.get("errors"), status.get("dry_run")))
        if status.get("error"):
            print("[watch_engine] error=%s reason=%s"
                  % (status.get("error"), status.get("reason")))
    return 0 if status.get("ok") else 1


__all__ = [
    "BUY_ACTIONS",
    "EXECUTION_FIELDS",
    "OPERATORS",
    "QUOTE_FIELDS",
    "SCHEMA",
    "STATUS_ENV",
    "SELL_ACTIONS",
    "TRIGGER_LEDGER_ENV",
    "WatchEngineError",
    "evaluate_condition",
    "evaluate_once",
    "load_quotes_json",
    "main",
    "new_run_id",
    "parse_condition",
    "run",
    "write_status_atomic",
]


if __name__ == "__main__":
    sys.exit(main())
