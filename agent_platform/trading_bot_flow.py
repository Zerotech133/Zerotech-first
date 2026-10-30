# -*- coding: utf-8 -*-
"""交易机器人：盯盘触发 → 人工确认 → TR1 模拟执行（2026-10-30 WO-BOT-CONFIRM）。

paper-only；人工确认是硬门：
- 盯盘/上游只创建 pending confirmation，不直接下单；
- 人工 approve 后才走 risk_gate + paper_book（模拟账）；
- reject/expire 不产生执行记录；
- kill switch 生效时一律不执行；
- 所有动作写 append-only audit.jsonl。

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

PROFILES = {
    "aggressive": "激进",
    "balanced": "平衡",
    "conservative": "保守",
}

try:
    from .paper import paper_book, risk_gate
except ImportError:  # 直接按路径加载/脚本运行
    import sys
    sys.path.insert(0, os.path.dirname(BASE))
    from agent_platform.paper import paper_book, risk_gate  # noqa: E402


def _dirs(data_dir=None):
    root = data_dir or os.environ.get("TRADING_BOT_DIR") or DATA_DIR
    return root, os.path.join(root, "confirmations"), os.path.join(root, "cards")


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _audit(event, data_dir=None):
    _, _, _ = _dirs(data_dir)
    os.makedirs(os.path.dirname(AUDIT if not data_dir else os.path.join(data_dir, "audit.jsonl")),
                exist_ok=True)
    p = AUDIT if not data_dir else os.path.join(data_dir, "audit.jsonl")
    with io.open(p, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _confirm_path(cid, data_dir=None):
    _, cdir, _ = _dirs(data_dir)
    return os.path.join(cdir, str(cid) + ".json")


def _load(cid, data_dir=None):
    with io.open(_confirm_path(cid, data_dir), encoding="utf-8") as f:
        return json.load(f)


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


def create_confirmation(symbol, name="", action="BUY", price=None, stop=None,
                        target=None, confidence=0.6, reason="", source="WA1",
                        source_report_id="", profile="balanced", audience="external_user", data_dir=None):
    """盯盘触发→pending 人工确认；幂等：同 report_id 返回已有记录。

    profile：组合风格（aggressive/balanced/conservative），由人确认时最终定；
    paper 阶段只记录人的选择，不改变风控阈值（风控仍由 risk_gate 统一）。
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
            return old
    cid = "CFM-" + uuid.uuid4().hex[:12].upper()
    rec = {
        "schema": "trading_bot_confirmation/v1",
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
           paper_config_path=None, data_dir=None, mode="manual"):
    """裁决；manual=人工，auto=内部策略自动（仅 internal audience）。"""
    decision = str(decision or "").lower()
    if decision not in ("approve", "reject"):
        raise ValueError("decision 必须是 approve/reject")
    rec = _load(confirmation_id, data_dir)
    if rec.get("status") != "pending":
        return rec  # 幂等：已裁决不再重复执行
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
    if decision == "reject":
        rec.update({"status": "rejected", "decision": "reject", "decided_by": by,
                    "decided_at": _now(), "note": note,
                    "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_rejected", "confirmation_id": confirmation_id,
                "by": by, "mode": "manual", "at": rec["decided_at"]}, data_dir)
        return rec

    if paper_book.kill_switch_on():
        rec.update({"status": "killed", "decision": decision_value, "decided_by": by,
                    "decided_at": _now(), "note": note or "KILL switch on",
                    "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_killed", "confirmation_id": confirmation_id,
                "by": by, "mode": "auto" if auto else "manual",
                "at": rec["decided_at"]}, data_dir)
        return rec

    px = float(price) if price is not None else rec.get("price")
    if px is None:
        rec.update({"status": "blocked", "decision": decision_value, "decided_by": by,
                    "decided_at": _now(), "note": "价格缺失，无法模拟执行",
                    "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
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
                    "trading_permitted": False})
        _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
        _audit({"event": "confirmation_blocked", "confirmation_id": confirmation_id,
                "by": by, "reason": gate.get("reasons"), "at": rec["decided_at"]}, data_dir)
        return rec

    day = date.today().isoformat()
    ledger = paper_ledger_path
    seen = None if ledger is None else paper_book.existing_keys(ledger)
    result = paper_book.apply_decision(d, gate, state, day, ledger_path=ledger, seen=seen)
    if not result.get("duplicate"):
        paper_book.save_state(state, paper_state_path)
    rec.update({"status": "executed", "decision": decision_value, "decided_by": by,
                "decided_at": _now(), "note": note, "gate": gate,
                "execution": {"date": day, "duplicate": bool(result.get("duplicate")),
                              "ledger": result.get("record"),
                              "paper_ledger": ledger or paper_book.DEFAULT_LEDGER},
                "trading_permitted": False})
    _atomic_json(_confirm_path(confirmation_id, data_dir), rec)
    _audit({"event": "confirmation_executed", "confirmation_id": confirmation_id,
            "by": by, "mode": "auto" if auto else "manual",
            "symbol": rec.get("symbol"), "action": action,
            "duplicate": bool(result.get("duplicate")), "at": rec["decided_at"]}, data_dir)
    return rec



def auto_execute(confirmation_id, by="SYSTEM:TR1", note="内部策略自动执行", **kwargs):
    """内部策略路径：不走人工门，直接风控+paper_book；仅 internal 确认。"""
    return decide(confirmation_id, "approve", by=by, note=note, mode="auto", **kwargs)


def trigger_from_watch(report, **kwargs):
    """从 QUANT_UPSTREAM_REPORT/盯盘事件建确认；只接受显式 watch 触发的 symbol/action。"""
    symbol = str(report.get("symbol") or report.get("stock") or "")
    if not symbol:
        raise ValueError("watch report 缺 symbol")
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
