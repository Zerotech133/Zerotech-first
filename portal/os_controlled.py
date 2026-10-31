# -*- coding: utf-8 -*-
"""C 阶段最小真实受控闭环（CONTROLLED_ONLY）。

与 G1 隔离链的区别（这是"从演示到能干活"的分界）：
- **真实消费者**：必须先注册（consumer.registered），才允许真实认领；
- **真实签准**：本人 signer（method + nonce 摘要）绑动作范围/环境 controlled/有效期；
- **正式工单**：绑定签准与验收口径（workorder.formal），不是模拟工单；
- **风控裁决**：动作分级 L1（本地可逆）/L2（外发、发布、生产、交易）→ L2 默认 deny；
- **真实认领**：method="real"，必须绑定注册消费者 + 正式工单 + 有效签准 + 租约；
- **受控产物**：只在隔离工作区落地，带 sha256 + 自检回执 + 审计链；
- **审计链**：chain/controlled 给出 CONTROLLED_ONLY / FAIL / BLOCKED。

本模块只做"真实受控"的最小闭环；L2 外部动作仍被风控拒绝（需后续人工审批通道）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import os_goal_contract as C
    import os_goal_store as S
except ImportError:  # pragma: no cover
    from . import os_goal_contract as C  # type: ignore
    from . import os_goal_store as S  # type: ignore

BEIJING = timezone(timedelta(hours=8))
USER_ACTOR = {"kind": "user", "id": "laotian", "seat": "laotian"}
SYSTEM_ACTOR = {"kind": "system", "id": "os_controlled"}
CONTROLLED_ACTIONS = (
    "artifact.generate",
    "workspace.write",
    "test.run",
    "workspace.cleanup",
)
L1_ACTIONS = set(CONTROLLED_ACTIONS)
L2_ACTIONS = {"external.send", "publish", "production.deploy", "trade.execute"}
SIGNER_METHODS = ("session_nonce", "local_password_session")
CONSUMER_KINDS = ("agent", "human", "service")
# H2（K-ORG-20261031-PORTALAUTH）：真实签准改用**服务端一次性 nonce**（TTL+单次消费）。
# 旧实现只校验 len(nonce)>=8 —— 任意客户端可自造 nonce 冒充"真实签准"（审计 v2 实测）。
NONCE_TTL_SECONDS = 120
MAX_GRANT_NONCES = 1024
_grant_nonces: Dict[str, Dict[str, Any]] = {}
_grant_nonce_lock = threading.Lock()
LEASE_MINUTES = 30
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(html|md|txt|json|log)$")


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _valid_until(hours: int = 2) -> str:
    return (datetime.now(BEIJING) + timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )


def _parse(ts: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=BEIJING)


def _valid_grant(payload: Dict[str, Any]) -> bool:
    until = _parse(payload.get("valid_until"))
    return bool(until and datetime.now(BEIJING) < until)


def _usable_grant(store: S.OsGoalStore, item_id: str) -> Dict[str, Any]:
    """取最新真实签准并校验：controlled 环境、未过期、未被撤销。"""
    evs = _evs(store, item_id)
    g = _find(evs, "grant.signed")
    _require(g is not None, "缺少真实签准：请先 sign_real_grant")
    p = g["payload"]
    _require(p.get("env") == "controlled", "真实签准环境必须是 controlled")
    _require(_valid_grant(p), "签准已过期，请重新签准")
    revoked = any(
        e.get("type") == "grant.revoked"
        and (e.get("payload") or {}).get("grant_id") == p.get("grant_id")
        for e in evs
    )
    _require(not revoked, "签准已撤销：%s" % p.get("grant_id"))
    return p


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise C.ContractError(msg)


def _evs(store: S.OsGoalStore, item_id: str) -> List[Dict[str, Any]]:
    return list(store.events_for(item_id))


def _find(evs: List[Dict[str, Any]], etype: str) -> Optional[Dict[str, Any]]:
    found = [e for e in evs if e.get("type") == etype]
    return found[-1] if found else None


def _all_events(store: S.OsGoalStore) -> List[Dict[str, Any]]:
    return list(getattr(store, "_events", []) or [])


def _consumer(store: S.OsGoalStore, consumer_id: str) -> Optional[Dict[str, Any]]:
    """返回该消费者的最新注册/状态事件（后写覆盖先写，确保 deactivate 生效）。"""
    found = None
    for e in _all_events(store):
        p = e.get("payload") or {}
        if (
            e.get("type") == "consumer.registered"
            and p.get("consumer_id") == consumer_id
        ):
            found = p
    return found


def _active_consumer(store: S.OsGoalStore, consumer_id: str) -> Dict[str, Any]:
    p = _consumer(store, consumer_id)
    _require(p is not None, "消费者未注册：%s（先注册再真实认领）" % consumer_id)
    _require(p.get("status") == "active", "消费者未激活：%s" % consumer_id)
    return p


def _item(store: S.OsGoalStore, item_id: str) -> Dict[str, Any]:
    try:
        return store.get_item(item_id)
    except Exception as exc:  # noqa: BLE001
        raise C.ContractError("事项不存在：%s（%s）" % (item_id, exc))


def _workspace(store: S.OsGoalStore, item_id: str) -> str:
    base = os.path.realpath(store.base_dir)
    ws = os.path.realpath(os.path.join(base, "items", item_id, "workspace"))
    _require(ws.startswith(base + os.sep), "工作区越界")
    os.makedirs(ws, exist_ok=True)
    return ws


# ---------------- 消费者注册 ----------------
def register_consumer(
    store: S.OsGoalStore,
    consumer_id: str,
    kind: str = "agent",
    display_name: str = "",
    capabilities: Optional[List[str]] = None,
    owner_id: str = "laotian",
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _require(
        bool(ID_RE.match(str(consumer_id or ""))),
        "consumer_id 非法（3-64 位字母数字._-）",
    )
    _require(kind in CONSUMER_KINDS, "消费者类型必须是 %s" % (CONSUMER_KINDS,))
    caps = [str(c) for c in (capabilities or ["artifact.generate"])]
    _require(all(c in L1_ACTIONS for c in caps), "能力必须属于受控 L1 动作")
    existing = _consumer(store, consumer_id)
    if existing and existing.get("status") == "active":
        return existing
    ev = store.append_event(
        "consumer.registered",
        {},
        {
            "consumer_id": consumer_id,
            "kind": kind,
            "display_name": display_name or consumer_id,
            "capabilities": caps,
            "owner_id": owner_id,
            "status": "active",
            "registered_at": _now(),
            "reactivated": bool(existing),
        },
        actor=USER_ACTOR,
        idem=idem,
    )
    return ev["payload"]


def list_consumers(store: S.OsGoalStore) -> List[Dict[str, Any]]:
    latest = {}
    for e in _all_events(store):
        if e.get("type") == "consumer.registered":
            p = e.get("payload") or {}
            if p.get("consumer_id"):
                latest[p["consumer_id"]] = p
    return list(latest.values())


def deactivate_consumer(
    store: S.OsGoalStore,
    consumer_id: str,
    reason: str = "manual",
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _active_consumer(store, consumer_id)
    ev = store.append_event(
        "consumer.registered",
        {},
        {
            "consumer_id": consumer_id,
            "status": "inactive",
            "deactivated_at": _now(),
            "reason": reason,
        },
        actor=USER_ACTOR,
        idem=idem,
    )
    return ev["payload"]


# ---------------- 签准 nonce（H2） ----------------
def _purge_grant_nonces_locked(now: float) -> None:
    expired = [k for k, v in _grant_nonces.items() if float(v.get("expires") or 0) <= now]
    for k in expired:
        _grant_nonces.pop(k, None)


def issue_grant_nonce(
    store: S.OsGoalStore,
    item_id: str,
    actions: List[str],
    signer_method: str = "session_nonce",
    signer_id: str = "",
    ttl_seconds: int = NONCE_TTL_SECONDS,
) -> Dict[str, Any]:
    """签发**一次性**签准 nonce（绑定 item/动作/方式/会话 sub，TTL 默认 120s）。"""
    _item(store, item_id)
    _require(signer_method in SIGNER_METHODS, "签准方式必须是 %s" % (SIGNER_METHODS,))
    sid = str(signer_id or "").strip()
    _require(bool(sid), "签发签准 nonce 需要登录会话（zb_session sub）")
    acts = sorted({str(a) for a in (actions or [])})
    _require(bool(acts), "签准动作不能为空")
    _require(all(a in L1_ACTIONS for a in acts),
             "真实签准只允许 L1 受控动作：%s" % (CONTROLLED_ACTIONS,))
    ttl = max(30, min(int(ttl_seconds or NONCE_TTL_SECONDS), 600))
    nonce = "n1-" + secrets.token_urlsafe(24)
    now = time.time()
    with _grant_nonce_lock:
        _purge_grant_nonces_locked(now)
        if len(_grant_nonces) >= MAX_GRANT_NONCES:
            oldest = min(_grant_nonces, key=lambda k: _grant_nonces[k].get("issued", 0))
            _grant_nonces.pop(oldest, None)
        _grant_nonces[nonce] = {
            "item_id": item_id,
            "actions": acts,
            "signer_method": signer_method,
            "signer_id": sid,
            "expires": now + ttl,
            "issued": now,
            "issued_at": _now(),
        }
    return {
        "nonce": nonce,
        "expires_in": ttl,
        "item_id": item_id,
        "actions": acts,
        "signer_method": signer_method,
        "signer_id": sid,
    }


def _consume_grant_nonce(nonce: str, item_id: str, actions: List[str],
                         signer_method: str, signer_id: str) -> Dict[str, Any]:
    """单次消费：先 pop 再校验（重放必失败），并核对绑定范围与会话 signer。"""
    key = str(nonce or "").strip()
    _require(bool(key), "真实签准必须带服务端签发的一次性 nonce")
    with _grant_nonce_lock:
        rec = _grant_nonces.pop(key, None)
    _require(rec is not None,
             "nonce 无效或已被使用（须先 POST /api/os/item/<iid>/grant/nonce 获取）")
    _require(float(rec.get("expires") or 0) >= time.time(),
             "nonce 已过期（TTL %ss）" % NONCE_TTL_SECONDS)
    _require(rec.get("item_id") == item_id, "nonce 与 item 不匹配")
    _require(rec.get("signer_method") == signer_method, "nonce 与签准方式不匹配")
    _require(rec.get("signer_id") == str(signer_id or "").strip(),
             "signer 与会话不一致（nonce 绑定其他会话）")
    _require(sorted(rec.get("actions") or []) == sorted({str(a) for a in (actions or [])}),
             "nonce 绑定的动作范围不一致")
    return rec


# ---------------- 签准 / 工单 ----------------
def sign_real_grant(
    store: S.OsGoalStore,
    item_id: str,
    actions: List[str],
    nonce: str,
    signer_method: str = "session_nonce",
    valid_hours: int = 2,
    idem: Optional[str] = None,
    signer_id: Optional[str] = None,
) -> Dict[str, Any]:
    _item(store, item_id)
    _require(signer_method in SIGNER_METHODS, "签准方式必须是 %s" % (SIGNER_METHODS,))
    sid = str(signer_id or "").strip()
    _require(bool(sid), "真实签准需要登录会话（zb_session sub），不接受匿名/自填 signer")
    acts = [str(a) for a in (actions or [])]
    _require(bool(acts), "签准动作不能为空")
    _require(
        all(a in L1_ACTIONS for a in acts),
        "真实签准只允许 L1 受控动作：%s" % (CONTROLLED_ACTIONS,),
    )
    _require(1 <= int(valid_hours) <= 24, "真实签准有效期 1-24 小时")
    # H2：一次性 nonce 在此消费（重放/过期/错 item/错动作/错方式/伪造 signer 全部拒绝）
    _consume_grant_nonce(nonce, item_id, acts, signer_method, sid)
    ev = store.append_event(
        "grant.signed",
        {"item_id": item_id},
        {
            "grant_id": "GRT-%s-%03d"
            % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
            "grant_version": "g1",
            "actions": sorted(set(acts)),
            "env": "controlled",
            "signer": {
                "kind": "user",
                "id": sid,
                "method": signer_method,
                "nonce_sha256": "sha256:" + hashlib.sha256(nonce.encode()).hexdigest(),
            },
            "valid_until": _valid_until(int(valid_hours)),
        },
        actor=USER_ACTOR,
        idem=idem,
    )
    return ev["payload"]


def revoke_grant(
    store: S.OsGoalStore, item_id: str, grant_id: str, idem: Optional[str] = None
) -> Dict[str, Any]:
    _item(store, item_id)
    g = _find(_evs(store, item_id), "grant.signed")
    _require(
        g is not None and g["payload"].get("grant_id") == grant_id,
        "撤销失败：签准不存在或不是最新签准：%s" % grant_id,
    )
    ev = store.append_event(
        "grant.revoked",
        {"item_id": item_id},
        {"grant_id": grant_id, "revoked_at": _now()},
        actor=USER_ACTOR,
        idem=idem,
    )
    return ev["payload"]


def create_formal_workorder(
    store: S.OsGoalStore,
    item_id: str,
    acceptance: str,
    required_capabilities: Optional[List[str]] = None,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _item(store, item_id)
    evs = _evs(store, item_id)
    grant = _usable_grant(store, item_id)
    _require(bool(str(acceptance or "").strip()), "正式工单必须写验收口径")
    caps = [str(c) for c in (required_capabilities or ["artifact.generate"])]
    _require(
        all(c in grant.get("actions", []) for c in caps), "工单要求超出签准动作范围"
    )
    ev = store.append_event(
        "workorder.formal",
        {"item_id": item_id},
        {
            "workorder_id": "WO-%s-%03d" % (time.strftime("%Y%m%d"), len(evs) + 1),
            "acceptance": acceptance,
            "required_capabilities": caps,
            "grant_id": grant.get("grant_id"),
            "env": "controlled",
            "created_by": "laotian",
            "created_at": _now(),
        },
        actor=SYSTEM_ACTOR,
        idem=idem,
    )
    return ev["payload"]


# ---------------- 风控裁决 ----------------
def risk_verdict(
    store: S.OsGoalStore,
    item_id: str,
    action: str,
    consumer_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _item(store, item_id)
    action = str(action or "")
    reasons, decision, level = [], "deny", "L2"
    if action in L1_ACTIONS:
        decision, level = "allow", "L1"
        reasons.append("L1 本地可逆动作（隔离工作区）")
    elif action in L2_ACTIONS:
        decision, level = "deny", "L2"
        reasons.append("L2 外部/不可逆动作默认拒绝：需后续人工审批通道")
    else:
        decision, level = "deny", "unknown"
        reasons.append("未知动作，默认拒绝")
    if consumer_id:
        try:
            _active_consumer(store, consumer_id)
            reasons.append("消费者已注册且激活")
        except C.ContractError as exc:
            decision = "deny"
            reasons.append("消费者校验失败：%s" % exc)
    ev = store.append_event(
        "risk.verdict",
        {"item_id": item_id},
        {
            "verdict_id": "RSK-%s-%03d"
            % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
            "action": action,
            "decision": decision,
            "risk_level": level,
            "reasons": reasons,
            "policy": "os-risk-v0",
            "context": context or {},
        },
        actor=SYSTEM_ACTOR,
        idem=idem,
    )
    return ev["payload"]


# ---------------- 真实认领 / 受控产物 ----------------
def real_claim(
    store: S.OsGoalStore, item_id: str, consumer_id: str, idem: Optional[str] = None
) -> Dict[str, Any]:
    consumer = _active_consumer(store, consumer_id)
    evs = _evs(store, item_id)
    wo = _find(evs, "workorder.formal")
    _require(wo is not None, "先建正式工单，再真实认领")
    grant = _usable_grant(store, item_id)
    caps = set(wo["payload"].get("required_capabilities") or [])
    _require(caps <= set(grant.get("actions", [])), "签准未覆盖工单要求动作")
    ccaps = set(consumer.get("capabilities") or [])
    _require(caps <= ccaps, "消费者能力不足：缺 %s" % sorted(caps - ccaps))
    lease = (datetime.now(BEIJING) + timedelta(minutes=LEASE_MINUTES)).isoformat(
        timespec="seconds"
    )
    ev = store.append_event(
        "workorder.claimed",
        {"item_id": item_id},
        {
            "workorder_id": wo["payload"].get("workorder_id"),
            "claim": {
                "consumer_id": consumer_id,
                "method": "real",
                "claimed_at": _now(),
                "lease_until": lease,
                "authority_ref": grant.get("grant_id"),
            },
        },
        actor={"kind": "consumer", "id": consumer_id, "seat": consumer_id},
        idem=idem,
    )
    return ev["payload"]


def _claim_active(claim_event: Optional[Dict[str, Any]], consumer_id: str) -> bool:
    if not claim_event:
        return False
    claim = (claim_event.get("payload") or {}).get("claim") or {}
    until = _parse(claim.get("lease_until"))
    return (
        claim.get("method") == "real"
        and claim.get("consumer_id") == consumer_id
        and bool(until and datetime.now(BEIJING) < until)
    )


def produce_controlled_artifact(
    store: S.OsGoalStore,
    item_id: str,
    consumer_id: str,
    filename: str,
    content: str,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _active_consumer(store, consumer_id)
    _require(
        bool(FILE_RE.match(str(filename or ""))),
        "文件名必须是安全文件名（html/md/txt/json/log）",
    )
    _require(bool(str(content or "").strip()), "产物内容不能为空")
    evs = _evs(store, item_id)
    claim = _find(evs, "workorder.claimed")
    _require(_claim_active(claim, consumer_id), "没有有效的真实认领（或租约过期）")
    grant = _usable_grant(store, item_id)
    wo = _find(evs, "workorder.formal")
    _require(wo is not None, "缺少正式工单")
    _require(
        "artifact.generate" in (grant.get("actions") or []),
        "签准未包含 artifact.generate",
    )
    safe = os.path.realpath(os.path.join(_workspace(store, item_id), filename))
    base = os.path.realpath(store.base_dir)
    _require(
        safe.startswith(
            os.path.realpath(os.path.join(base, "items", item_id, "workspace")) + os.sep
        ),
        "产物路径越界",
    )
    # 风控：执行前必须有 allow 裁决
    risk = _find(evs, "risk.verdict")
    if not (
        risk
        and risk["payload"].get("action") == "artifact.generate"
        and risk["payload"].get("decision") == "allow"
    ):
        risk_ev = risk_verdict(
            store,
            item_id,
            "artifact.generate",
            consumer_id=consumer_id,
            context={"source": "produce_artifact"},
        )
        _require(
            risk_ev.get("decision") == "allow",
            "风控未放行：%s" % risk_ev.get("reasons"),
        )
    data = content.encode("utf-8")
    with open(safe, "wb") as fh:
        fh.write(data)
    sha = "sha256:" + hashlib.sha256(data).hexdigest()
    art = store.append_event(
        "artifact.produced",
        {"item_id": item_id},
        {
            "artifact_id": "ART-%s-C0" % item_id,
            "version": "c1",
            "workorder_id": wo["payload"].get("workorder_id"),
            "path": safe,
            "path_rel": os.path.join("items", item_id, "workspace", filename),
            "sha256": sha,
            "bytes": len(data),
            "env": "controlled",
            "consumer": {"kind": "consumer", "id": consumer_id},
            "rollback": {
                "method": "delete_dir",
                "recipe": "撤回=删除 %s（进回收站）；事件与指纹保留对账"
                % os.path.dirname(safe),
            },
        },
        actor={"kind": "consumer", "id": consumer_id, "seat": consumer_id},
        idem=idem,
    )
    # 自检回执（与 G1 一致：自检≠独立 QA）
    store.append_event(
        "test.recorded",
        {"item_id": item_id},
        {
            "evidence_id": "TES-%s-C0" % item_id,
            "artifact_id": art["payload"]["artifact_id"],
            "type": "unit",
            "command": "os_controlled.static_selfcheck",
            "result": "pass" if len(data) > 0 and safe.startswith(base) else "fail",
            "output_sha256": sha,
            "executed_by": "os_controlled.selfcheck（非独立QA）",
        },
        actor=SYSTEM_ACTOR,
    )
    return {
        "artifact": art["payload"],
        "risk": risk["payload"] if risk else {},
        "claim": claim["payload"],
    }


# ---------------- 审计链 ----------------
def chain_report_controlled(store: S.OsGoalStore, item_id: str) -> Dict[str, Any]:
    evs = _evs(store, item_id)
    _item(store, item_id)
    grant = _find(evs, "grant.signed")
    wo = _find(evs, "workorder.formal")
    claim = _find(evs, "workorder.claimed")
    risk = _find(evs, "risk.verdict")
    art = _find(evs, "artifact.produced")
    gates: Dict[str, Dict[str, Any]] = {}

    def gate(name: str, status: str, detail: str) -> None:
        gates[name] = {"gate": name, "status": status, "detail": detail}

    gate("C0-ITEM", "PASS", "事项已建档（%s）" % item_id)
    if grant is None:
        gate("C0-AUTH", "BLOCKED", "缺少真实签准")
    else:
        p = grant["payload"]
        revokes = [
            e
            for e in evs
            if e.get("type") == "grant.revoked"
            and (e.get("payload") or {}).get("grant_id") == p.get("grant_id")
        ]
        signer_ok = (p.get("signer") or {}).get("kind") == "user"
        if art is not None:
            # 历史链：按产物产生时刻判定授权（之后的撤销/过期不追溯翻案）
            produced = _parse(art.get("at")) or datetime.now(BEIJING)
            until = _parse(p.get("valid_until"))
            revoke_before_art = any(
                art.get("seq") is not None
                and e.get("seq") is not None
                and e["seq"] < art["seq"]
                for e in revokes
            )
            ok = (
                p.get("env") == "controlled"
                and signer_ok
                and bool(until and produced < until)
                and not revoke_before_art
            )
            note = "历史产物按产生时刻判定" + (
                "；撤销发生在产物之后" if revokes and not revoke_before_art else ""
            )
        else:
            revoked = bool(revokes)
            ok = (
                p.get("env") == "controlled"
                and signer_ok
                and _valid_grant(p)
                and not revoked
            )
            note = "（已撤销）" if revoked else ""
        gate(
            "C0-AUTH",
            "PASS" if ok else "FAIL",
            "真实签准 env=%s 有效期至 %s%s"
            % (p.get("env"), p.get("valid_until"), note),
        )
    if wo is None:
        gate("C0-WO", "BLOCKED", "缺少正式工单")
    else:
        ok = (
            bool(wo["payload"].get("acceptance"))
            and wo["payload"].get("env") == "controlled"
        )
        gate(
            "C0-WO",
            "PASS" if ok else "FAIL",
            "正式工单 %s" % wo["payload"].get("workorder_id"),
        )
    if claim is None:
        gate("C0-CLAIM", "BLOCKED", "尚未真实认领")
    else:
        claim_p = claim["payload"].get("claim") or {}
        cid = claim_p.get("consumer_id")
        consumer = _consumer(store, cid)
        claim_seq = claim.get("seq")
        deact_seqs = [
            e.get("seq")
            for e in evs
            if e.get("type") == "consumer.registered"
            and (e.get("payload") or {}).get("consumer_id") == cid
            and (e.get("payload") or {}).get("status") == "inactive"
            and e.get("seq") is not None
        ]
        deact_before_claim = any(
            claim_seq is not None and s2 < claim_seq for s2 in deact_seqs
        )
        ok = (
            claim_p.get("method") == "real"
            and consumer is not None
            and not deact_before_claim
        )
        note = (
            "；停用发生在认领之后（历史有效）"
            if deact_seqs and not deact_before_claim
            else ""
        )
        gate(
            "C0-CLAIM",
            "PASS" if ok else "FAIL",
            "consumer=%s method=%s%s" % (cid, claim_p.get("method"), note),
        )
    if risk is None:
        gate("C0-RISK", "BLOCKED", "缺少风控裁决")
    else:
        rp = risk["payload"]
        ok = rp.get("action") == "artifact.generate" and rp.get("decision") == "allow"
        gate(
            "C0-RISK",
            "PASS" if ok else "FAIL",
            "action=%s decision=%s" % (rp.get("action"), rp.get("decision")),
        )
    if art is None:
        gate("C0-ART", "BLOCKED", "尚未产出受控产物")
    else:
        ap = art["payload"]
        ok = ap.get("env") == "controlled" and str(ap.get("sha256", "")).startswith(
            "sha256:"
        )
        gate(
            "C0-ART",
            "PASS" if ok else "FAIL",
            "artifact=%s sha=%s" % (ap.get("artifact_id"), ap.get("sha256")),
        )
    bad_actor = [
        e.get("seq")
        for e in evs
        if (e.get("actor") or {}).get("kind")
        not in ("user", "agent", "system", "consumer")
    ]
    gate(
        "C0-AUDIT",
        "PASS" if not bad_actor else "FAIL",
        "actor 审计：%s" % ("全部合法" if not bad_actor else "异常 seq=%s" % bad_actor),
    )

    statuses = [g["status"] for g in gates.values()]
    if "FAIL" in statuses:
        verdict, exit_code = "FAIL", 4
    elif "BLOCKED" in statuses:
        verdict, exit_code = "BLOCKED", 3
    else:
        verdict, exit_code = "CONTROLLED_ONLY", 0
    return {
        "item_id": item_id,
        "verdict": verdict,
        "exit_code": exit_code,
        "gates": gates,
        "counts": {"events": len(evs)},
        "honesty": {
            "claim": "real",
            "env": "controlled",
            "limit": "L2 外部动作仍被风控拒绝；本链只证本地受控可执行",
        },
    }


# ================= C1：L2 人工审批通道（仍本机受控，不实际外发） =================
def request_l2_approval(
    store: S.OsGoalStore,
    item_id: str,
    action: str,
    consumer_id: str,
    summary: str = "",
    ttl_minutes: int = 30,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _item(store, item_id)
    action = str(action or "")
    _require(
        action in L2_ACTIONS,
        "只有 L2 外部/不可逆动作需要人工审批：%s" % (sorted(L2_ACTIONS),),
    )
    _active_consumer(store, consumer_id)
    _require(1 <= int(ttl_minutes) <= 240, "审批有效期 1-240 分钟")
    expires = (datetime.now(BEIJING) + timedelta(minutes=int(ttl_minutes))).isoformat(
        timespec="seconds"
    )
    ev = store.append_event(
        "approval.requested",
        {"item_id": item_id},
        {
            "approval_id": "APR-%s-%03d"
            % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
            "action": action,
            "consumer_id": consumer_id,
            "summary": str(summary or "")[:400],
            "expires_at": expires,
            "status": "pending",
            "requested_by": "consumer",
            "env": "controlled",
            "external_sent": False,
        },
        actor={"kind": "consumer", "id": consumer_id, "seat": consumer_id},
        idem=idem,
    )
    return ev["payload"]


def decide_l2_approval(
    store: S.OsGoalStore,
    item_id: str,
    approval_id: str,
    decision: str,
    nonce: str,
    note: str = "",
    signer_method: str = "session_nonce",
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _item(store, item_id)
    evs = _evs(store, item_id)
    req = None
    for e in reversed(evs):
        if (
            e.get("type") == "approval.requested"
            and (e.get("payload") or {}).get("approval_id") == approval_id
        ):
            req = e["payload"]
            break
    _require(req is not None, "审批单不存在：%s" % approval_id)
    already = any(
        e.get("type") == "approval.decided"
        and (e.get("payload") or {}).get("approval_id") == approval_id
        for e in evs
    )
    _require(not already, "审批单已有结论，不能重复裁决：%s" % approval_id)
    exp = _parse(req.get("expires_at"))
    _require(bool(exp and datetime.now(BEIJING) < exp), "审批单已过期，请重新申请")
    _require(decision in ("approved", "denied"), "审批结论必须是 approved / denied")
    _require(signer_method in SIGNER_METHODS, "签准方式不合法")
    _require(
        isinstance(nonce, str) and len(nonce.strip()) >= 8,
        "人工审批必须带 nonce（≥8 字符）",
    )
    ev = store.append_event(
        "approval.decided",
        {"item_id": item_id},
        {
            "approval_id": approval_id,
            "decision": decision,
            "note": str(note or "")[:300],
            "action": req.get("action"),
            "consumer_id": req.get("consumer_id"),
            "signer": {
                "kind": "user",
                "id": "laotian",
                "method": signer_method,
                "nonce_sha256": "sha256:" + hashlib.sha256(nonce.encode()).hexdigest(),
            },
            "decided_at": _now(),
        },
        actor=USER_ACTOR,
        idem=idem,
    )
    return ev["payload"]


def _approval_state(
    store: S.OsGoalStore, item_id: str, approval_id: str
) -> Dict[str, Any]:
    evs = _evs(store, item_id)
    req = dec = None
    for e in evs:
        p = e.get("payload") or {}
        if (
            e.get("type") == "approval.requested"
            and p.get("approval_id") == approval_id
        ):
            req = p
        if e.get("type") == "approval.decided" and p.get("approval_id") == approval_id:
            dec = p
    if req is None:
        return {"status": "missing"}
    exp = _parse(req.get("expires_at"))
    if exp and datetime.now(BEIJING) >= exp:
        return {"status": "expired", "request": req, "decision": dec}
    if dec is None:
        return {"status": "pending", "request": req}
    return {"status": dec.get("decision") or "unknown", "request": req, "decision": dec}


def risk_verdict_with_approval(
    store: S.OsGoalStore,
    item_id: str,
    action: str,
    consumer_id: str,
    approval_id: str,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _item(store, item_id)
    action = str(action or "")
    reasons = []
    decision, level = "deny", "L2" if action in L2_ACTIONS else "unknown"
    if action not in L2_ACTIONS:
        reasons.append("该动作不是 L2，不需要/不支持人工审批通道")
    else:
        st = _approval_state(store, item_id, approval_id)
        if st["status"] != "approved":
            reasons.append("审批未通过：status=%s" % st["status"])
        else:
            req = st.get("request") or {}
            if req.get("action") != action:
                reasons.append("审批动作与执行动作不一致")
            elif req.get("consumer_id") != consumer_id:
                reasons.append("审批消费者与执行消费者不一致")
            else:
                decision = "allow"
                reasons.append("本人逐次审批通过（%s）" % approval_id)
    ev = store.append_event(
        "risk.verdict",
        {"item_id": item_id},
        {
            "verdict_id": "RSK-%s-%03d"
            % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
            "action": action,
            "decision": decision,
            "risk_level": level,
            "reasons": reasons,
            "policy": "os-risk-v0-c1",
            "approval_id": approval_id,
            "consumer_id": consumer_id,
        },
        actor=SYSTEM_ACTOR,
        idem=idem,
    )
    return ev["payload"]


def record_l2_outbox(
    store: S.OsGoalStore,
    item_id: str,
    consumer_id: str,
    approval_id: str,
    action: str,
    summary: str = "",
    payload_text: str = "",
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    _active_consumer(store, consumer_id)
    action = str(action or "")
    _require(action in L2_ACTIONS, "outbox 仅用于 L2 动作")
    _require(bool(FILE_RE.match("l2_outbox.json")), "unreachable")
    st = _approval_state(store, item_id, approval_id)
    _require(st["status"] == "approved", "outbox 前必须有本人 approved 审批")
    req = st.get("request") or {}
    _require(
        req.get("action") == action and req.get("consumer_id") == consumer_id,
        "审批与 outbox 的动作/消费者不一致",
    )
    rk = risk_verdict_with_approval(store, item_id, action, consumer_id, approval_id)
    _require(rk.get("decision") == "allow", "风控未放行：%s" % rk.get("reasons"))
    body = {
        "action": action,
        "approval_id": approval_id,
        "consumer_id": consumer_id,
        "summary": str(summary or "")[:400],
        "payload": str(payload_text or "")[:4000],
        "external_sent": False,
        "env": "controlled",
        "note": "C1 仅记录受控 outbox；未调用任何外部发送通道",
    }
    raw = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
    ws = _workspace(store, item_id)
    path = os.path.join(
        ws, "l2_outbox_%s.json" % re.sub(r"[^A-Za-z0-9._-]", "_", approval_id)
    )
    with open(path, "wb") as fh:
        fh.write(raw)
    sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    ev = store.append_event(
        "l2.outbox.recorded",
        {"item_id": item_id},
        {
            "outbox_id": "OUT-%s-%03d"
            % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
            "approval_id": approval_id,
            "action": action,
            "consumer_id": consumer_id,
            "path": path,
            "sha256": sha,
            "bytes": len(raw),
            "external_sent": False,
            "env": "controlled",
        },
        actor={"kind": "consumer", "id": consumer_id, "seat": consumer_id},
        idem=idem,
    )
    return ev["payload"]


def chain_report_c1(store: S.OsGoalStore, item_id: str) -> Dict[str, Any]:
    evs = _evs(store, item_id)
    _item(store, item_id)
    req = dec = risk = outbox = None
    for e in evs:
        t = e.get("type")
        if t == "approval.requested":
            req = e
        elif t == "approval.decided":
            dec = e
        elif t == "risk.verdict" and (e.get("payload") or {}).get("approval_id"):
            risk = e
        elif t == "l2.outbox.recorded":
            outbox = e
    gates = {}

    def gate(n, st, d):
        gates[n] = {"gate": n, "status": st, "detail": d}

    gate("C1-ITEM", "PASS", "事项已建档（%s）" % item_id)
    gate(
        "C1-REQ",
        "PASS" if req else "BLOCKED",
        "审批单 %s action=%s"
        % (
            (req or {}).get("payload", {}).get("approval_id"),
            (req or {}).get("payload", {}).get("action"),
        ),
    )
    decision = (dec or {}).get("payload", {}).get("decision")
    rid = (req or {}).get("payload", {}).get("approval_id", "-")
    if dec is None:
        gate("C1-DECISION", "BLOCKED", "本人审批：未决（%s）" % rid)
    elif decision != "approved":
        gate("C1-DECISION", "BLOCKED", "本人审批：%s（%s）" % (decision, rid))
    else:
        exp = _parse((req or {}).get("payload", {}).get("expires_at"))
        ok = bool(outbox) or bool(exp and datetime.now(BEIJING) < exp)
        note = "；outbox 已记录（历史有效）" if outbox else ""
        gate(
            "C1-DECISION",
            "PASS" if ok else "BLOCKED",
            "本人审批：approved（%s）%s" % (rid, note),
        )
    gate(
        "C1-RISK",
        "PASS"
        if risk and (risk.get("payload") or {}).get("decision") == "allow"
        else "BLOCKED",
        "风控：%s" % ((risk or {}).get("payload", {}).get("decision") or "未裁决"),
    )
    gate(
        "C1-OUTBOX",
        "PASS"
        if outbox and (outbox.get("payload") or {}).get("external_sent") is False
        else "BLOCKED",
        "受控 outbox（不外发）",
    )
    bad = [
        e.get("seq")
        for e in evs
        if (e.get("actor") or {}).get("kind")
        not in ("user", "agent", "system", "consumer")
    ]
    gate("C1-AUDIT", "PASS" if not bad else "FAIL", "actor 审计")
    statuses = [g["status"] for g in gates.values()]
    if "FAIL" in statuses:
        verdict, code = "FAIL", 4
    elif "BLOCKED" in statuses:
        verdict, code = "BLOCKED", 3
    else:
        verdict, code = "CONTROLLED_L2_APPROVED", 0
    return {
        "item_id": item_id,
        "verdict": verdict,
        "exit_code": code,
        "gates": gates,
        "counts": {"events": len(evs)},
        "honesty": {
            "approval": "per_action_user",
            "external_sent": False,
            "limit": "仅审批+outbox 记录；未调用任何外部发送通道",
        },
    }


def list_approvals(store: S.OsGoalStore, item_id: str) -> List[Dict[str, Any]]:
    """列出某事项的 L2 审批单（含状态：pending/approved/denied/expired）。"""
    evs = _evs(store, item_id)
    reqs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    decs: Dict[str, Dict[str, Any]] = {}
    for e in evs:
        p = e.get("payload") or {}
        t = e.get("type")
        if t == "approval.requested" and p.get("approval_id"):
            aid = p["approval_id"]
            if aid not in reqs:
                order.append(aid)
            reqs[aid] = p
        elif t == "approval.decided" and p.get("approval_id"):
            decs[p["approval_id"]] = p
    out = []
    for aid in order:
        req = reqs[aid]
        dec = decs.get(aid)
        status = "pending"
        if dec:
            status = dec.get("decision") or "unknown"
        else:
            exp = _parse(req.get("expires_at"))
            if exp and datetime.now(BEIJING) >= exp:
                status = "expired"
        out.append(
            {
                "approval_id": aid,
                "action": req.get("action"),
                "consumer_id": req.get("consumer_id"),
                "summary": req.get("summary"),
                "status": status,
                "expires_at": req.get("expires_at"),
                "requested_at": req.get("requested_at"),
                "decided_at": (dec or {}).get("decided_at"),
                "decided_note": (dec or {}).get("note"),
            }
        )
    return out
