# -*- coding: utf-8 -*-
"""S2 审批卡回调处理（D2-D3，2026-10-30 WO-SUPERVISOR）。

纯函数：解析飞书 card.action.trigger payload → 校验操作人/动作 → 幂等写
`approval` JSONL。**不接 live 事件服务**（接线阶段再挂）；默认只返回结果。

payload 关键结构（飞书卡片回调）：
{"operator":{"open_id":"ou_xxx"},"action":{"value":{"tag":"approval:approve",
 "task":"WO-...","level":"S2","evidence_hash":"..."}}}
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "agent_platform" / "data" / "approvals.jsonl"
TAGS = {"approval:approve": "approve", "approval:reject": "reject",
        "approval:escalate": "escalate"}


def _read_jsonl(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    except OSError:
        pass
    return rows


_APPEND_LOCK = threading.Lock()


def _append(path, rec):
    """O_APPEND 单次写，避免多进程/线程交错半行。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    with _APPEND_LOCK:
        fd = os.open(str(p), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)


def handle_action(payload, ledger=None, allowed_ops=None, owner=None,
                  owner_ids=None, now=None):
    """返回 {ok, action, task, error?, duplicate?}；不修改任何审批门。"""
    ledger = Path(ledger or LEDGER)
    now = now or time.time()
    op = str(((payload or {}).get("operator") or {}).get("open_id") or "")
    val = ((payload or {}).get("action") or {}).get("value") or {}
    tag = str(val.get("tag") or "")
    action = TAGS.get(tag)
    task = str(val.get("task") or "").strip()
    if not op:
        return {"ok": False, "error": "missing_operator"}
    if allowed_ops is not None and op not in set(allowed_ops):
        return {"ok": False, "error": "operator_not_allowed"}
    if not action:
        return {"ok": False, "error": "unknown_action"}
    if not task:
        return {"ok": False, "error": "missing_task"}
    owners = {str(x) for x in (owner_ids or []) if str(x)}
    if owner:
        owners.add(str(owner))
    if owners and op in owners:  # 自审防护：owner/owner_ids 用 open_id
        return {"ok": False, "error": "self_approval_blocked"}
    rows = _read_jsonl(ledger)
    for r in rows:
        if r.get("task") == task and r.get("action") == action and r.get("operator") == op:
            return {"ok": True, "action": action, "task": task, "duplicate": True}
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
           "schema": "approval/v1", "task": task, "action": action,
           "operator": op, "level": str(val.get("level") or "S2"),
           "evidence_hash": str(val.get("evidence_hash") or ""),
           "source": "feishu_card", "revocable": True}
    _append(ledger, rec)
    return {"ok": True, "action": action, "task": task, "record": rec}
