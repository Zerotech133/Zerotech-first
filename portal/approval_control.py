# -*- coding: utf-8 -*-
"""S2 审批卡回调处理（D2-D3，2026-10-30 WO-SUPERVISOR）。

纯函数：解析飞书 card.action.trigger payload → 校验操作人/动作 → 幂等写
`approval` JSONL。**不接 live 事件服务**（接线阶段再挂）；默认只返回结果。

payload 关键结构（飞书卡片回调）：
{"operator":{"open_id":"ou_xxx"},"action":{"value":{"tag":"approval:approve",
 "task":"WO-...","level":"S2","evidence_hash":"..."}}}

2026-10-31 WO-L4-M4-HARDEN-20261031（7d P0，M4 三方签字）：
新增 `evaluate_three_party_signoff()` 最小机制——同一 task 的 approve 记录须
有 **3 个 distinct operator** 且角色齐全（默认 QA1/GUANYU/VERIFY1，角色映射
由调用方注入，**不自定组织政策**）；被 annotate 以 `refs` 引用过的试点点击
不计签。`handle_action` 只做**校验+告警灰度**（结果落 `signoff` /
`warnings`，默认不阻断存量；灰度期不改变 ok 语义）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "agent_platform" / "data" / "approvals.jsonl"
TAGS = {"approval:approve": "approve", "approval:reject": "reject",
        "approval:escalate": "escalate"}
# 7d 治理跟踪 v1 O4：三方 = QA1 / GUANYU / VERIFY1（A0/老田 确认另计，
# 不在本函数最小机制内——见报告「需拍板点」）。
THREE_PARTY_ROLES = ("QA1", "GUANYU", "VERIFY1")
MIN_THREE_PARTY_SIGNERS = 3


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


def _role_resolver(role_of):
    """把 role_of（mapping 或 callable）归一为 operator → role 的解析器。"""
    if role_of is None:
        return None
    if callable(role_of):
        return role_of
    mapping = {str(k): str(v) for k, v in dict(role_of).items()}
    return mapping.get


def evaluate_three_party_signoff(rows, task, required_roles=THREE_PARTY_ROLES,
                                 role_of=None, min_signers=MIN_THREE_PARTY_SIGNERS):
    """M4 三方签字最小校验（纯函数、只读；不自定组织政策）。

    台账口径（7d_治理跟踪 v1 O4）：
    - 只认 ``action == "approve"``；reject/escalate/annotate 不算签字；
    - 同一 operator 重复点击只算 1 签（幂等），需 ``min_signers`` 个 distinct
      operator（默认 3）；
    - 被 ``annotate`` 的 ``refs`` 引用过的 ts 视为「试点/已排除」，不计签；
    - 角色齐：``role_of``（{open_id: role} 或 callable）需覆盖
      ``required_roles``（默认 QA1/GUANYU/VERIFY1）各一个；映射未提供时
      返回 ``roles_unresolved``（灰度告警，不算通过——不自定谁是谁）。

    返回 ``{ok, task, distinct_signers, signers, required_roles,
    missing_roles, excluded_ts, reason}``；reason 为空串才算通过。
    """
    task = str(task or "").strip()
    rows = list(rows or [])
    excluded = set()
    for row in rows:
        if row.get("action") != "annotate":
            continue
        refs = row.get("refs")
        if isinstance(refs, list):
            excluded.update(str(item) for item in refs)
    resolver = _role_resolver(role_of)
    signers, seen, roles_seen = [], set(), {}
    unresolved = False
    for row in rows:
        if str(row.get("task") or "") != task or row.get("action") != "approve":
            continue
        if str(row.get("ts") or "") in excluded:
            continue
        operator = str(row.get("operator") or "").strip()
        if not operator or operator in seen:
            continue
        seen.add(operator)
        role = ""
        if resolver is not None:
            try:
                role = str(resolver(operator) or "").strip()
            except Exception:  # noqa: BLE001 灰度校验不得因映射异常中断
                role = ""
        if role:
            roles_seen.setdefault(role, operator)
        else:
            unresolved = True
        signers.append({"operator": operator, "role": role})
    required = [str(item) for item in (required_roles or ())]
    missing_roles = [item for item in required if item not in roles_seen]
    if len(signers) < int(min_signers):
        reason = "distinct_signers_lt_%d(%d)" % (int(min_signers), len(signers))
    elif resolver is None or unresolved:
        reason = "roles_unresolved"
    elif missing_roles:
        reason = "missing_roles:" + ",".join(missing_roles)
    else:
        reason = ""
    return {"ok": reason == "", "task": task,
            "distinct_signers": len(signers), "signers": signers,
            "required_roles": required, "missing_roles": missing_roles,
            "excluded_ts": sorted(excluded), "reason": reason}


def _attach_signoff(result, ledger, task, signoff_roles, required_roles,
                    min_signers):
    """灰度挂载：校验结果 + 告警；不改变审批主流程 ok/拒绝语义。"""
    try:
        signoff = evaluate_three_party_signoff(
            _read_jsonl(ledger), task, required_roles=required_roles,
            role_of=signoff_roles, min_signers=min_signers)
    except Exception as exc:  # noqa: BLE001 校验自身异常不得影响审批
        signoff = {"ok": False, "task": task, "distinct_signers": 0,
                   "signers": [], "required_roles": list(required_roles or ()),
                   "missing_roles": [], "excluded_ts": [],
                   "reason": "signoff_eval_error:%s" % exc}
    result["signoff"] = signoff
    if not signoff["ok"]:
        result.setdefault("warnings", []).append(
            "three_party_incomplete:" + str(signoff["reason"]))
        log.warning("approval three_party incomplete task=%r reason=%s"
                    "（灰度告警，不阻断存量）", task, signoff["reason"])
    return result


def handle_action(payload, ledger=None, allowed_ops=None, owner=None,
                  owner_ids=None, now=None, signoff_roles=None,
                  signoff_required_roles=THREE_PARTY_ROLES,
                  signoff_min_signers=MIN_THREE_PARTY_SIGNERS):
    """返回 {ok, action, task, error?, duplicate?, signoff?, warnings?}。

    2026-10-31：`signoff` 为三方签字灰度校验结果（只告警，不阻断）；
    待组织确认角色→open_id 映射后再决定是否升级为强制（见报告需拍板点）。
    """
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
            return _attach_signoff(
                {"ok": True, "action": action, "task": task, "duplicate": True},
                ledger, task, signoff_roles, signoff_required_roles,
                signoff_min_signers)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
           "schema": "approval/v1", "task": task, "action": action,
           "operator": op, "level": str(val.get("level") or "S2"),
           "evidence_hash": str(val.get("evidence_hash") or ""),
           "source": "feishu_card", "revocable": True}
    _append(ledger, rec)
    return _attach_signoff(
        {"ok": True, "action": action, "task": task, "record": rec},
        ledger, task, signoff_roles, signoff_required_roles,
        signoff_min_signers)
