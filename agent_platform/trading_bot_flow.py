# -*- coding: utf-8 -*-
"""交易机器人：盯盘触发 → 人工确认 → TR1 模拟执行（2026-10-30 WO-BOT-CONFIRM）。

paper-only；人工确认是硬门：
- 盯盘/上游只创建 pending confirmation，不直接下单；
- 人工 approve 后才走 risk_gate + paper_book（模拟账）；
- reject/expire 不产生执行记录；
- kill switch 生效时一律不执行；
- 所有动作写 append-only audit.jsonl。

IFS v1 F4（2026-11-03 WO-IFS-PAPER-20261103）：确认→paper 的**唯一 A 路径**。
``create_confirmation`` 支持 ``source_decision_id/source_handoff_id/
source_watch_plan_id``（schema ``trading_bot_confirmation/v2``，旧 v1 记录自动兼容）；
执行成功由 ``agent_platform.paper.trade_receipt`` 落 trade receipt（``trade_id`` +
四 ID 链回写），同 ``confirmation_id`` / ``source_report_id`` 幂等不重复执行。
legacy B/C 入口（``/api/paper_order``、``/api/paper_direct_open``）口径见
``agent_platform.paper.entry_policy``：不接 D1、不产生四 ID 链。

数据目录默认 agent_platform/data/trading_bot；测试用 TRADING_BOT_DIR 覆盖。
"""
from __future__ import annotations

import io
import json
import os
import uuid
from datetime import date, datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data", "trading_bot")
CONFIRM_DIR = os.path.join(DATA_DIR, "confirmations")
CARD_DIR = os.path.join(DATA_DIR, "cards")
AUDIT = os.path.join(DATA_DIR, "audit.jsonl")

#: confirmation schema：v2 增加 source_decision_id/source_handoff_id/
#: source_watch_plan_id/trade_id/entry_path；旧 v1 记录读入时归一化兼容。
CONFIRMATION_SCHEMA_V1 = "trading_bot_confirmation/v1"
CONFIRMATION_SCHEMA = "trading_bot_confirmation/v2"
SCHEMA_VERSION = 2

PROFILES = {
    "aggressive": "激进",
    "balanced": "平衡",
    "conservative": "保守",
}

try:
    from .paper import entry_policy, paper_book, risk_gate, trade_receipt
except ImportError:  # 直接按路径加载/脚本运行
    import sys
    sys.path.insert(0, os.path.dirname(BASE))
    from agent_platform.paper import (  # noqa: E402
        entry_policy, paper_book, risk_gate, trade_receipt,
    )

#: 唯一 A 路径标识（与 entry_policy 单一来源，写入 confirmation/receipt）。
ENTRY_PATH = entry_policy.UNIQUE_A_PATH


def _dirs(data_dir=None):
    root = data_dir or os.environ.get("TRADING_BOT_DIR") or DATA_DIR
    return root, os.path.join(root, "confirmations"), os.path.join(root, "cards")


