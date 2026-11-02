# -*- coding: utf-8 -*-
"""Paper 交易回执（trade receipt，IFS v1 F4；WO-IFS-PAPER-20261103）。

只在 A 路径（``trading_bot_flow`` + ``/api/sim/confirm``）模拟执行成功后落一张
回执：``trade_id`` + 四 ID 链（``decision_id`` / ``handoff_id`` /
``watch_plan_id`` / ``confirmation_id``）回写，供对账与审计。

边界（本单硬约束）：
- **不写真实 paper ledger**：本模块只写调用方显式传入的 ``receipts_path``
  （生产默认 ``agent_platform/data/trading_bot/receipts.jsonl``，测试必须传
  临时目录）；账本写入仍由 ``paper_book`` 在 ``trading_bot_flow`` 内完成。
- 不下单、不接实盘、不启服务、不迁移两账；``paper_only=true`` /
  ``trading_permitted=false`` 固定。
- 幂等：同一 ``confirmation_id`` 或 ``source_report_id`` 已有回执 → 返回已有
  receipt（``duplicate=true``），绝不重复铸造 ``trade_id``。
- 缺失的上游 ID 一律 null（不造数），四 ID 链允许部分缺失但**回执本身**必有
  ``confirmation_id`` / ``trade_id``。
"""

from __future__ import annotations

import io
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

try:  # 包内导入
    from . import entry_policy
except ImportError:  # 直接按路径加载
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from agent_platform.paper import entry_policy  # noqa: E402

SCHEMA = "paper_trade_receipt/v1"

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(os.path.dirname(BASE), "data", "trading_bot")
DEFAULT_RECEIPTS = os.path.join(DEFAULT_ROOT, "receipts.jsonl")
RECEIPT_DIRNAME = "receipts"

#: 四 ID 链字段（decision → handoff → watch_plan → confirmation）。
FOUR_ID_KEYS = ("decision_id", "handoff_id", "watch_plan_id", "confirmation_id")


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_trade_id(now=None):
    """生成全局唯一 trade_id：``pt-<YYYYMMDDHHMMSS>-<6hex>``。"""
    dt = now or datetime.now()
    prefix = "pt-" + dt.strftime("%Y%m%d%H%M%S") + "-"
    return prefix + secrets.token_hex(3)


def receipts_root(data_dir=None):
    """回执根目录：调用方 data_dir 优先，其次 env TRADING_BOT_DIR，最后默认。"""
    root = data_dir or os.environ.get("TRADING_BOT_DIR") or DEFAULT_ROOT
    return str(root)


def receipts_path(data_dir=None):
    """append-only 回执日志路径（jsonl）。"""
    return os.path.join(receipts_root(data_dir), "receipts.jsonl")


def _receipt_file(trade_id, data_dir=None):
    return os.path.join(receipts_root(data_dir), RECEIPT_DIRNAME,
                        str(trade_id) + ".json")


def _clip(value, limit=120):
    return str(value or "").strip()[:limit]


