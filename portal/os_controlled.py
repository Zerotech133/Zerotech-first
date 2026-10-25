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
import os
import re
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
CONTROLLED_ACTIONS = ("artifact.generate", "workspace.write", "test.run", "workspace.cleanup")
L1_ACTIONS = set(CONTROLLED_ACTIONS)
L2_ACTIONS = {"external.send", "publish", "production.deploy", "trade.execute"}
SIGNER_METHODS = ("session_nonce", "local_password_session")
CONSUMER_KINDS = ("agent", "human", "service")
LEASE_MINUTES = 30
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(html|md|txt|json|log)$")


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _valid_until(hours: int = 2) -> str:
    return (datetime.now(BEIJING) + timedelta(hours=hours)).isoformat(timespec="seconds")


def _parse(ts: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=BEIJING)


def _valid_grant(payload: Dict[str, Any]) -> bool:
    until = _parse(payload.get("valid_until"))
    return bool(until and datetime.now(BEIJING) < until)


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
    for e in _all_events(store):
        p = e.get("payload") or {}
        if e.get("type") == "consumer.registered" and p.get("consumer_id") == consumer_id:
            return p
    return None


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
def register_consumer(store: S.OsGoalStore, consumer_id: str, kind: str = "agent",
                      display_name: str = "", capabilities: Optional[List[str]] = None,
                      owner_id: str = "laotian", idem: Optional[str] = None) -> Dict[str, Any]:
    _require(bool(ID_RE.match(str(consumer_id or ""))), "consumer_id 非法（3-64 位字母数字._-）")
    _require(kind in CONSUMER_KINDS, "消费者类型必须是 %s" % (CONSUMER_KINDS,))
    caps = [str(c) for c in (capabilities or ["artifact.generate"])]
    _require(all(c in L1_ACTIONS for c in caps), "能力必须属于受控 L1 动作")
    existing = _consumer(store, consumer_id)
    if existing:
        return existing
    ev = store.append_event(
        "consumer.registered", {},
        {"consumer_id": consumer_id, "kind": kind,
         "display_name": display_name or consumer_id,
         "capabilities": caps, "owner_id": owner_id,
         "status": "active", "registered_at": _now()},
        actor=USER_ACTOR, idem=idem)
    return ev["payload"]


def list_consumers(store: S.OsGoalStore) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for e in _all_events(store):
        if e.get("type") == "consumer.registered":
            p = e.get("payload") or {}
            if p.get("consumer_id") not in seen:
                seen.add(p.get("consumer_id"))
                out.append(p)
    return out


def deactivate_consumer(store: S.OsGoalStore, consumer_id: str, reason: str = "manual",
                        idem: Optional[str] = None) -> Dict[str, Any]:
    _active_consumer(store, consumer_id)
    ev = store.append_event(
        "consumer.registered", {},
        {"consumer_id": consumer_id, "status": "inactive",
         "deactivated_at": _now(), "reason": reason},
        actor=USER_ACTOR, idem=idem)
    return ev["payload"]



# ---------------- 签准 / 工单 ----------------
def sign_real_grant(store: S.OsGoalStore, item_id: str, actions: List[str],
                    nonce: str, signer_method: str = "session_nonce",
                    valid_hours: int = 2, idem: Optional[str] = None) -> Dict[str, Any]:
    _item(store, item_id)
    _require(signer_method in SIGNER_METHODS, "签准方式必须是 %s" % (SIGNER_METHODS,))
    _require(isinstance(nonce, str) and len(nonce.strip()) >= 8, "真实签准必须带 nonce（≥8 字符）")
    acts = [str(a) for a in (actions or [])]
    _require(bool(acts), "签准动作不能为空")
    _require(all(a in L1_ACTIONS for a in acts), "真实签准只允许 L1 受控动作：%s" % (CONTROLLED_ACTIONS,))
    _require(1 <= int(valid_hours) <= 24, "真实签准有效期 1-24 小时")
    ev = store.append_event(
        "grant.signed", {"item_id": item_id},
        {"grant_id": "GRT-%s-%03d" % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
         "grant_version": "g1", "actions": sorted(set(acts)), "env": "controlled",
         "signer": {"kind": "user", "id": "laotian", "method": signer_method,
                    "nonce_sha256": "sha256:" + hashlib.sha256(nonce.encode()).hexdigest()},
         "valid_until": _valid_until(int(valid_hours))},
        actor=USER_ACTOR, idem=idem)
    return ev["payload"]


def revoke_grant(store: S.OsGoalStore, item_id: str, grant_id: str,
                 idem: Optional[str] = None) -> Dict[str, Any]:
    _item(store, item_id)
    ev = store.append_event(
        "grant.revoked", {"item_id": item_id},
        {"grant_id": grant_id, "revoked_at": _now()},
        actor=USER_ACTOR, idem=idem)
    return ev["payload"]