def _receipts_path(data_dir=None, receipts_path=None):
    """回执日志：显式 receipts_path > data_dir/receipts.jsonl > 默认。"""
    if receipts_path:
        return receipts_path
    return trade_receipt.receipts_path(data_dir)


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _audit(event, data_dir=None):
    # 审计日志与 confirmation/receipt 同一数据根（尊重 TRADING_BOT_DIR 测试覆盖，
    # 避免旧实现固定写模块级 AUDIT 而绕过 env 隔离）。
    root, _, _ = _dirs(data_dir)
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, "audit.jsonl")
    with io.open(p, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _confirm_path(cid, data_dir=None):
    _, cdir, _ = _dirs(data_dir)
    return os.path.join(cdir, str(cid) + ".json")


def _load(cid, data_dir=None):
    with io.open(_confirm_path(cid, data_dir), encoding="utf-8") as f:
        return _normalize_record(json.load(f))


def _find_by_report(report_id, data_dir=None):
    _, cdir, _ = _dirs(data_dir)
    if not os.path.isdir(cdir):
        return None
    for name in sorted(os.listdir(cdir)):
        if not name.endswith(".json"):
            continue
        try:
            d = _load(name[:-5], data_dir)
        except (OSError, ValueError):
            continue
        if d.get("source_report_id") == report_id:
            return d
    return None


def _normalize_record(rec):
    """旧 v1 记录归一化为 v2 内存视图（缺的 source_* / trade_id 补空，不造数）。"""
    if not isinstance(rec, dict):
        return rec
    for key in ("source_decision_id", "source_handoff_id",
                "source_watch_plan_id"):
        if not isinstance(rec.get(key), str):
            rec[key] = "" if rec.get(key) is None else str(rec.get(key) or "")
    if rec.get("schema") != CONFIRMATION_SCHEMA:
        # 旧 v1（或缺失 schema）→ 升 v2；旧字段一律保留。
        rec["schema"] = CONFIRMATION_SCHEMA
    rec["schema_version"] = SCHEMA_VERSION
    rec.setdefault("trade_id", None)
    rec.setdefault("entry_path", ENTRY_PATH)
    rec.setdefault("paper_only", True)
    return rec


def _clip_id(value, limit=120):
    return str(value or "").strip()[:limit]


def _apply_source_ids(rec, source_decision_id=None, source_handoff_id=None,
                      source_watch_plan_id=None):
    """写入/校验四 ID 上游字段：已有值不可被不同值覆盖（closed-on-failure）。"""
    wanted = {
        "source_decision_id": source_decision_id,
        "source_handoff_id": source_handoff_id,
        "source_watch_plan_id": source_watch_plan_id,
    }
    for key, value in wanted.items():
        value = _clip_id(value)
        if not value:
            continue
        old = _clip_id(rec.get(key))
        if old and old != value:
            raise ValueError(
                "source id 冲突：%s 已记录 %r，拒绝改写为 %r（不猜、不覆盖上游链）"
                % (key, old, value))
        rec[key] = value
    return rec


def four_ids(record):
    """四 ID 链（decision/handoff/watch_plan/confirmation）；缺失 → null。"""
    record = record if isinstance(record, dict) else {}
    return {
        "decision_id": _clip_id(record.get("source_decision_id")) or None,
        "handoff_id": _clip_id(record.get("source_handoff_id")) or None,
        "watch_plan_id": _clip_id(record.get("source_watch_plan_id")) or None,
        "confirmation_id": _clip_id(record.get("confirmation_id")) or None,
    }


def id_chain(record):
    """五 ID 链（四 ID + trade_id），全部显式（缺失 null）。"""
    chain = four_ids(record)
    chain["trade_id"] = _clip_id(
        record.get("trade_id") if isinstance(record, dict) else None) or None
    return chain


def get_confirmation(confirmation_id, data_dir=None):
    """读单条 confirmation（v1 记录归一化为 v2 视图；缺失抛 FileNotFoundError）。"""
    return _normalize_record(_load(confirmation_id, data_dir))


def receipt_summary(data_dir=None, receipts_path=None):
    """回执摘要（只读，供 API/审计）。"""
    return trade_receipt.summary(
        _receipts_path(data_dir, receipts_path))


def create_confirmation(symbol, name="", action="BUY", price=None, stop=None,
                        target=None, confidence=0.6, reason="", source="WA1",
                        source_report_id="", profile="balanced",
                        audience="external_user", data_dir=None, *,
                        source_decision_id="", source_handoff_id="",
                        source_watch_plan_id=""):
    """盯盘触发→pending 人工确认；幂等：同 report_id 返回已有记录。

    profile：组合风格（aggressive/balanced/conservative），由人确认时最终定；
    paper 阶段只记录人的选择，不改变风控阈值（风控仍由 risk_gate 统一）。
    IFS v1 F4：source_decision_id/source_handoff_id/source_watch_plan_id 记录
    D1→盯盘→确认的四 ID 上游链（schema v2；旧 v1 字段全部保留）。
    """
    action = str(action or "BUY").upper()
    if action not in ("BUY", "SELL", "WATCH"):
        raise ValueError("action 必须是 BUY/SELL/WATCH")
    profile = str(profile or "balanced").lower()
    if profile not in PROFILES:
        raise ValueError("profile 必须是 %s" % "/".join(PROFILES))
    audience = str(audience or "external_user")
    if audience not in ("internal", "external_user"):
        raise ValueError("audience 必须是 internal/external_user")
    if source_report_id:
        old = _find_by_report(source_report_id, data_dir)
        if old:
            return _normalize_record(old)
    cid = "CFM-" + uuid.uuid4().hex[:12].upper()
    rec = {
        "schema": CONFIRMATION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "confirmation_id": cid,
        "status": "pending",
        "audience": audience,
        "human_gate": audience != "internal",
        "symbol": str(symbol),
        "name": name or "",
        "action": action,
        "price": float(price) if price is not None else None,
        "stop": float(stop) if stop is not None else None,
        "target": float(target) if target is not None else None,
        "confidence": float(confidence) if confidence is not None else 0.0,
        "profile": profile,
        "profile_label": PROFILES[profile],
        "reason": reason or "",
        "source": source or "WA1",
        "source_report_id": source_report_id or "",
        # IFS v1 F4：四 ID 上游链（缺失为空串，执行时 receipt 里为 null）
        "source_decision_id": _clip_id(source_decision_id),
        "source_handoff_id": _clip_id(source_handoff_id),
        "source_watch_plan_id": _clip_id(source_watch_plan_id),
        "trade_id": None,
        "entry_path": ENTRY_PATH,
        "paper_only": True,
        "created_at": _now(),
        "decided_at": None,
        "decided_by": None,
        "decision": None,
        "execution": None,
        "trading_permitted": False,
        "mode": "paper",
    }
    _atomic_json(_confirm_path(cid, data_dir), rec)
    _audit({"event": "confirmation_created", "confirmation_id": cid,
            "symbol": rec["symbol"], "action": action, "audience": audience,
            "entry_path": ENTRY_PATH,
            "ids": four_ids(rec),
            "at": rec["created_at"]},
           data_dir)
    return rec


def list_confirmations(status=None, data_dir=None):
    _, cdir, _ = _dirs(data_dir)
    out = []
    if not os.path.isdir(cdir):
        return out
    for name in sorted(os.listdir(cdir)):
        if not name.endswith(".json"):
            continue
        try:
            d = _load(name[:-5], data_dir)
        except (OSError, ValueError):
            continue
        if status and d.get("status") != status:
            continue
        out.append(d)
    return out


def build_feishu_card(rec):
    """生成飞书交互卡 payload（不发送；发送由既有飞书通道/人工确认后接线）。"""
    cid = rec["confirmation_id"]
    action = rec.get("action")
    lines = [
        "标的：%s %s" % (rec.get("symbol"), rec.get("name") or ""),
        "动作：**%s**" % action,
        "价格：%s" % (rec.get("price") if rec.get("price") is not None else "市价/待定"),
        "止损：%s" % (rec.get("stop") if rec.get("stop") is not None else "未填"),
        "组合风格：**%s**（确认时可改）" % rec.get("profile_label", "平衡"),
        "理由：%s" % (rec.get("reason") or "—"),
        "来源：%s %s" % (rec.get("source") or "—", rec.get("source_report_id") or ""),
        "确认号：%s" % cid,
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange",
                   "title": {"tag": "plain_text", "content": "模拟交易人工确认（paper）"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {"tag": "note",
             "elements": [{"tag": "plain_text",
                           "content": "paper-only，不接实盘；超时不自动下单。"}]},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "批准"},
                 "type": "primary", "value": {"action": "approve", "confirmation_id": cid}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "拒绝"},
                 "type": "danger", "value": {"action": "reject", "confirmation_id": cid}},
            ]},
        ],
    }


