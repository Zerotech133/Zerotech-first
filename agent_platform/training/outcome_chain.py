# -*- coding: utf-8 -*-
"""Outcome / training_episode 只读链（IFS v1 F5；WO-IFS-TRAIN-20261103）。

把 IFS WIRE 链尾「paper trade receipt（五 ID）→ outcome（+outcome_id，六 ID）→
training_episode（+training_episode_id，七 ID）」补齐为**可回放、可审计、不造数**的
只读聚合层：

```text
receipt(trade_id, confirmation_id, decision_id, handoff_id, watch_plan_id)
  → outcome(outcome_id + 六 ID)；行情由调用方注入 T+1 next_close
  → training_episode(training_episode_id + ids/metrics/errors/corrections)
```

ID 契约（与 `docs/standards/2026-11-03_ID_CHAIN_v1.md` 同格式）：
- ``outcome_id``  = ``out-<yyyymmdd>-<SHA256(上游ID|horizon)[:8]大写>``，
  日期取**成交日**（quote 后续回填不改 ID，保证幂等不换号）；
- ``training_episode_id`` = ``ep-<yyyymmdd>-<SHA256(内容键)[:8]大写>``，
  同一 `(combo_id, as_of, outcome_ids, trade_ids)` 重跑返回同一 ID；
- 无上游 ID / 无日期 → 不生成 ID（显式 ``None``），**不伪造**。

缺失语义（本单硬约束）：
- 行情缺失 → ``status=pending_quote``，``next_trade_date/next_close/
  next_day_return/hit`` 全部 ``null``（默认不写入 outcome 账本，与 IFS
  ID_CHAIN §3.10「缺价格不写」一致；``include_pending=True`` 才显式落审计行）；
- 交易价格缺失 / 缺 trade_id → ``status=invalid`` + ``errors``，不产生半真记录；
- receipt 缺 decision/handoff/watch_plan → outcome 对应字段显式 ``null``（六 ID
  键始终存在），不拿 report_id/run_id 冒充。

只读边界：
- 本模块**不访问网络、不读未登记数据集**；行情只接受调用方注入（mapping/list
  JSON 或 callable），无默认行情源（不伪造 T+1 收盘价）。
- 写操作只有 ``append_outcomes`` / ``write_episode_json`` / ``append_episode``，
  全部要求**显式路径**；不提供任何生产默认写入。
- 不自动训练、不自动改 registry/权重、不写 portal_api、不启动服务、不实盘。

CLI::

    python -X utf8 -m agent_platform.training.outcome_chain contract
    python -X utf8 -m agent_platform.training.outcome_chain backfill \
        --receipts <receipts.jsonl> [--quotes <quotes.json>] [--out <outcomes.jsonl>] \
        [--include-pending] [--no-write] [--json]
    python -X utf8 -m agent_platform.training.outcome_chain episode \
        --receipts <receipts.jsonl> --outcomes <outcomes.jsonl> [--out <episode.json>] \
        [--combo-id <id>] [--as-of <YYYY-MM-DD>] \
        [--data-snapshot '<json>' | --data-snapshot-file <snapshot.json>] [--json]
    python -X utf8 -m agent_platform.training.outcome_chain backfill-ids \
        --legacy-outcomes <decision_outcomes.jsonl> [--out <out.jsonl>] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone

SCHEMA_VERSION = 1
OUTCOME_SCHEMA = "d1_outcome/v1"
EPISODE_SCHEMA = "training_episode/v1"
CONTRACT_VERSION = "ifsp_train/v1"

OUTCOME_ID_FORMAT = r"^out-\d{8}-[0-9A-F]{8}$"
EPISODE_ID_FORMAT = r"^ep-\d{8}-[0-9A-F]{8}$"
_OUTCOME_ID_RE = re.compile(OUTCOME_ID_FORMAT)
_EPISODE_ID_RE = re.compile(EPISODE_ID_FORMAT)

#: outcome 记录必须携带的六 ID 键（缺失值显式 null，不省略键）。
SIX_ID_KEYS = ("decision_id", "handoff_id", "watch_plan_id",
               "confirmation_id", "trade_id", "outcome_id")
#: outcome 的上游五 ID（ID_CHAIN：trade_id 为直接上游）。
UPSTREAM_ID_KEYS = SIX_ID_KEYS[:-1]

STATUS_COMPLETE = "complete"
STATUS_PENDING_QUOTE = "pending_quote"
STATUS_INVALID = "invalid"
OUTCOME_STATUSES = (STATUS_COMPLETE, STATUS_PENDING_QUOTE, STATUS_INVALID)
QUOTE_STATUSES = ("complete", "missing")

DIRECTIONS = {"BUY": 1, "SELL": -1}
DEFAULT_HORIZON_DAYS = 1

#: 禁止出现在训练 episode / outcome 里的执行字段（IFS-05）。
FORBIDDEN_EXEC_KEYS = ("order", "order_id", "qty", "quantity", "broker",
                       "execute", "execution", "fill", "fills", "account",
                       "position_size")

#: 默认数据位置（只作文档/参考；本模块不默认写入这些路径）。
BASE = os.path.dirname(os.path.abspath(__file__))
AGENT_ROOT = os.path.dirname(BASE)
DEFAULT_EPISODES = os.path.join(AGENT_ROOT, "data", "training",
                               "episodes.jsonl")
DEFAULT_RECEIPTS = os.path.join(AGENT_ROOT, "data", "trading_bot",
                                "receipts.jsonl")
DEFAULT_LEGACY_OUTCOMES = os.path.join(AGENT_ROOT, "data", "shadow",
                                       "decision_outcomes.jsonl")


class OutcomeChainError(ValueError):
    """契约/参数错误（调用方 fail-closed；不吞）。"""


# ---------------------------------------------------------------------------
# 基础工具（只读/纯函数）
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clip(value, limit=160):
    return str(value or "").strip()[:limit]


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _id_or_none(value):
    text = _clip(value, 120)
    return text or None


def _date_digits(value):
    """ISO 日期/日期时间 → YYYYMMDD（非法/缺失 → None，不猜日期）。"""
    text = _clip(value, 40)
    match = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", text)
    if match:
        return match.group(1) + match.group(2) + match.group(3)
    match = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
    if match:
        return match.group(1) + match.group(2) + match.group(3)
    return None


def _sha8(*parts):
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()[:8]


def read_jsonl(path):
    """只读 JSONL（缺失/坏行 → 跳过，不造数）；path 为空返回 []。"""
    rows = []
    if not path:
        return rows
    try:
        with open(str(path), encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return rows
    return rows


def load_json_file(path):
    """只读 JSON 文件（缺失/坏 JSON → None，不造数）。"""
    if not path:
        return None
    try:
        with open(str(path), encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _append_jsonl(path, rows):
    """append-only JSONL 写入（仅显式路径；fsync 落盘）。"""
    if not path:
        raise OutcomeChainError("写入目标为空：本模块不提供默认生产写入路径")
    folder = os.path.dirname(os.path.abspath(str(path)))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(str(path), "a", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True)
                         + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path, payload):
    """原子 JSON 写入（仅显式路径；临时文件 + os.replace）。"""
    if not path:
        raise OutcomeChainError("写入目标为空：本模块不提供默认生产写入路径")
    folder = os.path.dirname(os.path.abspath(str(path)))
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2,
                  sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, str(path))


# ---------------------------------------------------------------------------
# 六 ID 提取 / 生成
# ---------------------------------------------------------------------------
def record_ids(record):
    """从 receipt/outcome/legacy 行取六 ID（兼容 ``ids`` 嵌套与 source_* 别名）。

    返回 dict（六键齐全，缺失值为 None——不省略键、不拿别名冒充）。
    """
    record = record if isinstance(record, dict) else {}
    nested = record.get("ids") if isinstance(record.get("ids"), dict) else {}

    def pick(*keys):
        for key in keys:
            value = record.get(key)
            if value in (None, ""):
                value = nested.get(key)
            if value not in (None, ""):
                return _id_or_none(value)
        return None

    ids = {
        "decision_id": pick("decision_id", "source_decision_id"),
        "handoff_id": pick("handoff_id", "source_handoff_id", "receipt_id"),
        "watch_plan_id": pick("watch_plan_id", "source_watch_plan_id"),
        "confirmation_id": pick("confirmation_id"),
        "trade_id": pick("trade_id"),
        "outcome_id": pick("outcome_id"),
    }
    return ids


def six_ids(record):
    """对外别名：六 ID（含 outcome_id）显式视图。"""
    return record_ids(record)


def new_outcome_id(*, trade_id="", run_id="", as_of="", horizon_days=None):
    """生成 outcome_id：``out-<成交日>-<SHA8>``。

    - 上游键优先 trade_id（标准链），legacy 行回填允许 run_id 作为兼容上游键；
    - 日期取成交日（as_of），quote 后续回填不换 ID；
    - 缺上游键/日期 → None（不生成、不伪造）。
    """
    upstream = _id_or_none(trade_id) or _id_or_none(run_id)
    day = _date_digits(as_of)
    if not upstream or day is None:
        return None
    horizon = DEFAULT_HORIZON_DAYS if horizon_days is None else int(horizon_days)
    return "out-%s-%s" % (day, _sha8(upstream, horizon))


def new_training_episode_id(*, combo_id=None, as_of="", outcome_ids=(),
                            trade_ids=()):
    """生成 training_episode_id：``ep-<as_of 日>-<内容 SHA8>``（幂等同内容同 ID）。"""
    day = _date_digits(as_of) or datetime.now().strftime("%Y%m%d")
    seed = json.dumps({
        "combo_id": _id_or_none(combo_id),
        "as_of": _clip(as_of, 40),
        "outcome_ids": sorted(_id_or_none(x) or "" for x in outcome_ids),
        "trade_ids": sorted(_id_or_none(x) or "" for x in trade_ids),
    }, ensure_ascii=False, sort_keys=True)
    return "ep-%s-%s" % (day, _sha8(seed))


# ---------------------------------------------------------------------------
# 行情注入（只接受调用方提供；无默认网络行情源）
# ---------------------------------------------------------------------------
def _normalize_quote(quote):
    """行情归一：{next_trade_date, next_close}；非法/缺失 → None（不伪造）。"""
    if not isinstance(quote, dict):
        return None
    next_close = _num(quote.get("next_close"))
    if next_close is None:
        next_close = _num(quote.get("close"))
    if next_close is None or next_close <= 0:
        return None
    next_date = (_clip(quote.get("next_trade_date") or quote.get("date"), 40)
                 or None)
    return {"next_trade_date": next_date, "next_close": next_close}


def _first_after(rows, date):
    """按日期排序的行情行里取 date 之后的第一条（严格晚于成交日）。"""
    target = _clip(date, 40)
    ordered = sorted((row for row in rows if isinstance(row, dict)),
                     key=lambda row: _clip(row.get("date")
                                           or row.get("trade_date"), 40))
    for row in ordered:
        row_day = _clip(row.get("date") or row.get("trade_date"), 40)
        if not target or row_day > target:
            return _normalize_quote(row)
    return None


def _mapping_lookup(mapping):
    def lookup(symbol, date):
        symbol = _clip(symbol, 40)
        day = _clip(date, 40)
        candidates = ((symbol, day), str(symbol) + "|" + day, symbol)
        for key in candidates:
            if key in mapping:
                value = mapping[key]
                if isinstance(value, list):
                    return _first_after(value, day)
                return _normalize_quote(value)
        return None
    return lookup


def make_quote_lookup(source):
    """把行情源归一成 ``lookup(symbol, date) -> quote|None``。

    接受：callable；dict（键 ``(symbol, date)`` / ``"symbol|date"`` / ``symbol``，
    值=quote 或 bars 列表）；list（bars 行：symbol/date/close 或 quote 行）。
    缺失/非法一律返回 None，**不伪造行情**。
    """
    if source is None:
        return lambda symbol, date: None
    if callable(source):
        return source
    if isinstance(source, dict):
        return _mapping_lookup(source)
    if isinstance(source, (list, tuple)):
        by_symbol = {}
        for row in source:
            if not isinstance(row, dict):
                continue
            symbol = _clip(row.get("symbol"), 40)
            if symbol:
                by_symbol.setdefault(symbol, []).append(row)
        for rows in by_symbol.values():
            rows.sort(key=lambda item: _clip(item.get("date")
                                             or item.get("trade_date"), 40))

        def lookup(symbol, date):
            return _first_after(by_symbol.get(_clip(symbol, 40), []), date)
        return lookup
    raise OutcomeChainError("行情源须为 callable/dict/list/None: %r"
                            % (type(source).__name__,))


def load_quotes(path):
    """只读加载行情 JSON → lookup（文件缺失/坏 JSON → 全 None，不伪造）。"""
    return make_quote_lookup(load_json_file(path))


# ---------------------------------------------------------------------------
# outcome：生成 / 回填 / 幂等落盘
# ---------------------------------------------------------------------------
def _outcome_status(ids, price, quote):
    if not ids.get("trade_id"):
        return STATUS_INVALID
    if price is None or price <= 0:
        return STATUS_INVALID
    if quote is None:
        return STATUS_PENDING_QUOTE
    return STATUS_COMPLETE


def _hit(action, next_day_return):
    direction = DIRECTIONS.get(_clip(action, 16).upper())
    if direction is None or next_day_return is None:
        return None
    if direction > 0:
        return 1 if next_day_return > 0 else 0
    return 1 if next_day_return < 0 else 0


def build_outcome(receipt, quote=None, *, horizon_days=None):
    """构建单条 outcome（纯函数，不写盘）。

    - 六 ID 键始终存在（缺失显式 None）；
    - quote 缺失 → status=pending_quote + 行情字段全 null（不伪造）；
    - price/trade_id 缺失 → status=invalid + errors，不产生半真数据。
    """
    if not isinstance(receipt, dict):
        raise OutcomeChainError("receipt 须为 dict: %r" % (receipt,))
    horizon = DEFAULT_HORIZON_DAYS if horizon_days is None else int(horizon_days)
    ids = record_ids(receipt)
    trade_date = (_clip(receipt.get("date") or receipt.get("trade_date"), 40)
                  or None)
    price = _num(receipt.get("price"))
    action = _clip(receipt.get("action"), 16).upper() or None
    normalized_quote = _normalize_quote(quote) if quote is not None else None
    status = _outcome_status(ids, price, normalized_quote)
    errors = []
    if ids["trade_id"] is None:
        errors.append({"code": "missing_trade_id",
                       "message": "receipt 缺 trade_id：outcome 不生成 ID（不伪造）",
                       "ref": ids["confirmation_id"]})
    if price is None or price <= 0:
        errors.append({"code": "missing_price",
                       "message": "receipt 缺有效成交价：不计算收益（不伪造）",
                       "ref": ids["trade_id"]})
    if status == STATUS_PENDING_QUOTE:
        errors.append({"code": "quote_missing",
                       "message": "缺 T+1 行情：行情字段显式 null（不伪造）",
                       "ref": ids["trade_id"]})
    outcome_id = new_outcome_id(trade_id=ids["trade_id"],
                                as_of=trade_date,
                                horizon_days=horizon)
    ids["outcome_id"] = outcome_id
    next_trade_date = None
    next_close = None
    next_day_return = None
    hit = None
    quote_status = "missing"
    if status == STATUS_COMPLETE:
        next_trade_date = normalized_quote["next_trade_date"]
        next_close = round(normalized_quote["next_close"], 6)
        next_day_return = round(next_close / price - 1.0, 6)
        hit = _hit(action, next_day_return)
        quote_status = "complete"
    record = {
        "schema": OUTCOME_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "outcome_id": outcome_id,
        "status": status,
        # 六 ID（上游缺失显式 null）
        "decision_id": ids["decision_id"],
        "handoff_id": ids["handoff_id"],
        "watch_plan_id": ids["watch_plan_id"],
        "confirmation_id": ids["confirmation_id"],
        "trade_id": ids["trade_id"],
        "ids": dict(ids),
        "source": "paper_receipt",
        "source_report_id": _clip(receipt.get("source_report_id"), 120) or None,
        "symbol": _clip(receipt.get("symbol"), 40) or None,
        "name": _clip(receipt.get("name"), 80) or None,
        "action": action,
        "date": trade_date,
        "price": price,
        "horizon_days": horizon,
        "next_trade_date": next_trade_date,
        "next_close": next_close,
        "next_day_return": next_day_return,
        "hit": hit,
        "quote_status": quote_status,
        "errors": errors,
        "paper_only": True,
        "trading_permitted": False,
        "not_a_trade": True,
        "generated_at": _now(),
    }
    return record


def generate_outcomes(receipts, quotes=None, *, existing=(), horizon_days=None):
    """从 receipts 批量生成 outcome（幂等：按 trade_id 跳过已有/已完成；不写盘）。

    ``quotes`` 可为 callable / dict / list / None；None = 全部 pending_quote
    （显式 null，不伪造行情）。返回 ``{"complete", "pending", "invalid",
    "skipped", "outcomes", "errors"}``。
    """
    horizon = DEFAULT_HORIZON_DAYS if horizon_days is None else int(horizon_days)
    lookup = make_quote_lookup(quotes)
    existing_rows = [row for row in (existing or ())
                     if isinstance(row, dict)]
    done_trades = set()
    for row in existing_rows:
        ids = record_ids(row)
        if ids["trade_id"]:
            done_trades.add(ids["trade_id"])
    result = {"complete": [], "pending": [], "invalid": [],
              "skipped": 0, "outcomes": [], "errors": []}
    for receipt in receipts or ():
        if not isinstance(receipt, dict):
            result["invalid"].append({"status": STATUS_INVALID,
                                      "errors": [{"code": "bad_receipt",
                                                  "message": "receipt 须为 dict",
                                                  "ref": None}]})
            continue
        ids = record_ids(receipt)
        if ids["trade_id"] and ids["trade_id"] in done_trades:
            result["skipped"] += 1
            continue
        quote = None
        if ids["trade_id"]:
            quote = lookup(receipt.get("symbol"),
                           receipt.get("date") or receipt.get("trade_date"))
        outcome = build_outcome(receipt, quote, horizon_days=horizon)
        result["outcomes"].append(outcome)
        result["errors"].extend(outcome.get("errors") or [])
        if outcome["status"] == STATUS_COMPLETE:
            result["complete"].append(outcome)
            if ids["trade_id"]:
                done_trades.add(ids["trade_id"])
        elif outcome["status"] == STATUS_PENDING_QUOTE:
            result["pending"].append(outcome)
        else:
            result["invalid"].append(outcome)
    return result


def backfill_outcomes(receipts, quotes=None, *, existing=(), path=None,
                      write=False, include_pending=False, horizon_days=None):
    """生成 + （可选）显式路径落盘；默认只报告不写（``write=False``）。

    默认只写 complete（IFS ID_CHAIN §3.10：缺价格不写）；``include_pending``
    才把 pending_quote 显式落审计行（行情字段仍 null）。invalid 永不写入。

    返回报告 dict（含 ``written``/``out_path``/``skipped``/``errors`` 等）。
    """
    if write and not path:
        raise OutcomeChainError("write=True 必须显式给 out path"
                                "（本模块不提供默认生产写入路径）")
    existing_rows = list(existing or ())
    if write:
        existing_rows.extend(read_jsonl(path))
    report = generate_outcomes(receipts, quotes, existing=existing_rows,
                               horizon_days=horizon_days)
    to_write = list(report["complete"])
    if include_pending:
        to_write.extend(report["pending"])
    written = 0
    if write and to_write:
        written = append_outcomes(path, to_write)
    return {
        "schema": "ifsp_outcome_backfill_report/v1",
        "receipts": len([row for row in (receipts or ())
                         if isinstance(row, dict)]),
        "complete": len(report["complete"]),
        "pending": len(report["pending"]),
        "invalid": len(report["invalid"]),
        "skipped": report["skipped"],
        "written": written,
        "out_path": str(path) if path else None,
        "include_pending": bool(include_pending),
        "outcomes": to_write,
        "errors": report["errors"],
        "paper_only": True,
        "trading_permitted": False,
        "auto_training": False,
    }


def append_outcomes(path, outcomes):
    """append-only 幂等落 outcome（按 outcome_id/trade_id 去重）；返回新增行数。"""
    if not path:
        raise OutcomeChainError("out path 必填（不提供默认生产写入）")
    existing = read_jsonl(path)
    seen_ids = {_id_or_none(row.get("outcome_id")) for row in existing}
    seen_trades = {_id_or_none(row.get("trade_id")) for row in existing}
    new_rows = []
    for row in outcomes or ():
        if not isinstance(row, dict):
            continue
        outcome_id = _id_or_none(row.get("outcome_id"))
        trade_id = _id_or_none(row.get("trade_id"))
        if not outcome_id or not trade_id:
            continue
        if outcome_id in seen_ids or trade_id in seen_trades:
            continue
        new_rows.append(row)
        seen_ids.add(outcome_id)
        seen_trades.add(trade_id)
    if new_rows:
        _append_jsonl(path, new_rows)
    return len(new_rows)


def backfill_outcome_ids(rows, *, horizon_days=None):
    """给历史 outcome 行补 outcome_id / 六 ID 键 / ``ids`` / status（只读纯函数）。

    兼容 legacy ``decision_outcomes.jsonl`` 行：上游键 trade_id 缺失时用 ``run_id``
    作为兼容上游键；缺日期/上游 → outcome_id=None（不伪造）。已有 outcome_id 不换。
    返回 ``{"rows", "report"}``；report 含 filled/already/unavailable 计数。
    """
    horizon = DEFAULT_HORIZON_DAYS if horizon_days is None else int(horizon_days)
    out = []
    report = {"rows": 0, "filled": 0, "already": 0, "unavailable": 0}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        report["rows"] += 1
        new_row = dict(row)
        ids = record_ids(row)
        outcome_id = ids["outcome_id"]
        if outcome_id:
            report["already"] += 1
        else:
            outcome_id = new_outcome_id(
                trade_id=ids["trade_id"] or "",
                run_id="" if ids["trade_id"] else _clip(row.get("run_id"), 120),
                as_of=row.get("date") or row.get("as_of")
                or row.get("trade_date"),
                horizon_days=horizon)
            if outcome_id:
                report["filled"] += 1
            else:
                report["unavailable"] += 1
        new_row["outcome_id"] = outcome_id
        new_row["trade_id"] = ids["trade_id"]
        new_row["decision_id"] = ids["decision_id"]
        new_row["handoff_id"] = ids["handoff_id"]
        new_row["watch_plan_id"] = ids["watch_plan_id"]
        new_row["confirmation_id"] = ids["confirmation_id"]
        new_row["ids"] = record_ids(new_row)
        # 状态仅在不破坏既有字段的前提下补（已有 status 保留）
        new_row.setdefault(
            "status",
            STATUS_COMPLETE if new_row.get("next_day_return") is not None
            else STATUS_PENDING_QUOTE)
        new_row.setdefault("schema", OUTCOME_SCHEMA)
        new_row.setdefault("schema_version", SCHEMA_VERSION)
        new_row.setdefault("paper_only", True)
        new_row.setdefault("trading_permitted", False)
        out.append(new_row)
    return {"rows": out, "report": report}


def write_outcome_id_backfill(rows, path):
    """把回填后的 legacy outcome 行原子写回显式路径（不改原文件；返回行数）。"""
    if not path:
        raise OutcomeChainError("回填输出路径必填（不写生产账本）")
    backfilled = backfill_outcome_ids(rows)
    _atomic_jsonl_write(path, backfilled["rows"])
    return len(backfilled["rows"])


def _atomic_jsonl_write(path, rows):
    if not path:
        raise OutcomeChainError("写入目标为空：本模块不提供默认生产写入路径")
    folder = os.path.dirname(os.path.abspath(str(path)))
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True)
                         + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, str(path))


# ---------------------------------------------------------------------------
# training_episode/v1：只读聚合（不自动训练）
# ---------------------------------------------------------------------------
def _sorted_unique(values):
    return sorted({value for value in values if value})


def _metrics(paper_trades, outcomes):
    """episode 指标：只对完整 outcome 计算；分母 0 → None（不造 0）。"""
    complete = [row for row in outcomes
                if row.get("next_day_return") is not None
                and row.get("status") in (None, STATUS_COMPLETE)]
    returns = [_num(row.get("next_day_return")) for row in complete]
    returns = [value for value in returns if value is not None]
    hits = [row.get("hit") for row in complete
            if isinstance(row.get("hit"), int)
            and not isinstance(row.get("hit"), bool)]
    trade_count = len(paper_trades)
    return {
        "trades": trade_count,
        "outcomes": len(outcomes),
        "complete_outcomes": len(complete),
        "outcome_coverage": (round(len(complete) / trade_count, 3)
                             if trade_count else None),
        "directional_n": len(hits),
        "hit_rate": (round(sum(hits) / len(hits), 3) if hits else None),
        "avg_return": (round(sum(returns) / len(returns), 6)
                       if returns else None),
        # receipt 无 confidence → Brier 显式 null（不伪造）
        "brier": None,
        "win_rate": (round(sum(hits) / len(hits), 3) if hits else None),
    }


def _trade_view(receipt):
    """paper_trades 视图：只带对账/训练需要字段（无执行字段）。"""
    ids = record_ids(receipt)
    return {
        "trade_id": ids["trade_id"],
        "confirmation_id": ids["confirmation_id"],
        "decision_id": ids["decision_id"],
        "handoff_id": ids["handoff_id"],
        "watch_plan_id": ids["watch_plan_id"],
        "symbol": _clip(receipt.get("symbol"), 40) or None,
        "action": _clip(receipt.get("action"), 16).upper() or None,
        "date": _clip(receipt.get("date"), 40) or None,
        "price": _num(receipt.get("price")),
        "verdict": _clip(receipt.get("verdict"), 16) or None,
        "entry_path": _clip(receipt.get("entry_path"), 120) or None,
        "paper_only": True,
        "trading_permitted": False,
    }


def _snapshot_view(data_snapshot, as_of):
    if isinstance(data_snapshot, dict):
        return dict(data_snapshot)
    return {"as_of": as_of, "source": "unspecified",
            "note": "调用方未提供 data_snapshot（不造数）"}


def build_training_episode(*, receipts=(), outcomes=(), combo_id=None,
                           data_snapshot=None, corrections=None, as_of=None,
                           errors=None):
    """构建 ``training_episode/v1``（纯函数，不写盘、不训练）。

    - 六 ID 链（outcome 记录内）与 ids 聚合数组可追；
    - 缺行情/孤儿 outcome/缺 trade_id → ``errors`` 显式登记，不伪造；
    - ``corrections`` 只接受调用方输入（不自动生成/不自动应用）；
    - ``metrics`` 分母 0 → null；``auto_training=False`` / ``read_only=True``。
    """
    receipt_rows = [row for row in (receipts or ()) if isinstance(row, dict)]
    outcome_rows = [row for row in (outcomes or ()) if isinstance(row, dict)]
    paper_trades = []
    collected_errors = []
    for receipt in receipt_rows:
        view = _trade_view(receipt)
        if view["trade_id"] is None:
            collected_errors.append({
                "code": "trade_missing_id",
                "message": "receipt 缺 trade_id：不进入 episode ID 链（不伪造）",
                "ref": view["confirmation_id"],
            })
            continue
        paper_trades.append(view)
    trade_ids = {row["trade_id"] for row in paper_trades}
    for outcome in outcome_rows:
        ids = record_ids(outcome)
        if ids["trade_id"] is None:
            collected_errors.append({
                "code": "outcome_missing_trade",
                "message": "outcome 缺 trade_id：链上孤儿（不伪造）",
                "ref": ids["outcome_id"],
            })
        elif ids["trade_id"] not in trade_ids:
            collected_errors.append({
                "code": "outcome_orphan_trade",
                "message": "outcome 的 trade_id 不在本 episode paper_trades",
                "ref": ids["trade_id"],
            })
        if outcome.get("status") != STATUS_COMPLETE:
            collected_errors.append({
                "code": "outcome_pending_quote",
                "message": "outcome 行情字段显式 null（缺行情不伪造）",
                "ref": ids["trade_id"],
            })
    for item in (errors or ()):
        if isinstance(item, dict):
            collected_errors.append(dict(item))
    outcome_ids = _sorted_unique(record_ids(row)["outcome_id"]
                                 for row in outcome_rows)
    all_trade_ids = _sorted_unique(
        list(trade_ids) + [record_ids(row)["trade_id"]
                           for row in outcome_rows])
    as_of_value = _clip(
        as_of or max((_clip(row.get("next_trade_date") or row.get("date"), 40)
                      for row in outcome_rows), default=""), 40) or None
    episode_id = new_training_episode_id(
        combo_id=combo_id, as_of=as_of_value,
        outcome_ids=outcome_ids, trade_ids=all_trade_ids)
    ids_chain = {
        "decision_ids": _sorted_unique(record_ids(row)["decision_id"]
                                       for row in list(receipt_rows)
                                       + outcome_rows),
        "handoff_ids": _sorted_unique(record_ids(row)["handoff_id"]
                                      for row in list(receipt_rows)
                                      + outcome_rows),
        "watch_plan_ids": _sorted_unique(record_ids(row)["watch_plan_id"]
                                        for row in list(receipt_rows)
                                        + outcome_rows),
        "confirmation_ids": _sorted_unique(
            record_ids(row)["confirmation_id"]
            for row in list(receipt_rows) + outcome_rows),
        "trade_ids": all_trade_ids,
        "outcome_ids": outcome_ids,
    }
    episode = {
        "schema": EPISODE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "training_episode_id": episode_id,
        "combo_id": _id_or_none(combo_id),
        "data_snapshot": _snapshot_view(data_snapshot, as_of_value),
        "ids": ids_chain,
        "paper_trades": paper_trades,
        "outcomes": outcome_rows,
        "errors": sorted(collected_errors,
                         key=lambda item: (str(item.get("code")),
                                           str(item.get("ref") or ""))),
        "corrections": [dict(item) for item in (corrections or ())
                        if isinstance(item, dict)],
        "metrics": _metrics(paper_trades, outcome_rows),
        "paper_only": True,
        "trading_permitted": False,
        "not_a_trade": True,
        "read_only": True,
        "auto_training": False,
        "generated_at": _now(),
    }
    return episode


_EPISODE_REQUIRED = (
    "schema", "schema_version", "training_episode_id", "combo_id",
    "data_snapshot", "ids", "paper_trades", "outcomes", "errors",
    "corrections", "metrics", "paper_only", "trading_permitted",
    "not_a_trade", "read_only", "auto_training",
)
_EPISODE_IDS_KEYS = ("decision_ids", "handoff_ids", "watch_plan_ids",
                     "confirmation_ids", "trade_ids", "outcome_ids")
_METRIC_KEYS = ("trades", "outcomes", "complete_outcomes", "outcome_coverage",
                "directional_n", "hit_rate", "avg_return", "brier")


def _find_forbidden_keys(payload, path="$"):
    """递归找执行字段（IFS-05 禁止：order/qty/broker/...），返回路径列表。"""
    hits = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in FORBIDDEN_EXEC_KEYS and value not in (None, "", [], {}):
                hits.append("%s.%s" % (path, key))
            else:
                hits.extend(_find_forbidden_keys(value, "%s.%s" % (path, key)))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            hits.extend(_find_forbidden_keys(value, "%s[%d]" % (path, index)))
    return hits


def validate_episode(episode):
    """校验 ``training_episode/v1`` 契约；返回违规列表（空=通过，不抛错）。"""
    violations = []
    if not isinstance(episode, dict):
        return ["episode 须为 dict"]
    for key in _EPISODE_REQUIRED:
        if key not in episode:
            violations.append("缺字段: %s" % key)
    if episode.get("schema") != EPISODE_SCHEMA:
        violations.append("schema 必须是 %s" % EPISODE_SCHEMA)
    if episode.get("schema_version") != SCHEMA_VERSION:
        violations.append("schema_version 必须是 %d" % SCHEMA_VERSION)
    episode_id = episode.get("training_episode_id")
    if not isinstance(episode_id, str) \
            or not _EPISODE_ID_RE.match(episode_id or ""):
        violations.append("training_episode_id 格式非法: %r" % (episode_id,))
    if episode.get("combo_id") is not None \
            and not isinstance(episode.get("combo_id"), str):
        violations.append("combo_id 须为 str 或 null")
    if episode.get("paper_only") is not True:
        violations.append("paper_only 必须为 true")
    if episode.get("trading_permitted") is not False:
        violations.append("trading_permitted 必须为 false")
    if episode.get("not_a_trade") is not True:
        violations.append("not_a_trade 必须为 true")
    if episode.get("read_only") is not True:
        violations.append("read_only 必须为 true")
    if episode.get("auto_training") is not False:
        violations.append("auto_training 必须为 false（不自动训练）")
    ids = episode.get("ids")
    if not isinstance(ids, dict):
        violations.append("ids 须为 dict")
    else:
        for key in _EPISODE_IDS_KEYS:
            if not isinstance(ids.get(key), list):
                violations.append("ids.%s 须为 list" % key)
        if not _sorted_unique(ids.get("trade_ids") or []):
            violations.append("ids.trade_ids 不能为空（episode 须有 paper trade）")
        if not _sorted_unique(ids.get("outcome_ids") or []):
            violations.append("ids.outcome_ids 不能为空（episode 须有 outcome）")
        outcome_ids_in_rows = {
            record_ids(row)["outcome_id"] for row in
            (episode.get("outcomes") or []) if isinstance(row, dict)}
        for outcome_id in ids.get("outcome_ids") or []:
            if outcome_id not in outcome_ids_in_rows:
                violations.append("ids.outcome_ids 含未知 outcome: %s"
                                  % outcome_id)
    metrics = episode.get("metrics")
    if not isinstance(metrics, dict):
        violations.append("metrics 须为 dict")
    else:
        for key in _METRIC_KEYS:
            if key not in metrics:
                violations.append("metrics 缺字段: %s" % key)
    for row in episode.get("outcomes") or []:
        if not isinstance(row, dict):
            violations.append("outcomes 元素须为 dict")
            continue
        missing = [key for key in SIX_ID_KEYS if key not in row]
        if missing:
            violations.append("outcome 缺六 ID 键: %s" % ",".join(missing))
    if not isinstance(episode.get("corrections"), list):
        violations.append("corrections 须为 list")
    if not isinstance(episode.get("errors"), list):
        violations.append("errors 须为 list")
    found = _find_forbidden_keys(episode)
    if found:
        violations.append("禁止执行字段: %s" % ",".join(found[:5]))
    return violations


def write_episode_json(path, episode):
    """把 episode 原子写到显式路径（不写生产默认路径）；返回校验违规列表。"""
    if not path:
        raise OutcomeChainError("episode 输出路径必填（不写生产数据）")
    violations = validate_episode(episode)
    _atomic_json(path, episode)
    return violations


def append_episode(path, episode):
    """append-only episodes.jsonl（按 training_episode_id 幂等）；返回是否新增。"""
    if not path:
        raise OutcomeChainError("episode 账本路径必填（不写生产数据）")
    episode_id = _id_or_none(episode.get("training_episode_id"))
    if not episode_id:
        raise OutcomeChainError("episode 缺 training_episode_id")
    existing_ids = {_id_or_none(row.get("training_episode_id"))
                    for row in read_jsonl(path)}
    if episode_id in existing_ids:
        return False
    _append_jsonl(path, [episode])
    return True


def load_receipts(path):
    """只读 paper receipt 日志（缺失 → []；不造数）。"""
    return read_jsonl(path)


def load_outcomes(path):
    """只读 outcome 账本（缺失 → []；不造数）。"""
    return read_jsonl(path)


def contract():
    """机器可读契约（ID 格式/六 ID/缺省语义/边界；供接线与契约测试）。"""
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "outcome": {
            "schema": OUTCOME_SCHEMA,
            "id_format": OUTCOME_ID_FORMAT,
            "id_generator": "training/outcome_chain.py::new_outcome_id",
            "id_upstream_keys": ["trade_id", "run_id（legacy 兼容键）"],
            "id_date_basis": "成交日（quote 回填不改 ID）",
            "six_id_keys": list(SIX_ID_KEYS),
            "statuses": list(OUTCOME_STATUSES),
            "quote_statuses": list(QUOTE_STATUSES),
            "missing_quote": "status=pending_quote + 行情字段 null"
                             "（默认不写；include_pending 才落审计行）",
            "missing_price_or_trade": "status=invalid + errors，不写入",
        },
        "training_episode": {
            "schema": EPISODE_SCHEMA,
            "id_format": EPISODE_ID_FORMAT,
            "id_generator":
                "training/outcome_chain.py::new_training_episode_id",
            "required_fields": list(_EPISODE_REQUIRED),
            "ids_keys": list(_EPISODE_IDS_KEYS),
            "metrics_keys": list(_METRIC_KEYS),
            "null_metrics": "分母 0 → null（不造 0）",
            "read_only": True,
            "auto_training": False,
        },
        "write_boundary": {
            "default_write": False,
            "explicit_path_required": True,
            "writes_production_by_default": False,
            "migrates_books": False,
            "real_trading": False,
        },
        "forbidden_exec_keys": list(FORBIDDEN_EXEC_KEYS),
        "paper_only": True,
        "trading_permitted": False,
    }


# ---------------------------------------------------------------------------
# CLI（全部只读/显式写；不启服务、不自动训练）
# ---------------------------------------------------------------------------
def _json_arg(text, name):
    if text in (None, ""):
        return None
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise OutcomeChainError("%s 须为 JSON: %s" % (name, exc)) from exc
    return value


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="IFS F5 outcome / training_episode 只读链（不自动训练）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("contract", help="打印 ID/契约/缺省语义")

    p_backfill = sub.add_parser("backfill", help="receipt → outcome（T+1）")
    p_backfill.add_argument("--receipts", required=True)
    p_backfill.add_argument("--quotes", default=None,
                            help="行情 JSON（缺省=全部 pending_quote，不伪造）")
    p_backfill.add_argument("--out", default=None)
    p_backfill.add_argument("--include-pending", action="store_true")
    p_backfill.add_argument("--no-write", action="store_true")
    p_backfill.add_argument("--horizon", type=int,
                            default=DEFAULT_HORIZON_DAYS)
    p_backfill.add_argument("--json", action="store_true")

    p_episode = sub.add_parser("episode", help="receipt+outcome → training_episode")
    p_episode.add_argument("--receipts", required=True)
    p_episode.add_argument("--outcomes", required=True)
    p_episode.add_argument("--out", default=None,
                           help="episode json 输出（显式路径；缺省只打印）")
    p_episode.add_argument("--episodes-ledger", default=None,
                           help="episodes.jsonl 显式路径（append 幂等）")
    p_episode.add_argument("--combo-id", default=None)
    p_episode.add_argument("--as-of", default=None)
    p_episode.add_argument("--data-snapshot", default=None,
                           help="data_snapshot JSON（缺省=显式 unspecified）")
    p_episode.add_argument("--data-snapshot-file", default=None,
                           help="data_snapshot JSON 文件（避免 shell 引号问题）")
    p_episode.add_argument("--json", action="store_true")

    p_ids = sub.add_parser("backfill-ids",
                           help="legacy outcome 行补 outcome_id（只读回填）")
    p_ids.add_argument("--legacy-outcomes", required=True)
    p_ids.add_argument("--out", default=None)
    p_ids.add_argument("--no-write", action="store_true")
    p_ids.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "contract":
        _print(contract())
        return 0
    if args.command == "backfill":
        receipts = load_receipts(args.receipts)
        quotes = load_quotes(args.quotes) if args.quotes else None
        report = backfill_outcomes(
            receipts, quotes, path=args.out,
            write=bool(args.out) and not args.no_write,
            include_pending=args.include_pending,
            horizon_days=args.horizon)
        if args.json:
            _print(report)
        else:
            print("[outcome] receipts=%d complete=%d pending=%d invalid=%d "
                  "skipped=%d written=%d%s"
                  % (report["receipts"], report["complete"], report["pending"],
                     report["invalid"], report["skipped"], report["written"],
                     "" if report["out_path"] else "（未指定 --out，零写入）"))
        return 0
    if args.command == "episode":
        receipts = load_receipts(args.receipts)
        outcomes = load_outcomes(args.outcomes)
        snapshot = None
        if args.data_snapshot:
            snapshot = _json_arg(args.data_snapshot, "--data-snapshot")
        elif args.data_snapshot_file:
            snapshot = load_json_file(args.data_snapshot_file)
            if not isinstance(snapshot, dict):
                raise OutcomeChainError(
                    "--data-snapshot-file 须为 JSON object（缺失/坏 JSON 不造数）")
        episode = build_training_episode(
            receipts=receipts, outcomes=outcomes,
            combo_id=args.combo_id,
            data_snapshot=snapshot,
            as_of=args.as_of)
        violations = validate_episode(episode)
        payload = {"ok": not violations, "violations": violations,
                   "episode": episode}
        if args.out:
            write_episode_json(args.out, episode)
        if args.episodes_ledger:
            append_episode(args.episodes_ledger, episode)
        if args.json:
            _print(payload)
        else:
            print("[episode] %s trades=%d outcomes=%d errors=%d "
                  "contract=%s%s"
                  % (episode["training_episode_id"],
                     episode["metrics"]["trades"],
                     episode["metrics"]["outcomes"],
                     len(episode["errors"]),
                     "OK" if not violations else ";".join(violations[:3]),
                     "" if args.out else "（未指定 --out，episode 未落盘）"))
        return 0 if not violations else 1
    receipts = load_outcomes(args.legacy_outcomes)
    backfilled = backfill_outcome_ids(receipts)
    if args.out and not args.no_write:
        _atomic_jsonl_write(args.out, backfilled["rows"])
    if args.json:
        _print({"report": backfilled["report"],
                "rows": backfilled["rows"]
                if not args.out or args.no_write else [],
                "out_path": args.out,
                "written": bool(args.out) and not args.no_write})
    else:
        print("[outcome-ids] rows=%d filled=%d already=%d unavailable=%d%s"
              % (backfilled["report"]["rows"],
                 backfilled["report"]["filled"],
                 backfilled["report"]["already"],
                 backfilled["report"]["unavailable"],
                 "" if (args.out and not args.no_write)
                 else "（零写入）"))
    return 0


__all__ = [
    "SCHEMA_VERSION",
    "OUTCOME_SCHEMA",
    "EPISODE_SCHEMA",
    "CONTRACT_VERSION",
    "OUTCOME_ID_FORMAT",
    "EPISODE_ID_FORMAT",
    "SIX_ID_KEYS",
    "UPSTREAM_ID_KEYS",
    "STATUS_COMPLETE",
    "STATUS_PENDING_QUOTE",
    "STATUS_INVALID",
    "OUTCOME_STATUSES",
    "FORBIDDEN_EXEC_KEYS",
    "DEFAULT_HORIZON_DAYS",
    "OutcomeChainError",
    "read_jsonl",
    "load_json_file",
    "record_ids",
    "six_ids",
    "new_outcome_id",
    "new_training_episode_id",
    "make_quote_lookup",
    "load_quotes",
    "build_outcome",
    "generate_outcomes",
    "backfill_outcomes",
    "append_outcomes",
    "backfill_outcome_ids",
    "write_outcome_id_backfill",
    "build_training_episode",
    "validate_episode",
    "write_episode_json",
    "append_episode",
    "load_receipts",
    "load_outcomes",
    "contract",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