def create_formal_workorder(store: S.OsGoalStore, item_id: str,
                            acceptance: str, required_capabilities: Optional[List[str]] = None,
                            idem: Optional[str] = None) -> Dict[str, Any]:
    _item(store, item_id)
    evs = _evs(store, item_id)
    grant = _find(evs, "grant.signed")
    _require(grant is not None, "先真实签准，再建正式工单")
    _require(grant["payload"].get("env") == "controlled", "正式工单必须绑定 controlled 环境的真实签准")
    _require(_valid_grant(grant["payload"]), "签准已过期，请重新签准")
    _require(bool(str(acceptance or "").strip()), "正式工单必须写验收口径")
    caps = [str(c) for c in (required_capabilities or ["artifact.generate"])]
    _require(all(c in grant["payload"].get("actions", []) for c in caps),
             "工单要求超出签准动作范围")
    ev = store.append_event(
        "workorder.formal", {"item_id": item_id},
        {"workorder_id": "WO-%s-%03d" % (time.strftime("%Y%m%d"), len(evs) + 1),
         "acceptance": acceptance, "required_capabilities": caps,
         "grant_id": grant["payload"].get("grant_id"), "env": "controlled",
         "created_by": "laotian", "created_at": _now()},
        actor=SYSTEM_ACTOR, idem=idem)
    return ev["payload"]


# ---------------- 风控裁决 ----------------
def risk_verdict(store: S.OsGoalStore, item_id: str, action: str,
                 consumer_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None,
                 idem: Optional[str] = None) -> Dict[str, Any]:
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
        "risk.verdict", {"item_id": item_id},
        {"verdict_id": "RSK-%s-%03d" % (time.strftime("%Y%m%d"), len(_evs(store, item_id)) + 1),
         "action": action, "decision": decision, "risk_level": level,
         "reasons": reasons, "policy": "os-risk-v0",
         "context": context or {}},
        actor=SYSTEM_ACTOR, idem=idem)
    return ev["payload"]


# ---------------- 真实认领 / 受控产物 ----------------
def real_claim(store: S.OsGoalStore, item_id: str, consumer_id: str,
               idem: Optional[str] = None) -> Dict[str, Any]:
    _active_consumer(store, consumer_id)
    evs = _evs(store, item_id)
    wo = _find(evs, "workorder.formal")
    _require(wo is not None, "先建正式工单，再真实认领")
    grant = _find(evs, "grant.signed")
    _require(grant is not None and grant["payload"].get("env") == "controlled",
             "真实认领必须有 controlled 真实签准")
    _require(_valid_grant(grant["payload"]), "签准已过期，请重新签准")
    caps = (wo["payload"].get("required_capabilities") or [])
    _require(all(c in grant["payload"].get("actions", []) for c in caps),
             "签准未覆盖工单要求动作")
    lease = (datetime.now(BEIJING) + timedelta(minutes=LEASE_MINUTES)).isoformat(timespec="seconds")
    ev = store.append_event(
        "workorder.claimed", {"item_id": item_id},
        {"workorder_id": wo["payload"].get("workorder_id"),
         "claim": {"consumer_id": consumer_id, "method": "real",
                   "claimed_at": _now(), "lease_until": lease,
                   "authority_ref": grant["payload"].get("grant_id")}},
        actor={"kind": "consumer", "id": consumer_id, "seat": consumer_id},
        idem=idem)
    return ev["payload"]


def _claim_active(claim_event: Optional[Dict[str, Any]], consumer_id: str) -> bool:
    if not claim_event:
        return False
    claim = (claim_event.get("payload") or {}).get("claim") or {}
    until = _parse(claim.get("lease_until"))
    return (claim.get("method") == "real" and claim.get("consumer_id") == consumer_id
            and bool(until and datetime.now(BEIJING) < until))