def save_card(rec, data_dir=None):
    _, _, card_dir = _dirs(data_dir)
    os.makedirs(card_dir, exist_ok=True)
    p = os.path.join(card_dir, rec["confirmation_id"] + ".json")
    _atomic_json(p, build_feishu_card(rec))
    return p


def decide(confirmation_id, decision, by="老田", note="", price=None,
           profile=None, paper_state_path=None, paper_ledger_path=None,
           paper_config_path=None, data_dir=None, mode="manual", *,
           source_decision_id=None, source_handoff_id=None,
           source_watch_plan_id=None, receipts_path=None):
    """裁决；manual=人工，auto=内部策略自动（仅 internal audience）。

    IFS v1 F4：approve 执行成功 → paper_book 落账 + trade receipt（``trade_id``
    + 四 ID 链回写）；同 ``confirmation_id`` / ``source_report_id`` 幂等不重复
    执行；kill switch / 风控 block → 不执行、不出回执。
    """
    decision = str(decision or "").lower()
    if decision not in ("approve", "reject"):
        raise ValueError("decision 必须是 approve/reject")
    rec = _load(confirmation_id, data_dir)
    if rec.get("status") != "pending":
        return rec  # 幂等：已裁决不再重复执行（也不重复出回执）
    auto = str(mode or "manual").lower() == "auto"
    if auto:
        if rec.get("audience") != "internal":
            raise ValueError("内部自动执行仅限 internal 确认；外部测试需人工确认")
        if decision != "approve":
            raise ValueError("auto 模式只接受 approve")
        decision_value = "auto"
    else:
        decision_value = decision
    if profile:
        p = str(profile).lower()
        if p not in PROFILES:
            raise ValueError("profile 必须是 %s" % "/".join(PROFILES))
        rec["profile"] = p
        rec["profile_label"] = PROFILES[p]
    # 四 ID 上游链：可在此补齐；与已记录值冲突 → 拒绝（不猜、不覆盖）。
    _apply_source_ids(rec, source_decision_id, source_handoff_id,
                      source_watch_plan_id)
    receipt_log = _receipts_path(data_dir, receipts_path)
    if decision == "reject":
        rec.update({"status": "rejected", "decision": "reject", "decided_by": by,
                    "decided_at": _now(), "note": note,
                    "ids": id_chain(rec), "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_rejected", "confirmation_id": confirmation_id,
                "by": by, "mode": "manual", "ids": four_ids(rec),
                "at": rec["decided_at"]}, data_dir)
        return rec

    if paper_book.kill_switch_on():
        rec.update({"status": "killed", "decision": decision_value, "decided_by": by,
                    "decided_at": _now(), "note": note or "KILL switch on",
                    "ids": id_chain(rec), "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_killed", "confirmation_id": confirmation_id,
                "by": by, "mode": "auto" if auto else "manual",
                "ids": four_ids(rec), "at": rec["decided_at"]}, data_dir)
        return rec

    px = float(price) if price is not None else rec.get("price")
    if px is None:
        rec.update({"status": "blocked", "decision": decision_value, "decided_by": by,
                    "decided_at": _now(), "note": "价格缺失，无法模拟执行",
                    "ids": id_chain(rec), "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_blocked", "confirmation_id": confirmation_id,
                "by": by, "reason": "price_missing", "ids": four_ids(rec),
                "at": rec["decided_at"]}, data_dir)
        return rec

    # 幂等兜底（崩溃恢复）：已有回执 → 不重复执行，直接对账回执（trade_id 不变）。
    existing = trade_receipt.find_receipt(
        confirmation_id=confirmation_id,
        report_id=rec.get("source_report_id") or "",
        path=receipt_log)
    if existing is not None:
        rec.update({"status": "executed", "decision": decision_value,
                    "decided_by": by, "decided_at": _now(), "note": note,
                    "trade_id": existing.get("trade_id"),
                    "ids": trade_receipt.id_chain(existing),
                    "execution": {
                        "date": existing.get("date"),
                        "duplicate": True,
                        "trade_id": existing.get("trade_id"),
                        "receipt": existing,
                        "ids": trade_receipt.id_chain(existing),
                        "paper_ledger": paper_ledger_path
                        or paper_book.DEFAULT_LEDGER,
                    },
                    "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_reconciled",
                "confirmation_id": confirmation_id, "by": by,
                "trade_id": existing.get("trade_id"),
                "ids": trade_receipt.id_chain(existing),
                "at": rec["decided_at"]}, data_dir)
        return rec

    cfg = risk_gate.load_config(paper_config_path) if paper_config_path else risk_gate.load_config()
    state = paper_book.load_state(paper_state_path)
    action = rec.get("action")
    recommendation = {"BUY": "BUY", "SELL": "SELL", "WATCH": "WATCH"}.get(action, "WATCH")
    d = {
        "symbol": rec.get("symbol"), "name": rec.get("name"),
        "recommendation": recommendation, "confidence": rec.get("confidence") or 0.0,
        "price_at_decision": px, "ts": datetime.now().timestamp(),
        "status": "done", "parse_ok": True,
    }
    gate = risk_gate.evaluate(d, state=state, cfg=cfg)
    if gate.get("verdict") == "block":
        rec.update({"status": "blocked", "decision": decision_value, "decided_by": by,
                    "decided_at": _now(), "note": note, "gate": gate,
                    "ids": id_chain(rec), "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_blocked", "confirmation_id": confirmation_id,
                "by": by, "reason": gate.get("reasons"), "ids": four_ids(rec),
                "at": rec["decided_at"]}, data_dir)
        return rec

    day = date.today().isoformat()
    ledger = paper_ledger_path
    seen = None if ledger is None else paper_book.existing_keys(ledger)
    result = paper_book.apply_decision(d, gate, state, day, ledger_path=ledger, seen=seen)
    ledger_duplicate = bool(result.get("duplicate"))
    if not ledger_duplicate:
        paper_book.save_state(state, paper_state_path)
    receipt = None
    receipt_duplicate = False
    record = result.get("record")
    if not ledger_duplicate and isinstance(record, dict):
        candidate = trade_receipt.build_receipt(
            rec, dict(record, _ledger_path=str(ledger or paper_book.DEFAULT_LEDGER)),
            gate, entry_path=ENTRY_PATH)
        stored = trade_receipt.write_receipt(
            candidate, path=receipt_log,
            receipt_dir=os.path.join(_dirs(data_dir)[0], "receipts"))
        receipt = stored["receipt"]
        receipt_duplicate = bool(stored["duplicate"])
        rec["trade_id"] = receipt.get("trade_id")
    execution = {
        "date": day,
        "duplicate": ledger_duplicate or receipt_duplicate,
        "trade_id": rec.get("trade_id"),
        "receipt": receipt,
        "ids": id_chain(rec),
        "ledger": record,
        "paper_ledger": ledger or paper_book.DEFAULT_LEDGER,
    }
    rec.update({"status": "executed", "decision": decision_value, "decided_by": by,
                "decided_at": _now(), "note": note, "gate": gate,
                "ids": id_chain(rec),
                "execution": execution, "trading_permitted": False})
    _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
    _audit({"event": "confirmation_executed", "confirmation_id": confirmation_id,
            "by": by, "mode": "auto" if auto else "manual",
            "symbol": rec.get("symbol"), "action": action,
            "trade_id": rec.get("trade_id"), "ids": four_ids(rec),
            "duplicate": execution["duplicate"], "at": rec["decided_at"]}, data_dir)
    return rec