def _f(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _id_or_none(value):
    text = _clip(value, 120)
    return text or None


def four_ids(source):
    """从 confirmation / receipt / 任意 dict 取四 ID（缺失 → None，不造数）。"""
    source = source if isinstance(source, dict) else {}
    return {
        "decision_id": _id_or_none(source.get("decision_id")
                                   or source.get("source_decision_id")),
        "handoff_id": _id_or_none(source.get("handoff_id")
                                  or source.get("source_handoff_id")),
        "watch_plan_id": _id_or_none(source.get("watch_plan_id")
                                     or source.get("source_watch_plan_id")),
        "confirmation_id": _id_or_none(source.get("confirmation_id")),
    }


def id_chain(receipt):
    """五 ID 链（四 ID + trade_id），全部显式（缺失 null）。"""
    chain = four_ids(receipt)
    chain["trade_id"] = _id_or_none(receipt.get("trade_id")
                                    if isinstance(receipt, dict) else None)
    return chain


def build_receipt(confirmation, ledger_record, gate=None, *, trade_id=None,
                  entry_path=None, now=None):
    """由 confirmation + 已落账的 ledger record 构造回执（纯函数，不写盘）。"""
    if not isinstance(confirmation, dict):
        raise ValueError("confirmation 须为 dict")
    if not isinstance(ledger_record, dict):
        raise ValueError("ledger_record 须为 dict（paper_book 已落账记录）")
    confirmation_id = _clip(confirmation.get("confirmation_id"), 120)
    if not confirmation_id:
        raise ValueError("confirmation 缺 confirmation_id，拒绝出回执")
    ids = four_ids(confirmation)
    chain = dict(ids)
    chain["trade_id"] = _id_or_none(trade_id) or new_trade_id(now)
    gate = gate if isinstance(gate, dict) else {}
    receipt = {
        "schema": SCHEMA,
        "trade_id": chain["trade_id"],
        "confirmation_id": confirmation_id,
        "source_report_id": _clip(confirmation.get("source_report_id"), 120),
        # 四 ID 链（decision/handoff/watch_plan/confirmation）
        "decision_id": ids["decision_id"],
        "handoff_id": ids["handoff_id"],
        "watch_plan_id": ids["watch_plan_id"],
        "ids": chain,
        # 账本事实（只读引用，不重算）
        "symbol": _clip(ledger_record.get("symbol"), 32),
        "name": _clip(ledger_record.get("name") or confirmation.get("name"), 64),
        "action": _clip(ledger_record.get("action"), 16),
        "date": _clip(ledger_record.get("date"), 16),
        "price": _f(ledger_record.get("price")),
        "target_weight": _f(ledger_record.get("target_weight")),
        "weight_before": _f(ledger_record.get("weight_before")),
        "weight_after": _f(ledger_record.get("weight_after")),
        "nav": _f(ledger_record.get("nav")),
        "verdict": _clip(gate.get("verdict") or ledger_record.get("verdict"), 16),
        "risk_level": _clip(gate.get("risk_level")
                            or ledger_record.get("risk_level"), 16),
        "ledger_hash": _clip(ledger_record.get("hash"), 128),
        "ledger_prev_hash": _clip(ledger_record.get("prev_hash"), 128),
        "ledger_path": _clip(ledger_record.get("_ledger_path")
                             or ledger_record.get("ledger_path"), 260),
        "entry_path": _clip(entry_path or entry_policy.UNIQUE_A_PATH, 120),
        "mode": "paper",
        "paper_only": True,
        "trading_permitted": False,
        "not_a_trade": True,
        "created_at": _now(),
    }
    return receipt


def read_receipts(path=None):
    """只读回执日志（缺失/坏行 → 跳过，不造数）。"""
    target = Path(path or DEFAULT_RECEIPTS)
    rows = []
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
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


def find_receipt(*, confirmation_id="", report_id="", trade_id="", path=None):
    """按 confirmation_id / source_report_id / trade_id 查已有回执。"""
    keys = {
        "confirmation_id": _clip(confirmation_id, 120),
        "source_report_id": _clip(report_id, 120),
        "trade_id": _clip(trade_id, 120),
    }
    for row in read_receipts(path):
        for key, wanted in keys.items():
            if wanted and row.get(key) == wanted:
                return row
    return None


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_receipt(receipt, path=None, receipt_dir=None):
    """幂等落回执：同 confirmation_id/report_id 已有 → 返回已有（duplicate）。

    返回 ``{"receipt": dict, "duplicate": bool, "path": str}``。
    """
    if not isinstance(receipt, dict):
        raise ValueError("receipt 须为 dict")
    confirmation_id = _clip(receipt.get("confirmation_id"), 120)
    trade_id = _clip(receipt.get("trade_id"), 120)
    if not confirmation_id or not trade_id:
        raise ValueError("receipt 缺 confirmation_id/trade_id，拒绝落盘")
    target = path or DEFAULT_RECEIPTS
    existing = find_receipt(confirmation_id=confirmation_id,
                            report_id=receipt.get("source_report_id") or "",
                            path=target)
    if existing is not None:
        return {"receipt": existing, "duplicate": True, "path": target}
    missing = [key for key in ("trade_id", "confirmation_id") if not receipt.get(key)]
    if missing:
        raise ValueError("receipt 缺必填字段: %s" % ",".join(missing))
    line = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with io.open(target, "a", encoding="utf-8", newline="") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    receipt_dir = receipt_dir or os.path.join(
        os.path.dirname(os.path.abspath(target)), RECEIPT_DIRNAME)
    _atomic_json(os.path.join(receipt_dir, trade_id + ".json"), receipt)
    return {"receipt": receipt, "duplicate": False, "path": target}


def summary(path=None):
    """只读回执摘要（供 API/审计；不写盘）。"""
    rows = read_receipts(path)
    return {
        "schema": "paper_trade_receipt_summary/v1",
        "total": len(rows),
        "latest_trade_id": rows[-1].get("trade_id") if rows else None,
        "paper_only": True,
        "trading_permitted": False,
    }


__all__ = [
    "SCHEMA",
    "BASE",
    "DEFAULT_ROOT",
    "DEFAULT_RECEIPTS",
    "RECEIPT_DIRNAME",
    "FOUR_ID_KEYS",
    "new_trade_id",
    "receipts_root",
    "receipts_path",
    "four_ids",
    "id_chain",
    "build_receipt",
    "read_receipts",
    "find_receipt",
    "write_receipt",
    "summary",
]
