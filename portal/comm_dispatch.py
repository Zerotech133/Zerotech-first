# -*- coding: utf-8 -*-
"""阚泽部 · 派单通道（K5.1，WO-FS-148-7）。

一句话：把「已转盯盘官，预计 5 分钟」从话术变成真投递。

通道复用（不重造）
----------------
投递走军团既有 `agent_platform/gateway/inbox_gateway.py`（WO-LEGION-S21
门面）：`submit(kind, payload, source, idempotency_key)` 落
`bridge_inbox/inbox/<id>.json`，军团侧按既有消费模式处理（田丰归口→
工位执行），处理完移入 `processed/`。回执查 `receipt(id)`。

kind 映射（用门面自带词表，不新增）
--------------------------------
    stock_consult / watch_setup → task_request（派单执行）
    complaint                   → feedback（投诉，田丰归口最高优先级）
    其余（smalltalk/unclear/…） → question（咨询位/澄清）

幂等
----
idempotency_key = 飞书 msg_id（同一条消息只投一次，飞书重推不产生双工单）。
无 msg_id 时退化为 trace_id（调试路径）。

超时升级（K5.1 设计 §2.3）
-----------------------
`sweep_overdue()` 扫 inbox 中超过 deadline_min 未处理的条目 →
调用方（bot 侧定时或下一条消息触发）发「稍等」卡；15 分钟再升级田丰。
本模块只提供判定，不自己发卡（发送仍由调用方持权，便于测试与降级）。

开关
----
`COMM_DISPATCH_ENABLED` 默认 0：关时 dispatch 返回 ok=False/reason=disabled，
调用方回 stub 话术（与 K1-K4 的开关纪律一致，线上零行为变化）。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_AP = ROOT / "agent_platform"
if str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

SOURCE = "feishu_comm"
DEFAULT_DEADLINE_MIN = 5
ESCALATE_AFTER_MIN = 15

_KIND_MAP = {
    "stock_consult": "task_request",
    "watch_setup": "task_request",
    "complaint": "feedback",
}


def enabled():
    return str(os.environ.get("COMM_DISPATCH_ENABLED", "0")).strip().lower() in (
        "1", "true", "yes", "on")


def _inbox():
    """延迟取门面模块（测试可 monkeypatch 换临时 store）。"""
    from gateway import inbox_gateway as ig
    return ig


def kind_for(intent):
    return _KIND_MAP.get(intent, "question")


def dispatch(intent_result, open_id, msg_id="", deadline_min=None):
    """真投递。→ {ok, dispatch_id, replayed, state, kind, reason, error}"""
    if not enabled():
        return {"ok": False, "reason": "disabled", "dispatch_id": None,
                "kind": kind_for(intent_result.get("intent"))}
    intent = intent_result.get("intent")
    ents = intent_result.get("entities") or {}
    trace = intent_result.get("trace_id") or ""
    dm = int(deadline_min or os.environ.get(
        "COMM_DISPATCH_DEADLINE_MIN", DEFAULT_DEADLINE_MIN))
    payload = {
        "intent": intent,
        "code": ents.get("code") or "",
        "open_id": open_id,
        "feishu_msg_id": str(msg_id or ""),
        "trace_id": trace,
        "deadline_min": dm,
        "issued_by": "kanze_comm",
    }
    idem = ("feishu-%s" % msg_id) if msg_id else ("trace-%s" % trace)
    try:
        env = _inbox().submit(kind_for(intent), payload, source=SOURCE,
                              idempotency_key=idem)
    except Exception as ex:  # noqa: BLE001 —— 投递失败必须让调用方知道
        return {"ok": False, "reason": "submit_exception",
                "error": "%s: %s" % (type(ex).__name__, str(ex)[:120]),
                "dispatch_id": None, "kind": kind_for(intent)}
    data = env.get("data") or {}
    return {
        "ok": bool(env.get("ok")),
        "dispatch_id": data.get("id"),
        "replayed": bool(data.get("replayed")),
        "state": data.get("state"),
        "kind": kind_for(intent),
        "error": (env.get("error") or {}).get("message"),
    }


def check_receipt(dispatch_id):
    """→ {ok, state(inbox|processed|not_found)}"""
    try:
        env = _inbox().receipt(dispatch_id)
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "state": "error",
                "error": "%s: %s" % (type(ex).__name__, str(ex)[:120])}
    data = env.get("data") or {}
    return {"ok": bool(env.get("ok")), "state": data.get("state", "unknown"),
            "record": data.get("record") or {}}


def _parse_ts(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(s)[:19], fmt[:len(str(s)[:19])]
                                     if len(fmt) > 19 else fmt)
        except ValueError:
            continue
    return None


def sweep_overdue(now=None):
    """扫 inbox 中超 deadline 未处理的派单 → [{dispatch_id, kind, waited_min,
    escalate(bool), payload}]。只判定不发送。"""
    ig = _inbox()
    try:
        inbox_dir = Path(ig._store().inbox)
    except Exception:  # noqa: BLE001
        return []
    if not inbox_dir.exists():
        return []
    now_ts = now or time.time()
    out = []
    for f in sorted(inbox_dir.glob("*.json")):
        try:
            rec = __import__("json").loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = _parse_ts(rec.get("submitted_at"))
        if ts is None:
            continue
        waited_min = (now_ts - ts.timestamp()) / 60.0
        dm = int((rec.get("payload") or {}).get("deadline_min")
                 or DEFAULT_DEADLINE_MIN)
        if waited_min < dm:
            continue
        out.append({
            "dispatch_id": rec.get("id"),
            "kind": rec.get("kind"),
            "waited_min": round(waited_min, 1),
            "escalate": waited_min >= ESCALATE_AFTER_MIN,
            "payload": rec.get("payload") or {},
        })
    return out