def auto_execute(confirmation_id, by="SYSTEM:TR1", note="内部策略自动执行", **kwargs):
    """内部策略路径：不走人工门，直接风控+paper_book；仅 internal 确认。"""
    return decide(confirmation_id, "approve", by=by, note=note, mode="auto", **kwargs)


def trigger_from_watch(report, **kwargs):
    """从 QUANT_UPSTREAM_REPORT/盯盘事件建确认；只接受显式 watch 触发的 symbol/action。

    IFS v1 F4：report 里若带 D1 交棒链 ID（``decision_id`` / ``handoff_id`` /
    ``watch_plan_id`` 或 ``source_*`` 形式），自动写入 confirmation v2 四 ID 链；
    调用方显式 kwargs 优先（不覆盖显式值）。
    """
    symbol = str(report.get("symbol") or report.get("stock") or "")
    if not symbol:
        raise ValueError("watch report 缺 symbol")
    ids = {
        "source_decision_id": report.get("source_decision_id")
        or report.get("decision_id") or "",
        "source_handoff_id": report.get("source_handoff_id")
        or report.get("handoff_id") or report.get("receipt_id") or "",
        "source_watch_plan_id": report.get("source_watch_plan_id")
        or report.get("watch_plan_id") or report.get("plan_id") or "",
    }
    for key, value in ids.items():
        kwargs.setdefault(key, value)
    return create_confirmation(
        symbol=symbol,
        name=report.get("name") or "",
        action=report.get("action") or report.get("side") or "BUY",
        price=report.get("price"),
        stop=report.get("stop"),
        target=report.get("target"),
        confidence=report.get("confidence") or 0.6,
        reason=report.get("reason") or report.get("event") or "盯盘触发",
        source=report.get("source") or "WA1",
        source_report_id=report.get("report_id") or report.get("event_id") or "",
        **kwargs,
    )