def produce_controlled_artifact(store: S.OsGoalStore, item_id: str, consumer_id: str,
                                filename: str, content: str,
                                idem: Optional[str] = None) -> Dict[str, Any]:
    _active_consumer(store, consumer_id)
    _require(bool(FILE_RE.match(str(filename or ""))), "文件名必须是安全文件名（html/md/txt/json/log）")
    _require(bool(str(content or "").strip()), "产物内容不能为空")
    evs = _evs(store, item_id)
    claim = _find(evs, "workorder.claimed")
    _require(_claim_active(claim, consumer_id), "没有有效的真实认领（或租约过期）")
    grant = _find(evs, "grant.signed")
    wo = _find(evs, "workorder.formal")
    _require(grant is not None and wo is not None, "缺少签准或正式工单")
    _require(grant["payload"].get("env") == "controlled", "签准环境不是 controlled")
    _require(_valid_grant(grant["payload"]), "签准已过期")
    _require("artifact.generate" in (grant["payload"].get("actions") or []),
             "签准未包含 artifact.generate")
    safe = os.path.realpath(os.path.join(_workspace(store, item_id), filename))
    base = os.path.realpath(store.base_dir)
    _require(safe.startswith(os.path.realpath(os.path.join(base, "items", item_id, "workspace")) + os.sep),
             "产物路径越界")
    # 风控：执行前必须有 allow 裁决
    risk = _find(evs, "risk.verdict")
    if not (risk and risk["payload"].get("action") == "artifact.generate"
            and risk["payload"].get("decision") == "allow"):
        risk_ev = risk_verdict(store, item_id, "artifact.generate", consumer_id=consumer_id,
                               context={"source": "produce_artifact"})
        _require(risk_ev.get("decision") == "allow", "风控未放行：%s" % risk_ev.get("reasons"))
    data = content.encode("utf-8")
    with open(safe, "wb") as fh:
        fh.write(data)
    sha = "sha256:" + hashlib.sha256(data).hexdigest()
    art = store.append_event(
        "artifact.produced", {"item_id": item_id},
        {"artifact_id": "ART-%s-C0" % item_id, "version": "c1",
         "workorder_id": wo["payload"].get("workorder_id"),
         "path": safe, "path_rel": os.path.join("items", item_id, "workspace", filename),
         "sha256": sha, "bytes": len(data), "env": "controlled",
         "consumer": {"kind": "consumer", "id": consumer_id},
         "rollback": {"method": "delete_dir",
                      "recipe": "撤回=删除 %s（进回收站）；事件与指纹保留对账"
                                % os.path.dirname(safe)}},
        actor={"kind": "consumer", "id": consumer_id, "seat": consumer_id}, idem=idem)
    # 自检回执（与 G1 一致：自检≠独立 QA）
    store.append_event(
        "test.recorded", {"item_id": item_id},
        {"evidence_id": "TES-%s-C0" % item_id,
         "artifact_id": art["payload"]["artifact_id"],
         "type": "unit", "command": "os_controlled.static_selfcheck",
         "result": "pass" if len(data) > 0 and safe.startswith(base) else "fail",
         "output_sha256": sha,
         "executed_by": "os_controlled.selfcheck（非独立QA）"},
        actor=SYSTEM_ACTOR)
    return {"artifact": art["payload"], "risk": risk["payload"] if risk else {}, "claim": claim["payload"]}


# ---------------- 审计链 ----------------
def chain_report_controlled(store: S.OsGoalStore, item_id: str) -> Dict[str, Any]:
    evs = _evs(store, item_id)
    item = _item(store, item_id)
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
        ok = (p.get("env") == "controlled" and _valid_grant(p)
              and (p.get("signer") or {}).get("kind") == "user")
        gate("C0-AUTH", "PASS" if ok else "FAIL",
             "真实签准 env=%s 有效期至 %s" % (p.get("env"), p.get("valid_until")))
    if wo is None:
        gate("C0-WO", "BLOCKED", "缺少正式工单")
    else:
        ok = bool(wo["payload"].get("acceptance")) and wo["payload"].get("env") == "controlled"
        gate("C0-WO", "PASS" if ok else "FAIL", "正式工单 %s" % wo["payload"].get("workorder_id"))
    if claim is None:
        gate("C0-CLAIM", "BLOCKED", "尚未真实认领")
    else:
        claim_p = claim["payload"].get("claim") or {}
        consumer = _consumer(store, claim_p.get("consumer_id"))
        active = bool(consumer and consumer.get("status") == "active")
        lease = _parse(claim_p.get("lease_until"))
        ok = (claim_p.get("method") == "real" and active
              and bool(lease and datetime.now(BEIJING) < lease))
        gate("C0-CLAIM", "PASS" if ok else "FAIL",
             "consumer=%s method=%s" % (claim_p.get("consumer_id"), claim_p.get("method")))
    if risk is None:
        gate("C0-RISK", "BLOCKED", "缺少风控裁决")
    else:
        rp = risk["payload"]
        ok = rp.get("action") == "artifact.generate" and rp.get("decision") == "allow"
        gate("C0-RISK", "PASS" if ok else "FAIL",
             "action=%s decision=%s" % (rp.get("action"), rp.get("decision")))
    if art is None:
        gate("C0-ART", "BLOCKED", "尚未产出受控产物")
    else:
        ap = art["payload"]
        ok = (ap.get("env") == "controlled" and str(ap.get("sha256", "")).startswith("sha256:"))
        gate("C0-ART", "PASS" if ok else "FAIL", "artifact=%s sha=%s" % (ap.get("artifact_id"), ap.get("sha256")))
    bad_actor = [e.get("seq") for e in evs
                 if (e.get("actor") or {}).get("kind") not in ("user", "agent", "system", "consumer")]
    gate("C0-AUDIT", "PASS" if not bad_actor else "FAIL",
         "actor 审计：%s" % ("全部合法" if not bad_actor else "异常 seq=%s" % bad_actor))

    statuses = [g["status"] for g in gates.values()]
    if "FAIL" in statuses:
        verdict, exit_code = "FAIL", 4
    elif "BLOCKED" in statuses:
        verdict, exit_code = "BLOCKED", 3
    else:
        verdict, exit_code = "CONTROLLED_ONLY", 0
    return {"item_id": item_id, "verdict": verdict, "exit_code": exit_code,
            "gates": gates, "counts": {"events": len(evs)},
            "honesty": {"claim": "real", "env": "controlled",
                        "limit": "L2 外部动作仍被风控拒绝；本链只证本地受控可执行"}}
