# -*- coding: utf-8 -*-
"""软件面流程（G1）：需求→推荐卡→授权→工单/认领→隔离执行→测试→试用→验收→返工 v2

复用 G0 同一本账（os_goal_store），不另立账本；事件名沿用合同 v1.1：
  brief.frozen / card.frozen / card.selected / grant.signed /
  workorder.created / workorder.claimed / artifact.produced / test.recorded /
  trial.opened / verdict.recorded / brief.revised
纪律：
- 本人动作（冻结需求/选卡/签授权/开试用/验收）actor.kind 必须 user；
- 首版只允许隔离环境 4 动作；本流程只实现 prototype.generate 的隔离产物；
- 执行器只写 <store.base_dir>/items/<item_id>/workspace/，无 shell、无网络、无外发；
- 认领默认 simulated（隔离验证），页面/判据必须标"未获真实认领"；
- 推荐卡在 G1 为规则辅助（非模型结论），诚实标注 source_kind=rule_assisted。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
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
ALLOWED_ACTIONS = ("prototype.generate", "prototype.preview",
                   "prototype.test", "workspace.cleanup")
USER_ACTOR = {"kind": "user", "id": "laotian", "seat": "laotian"}
SYSTEM_ACTOR = {"kind": "system", "id": "os_software_flow"}
CONSUMER_ACTOR = {"kind": "consumer", "id": "DEV-ISOLATED", "seat": "DEV-ISOLATED"}
WORKSPACE_REL = os.path.join("items", "%s", "workspace")


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise C.ContractError(msg)


def _item(store: S.OsGoalStore, item_id: str) -> Dict[str, Any]:
    try:
        return store.get_item(item_id)
    except KeyError:
        raise C.ContractError("事项不存在：%r" % item_id)


def _evs(store: S.OsGoalStore, item_id: str) -> List[Dict[str, Any]]:
    return sorted(store.events_for(item_id), key=lambda e: e.get("seq", 0))


def _find(evs: List[Dict[str, Any]], etype: str) -> Optional[Dict[str, Any]]:
    hits = [e for e in evs if e.get("type") == etype]
    return hits[-1] if hits else None


def _require_event(evs, etype, msg):
    ev = _find(evs, etype)
    _require(ev is not None, msg)
    return ev


def _require_user(ev, what: str) -> None:
    _require((ev.get("actor") or {}).get("kind") == "user",
             "%s 必须由本人执行（actor.kind=user）" % what)


def _workspace_dir(store: S.OsGoalStore, item_id: str) -> str:
    base = os.path.realpath(store.base_dir)
    ws = os.path.realpath(os.path.join(base, "items", item_id, "workspace"))
    _require(ws.startswith(base + os.sep) or ws == base,
             "工作区路径越出账本根：%r" % ws)
    return ws


# ---------------------------------------------------------------- 流程步骤
def freeze_brief(store, item_id, goals, success_criteria,
                 non_goals=None, constraints=None, idem=None):
    _item(store, item_id)
    evs = _evs(store, item_id)
    _require(_find(evs, "brief.frozen") is None,
             "需求已冻结；改需求请走 rework/brief.revised")
    _require(isinstance(goals, list) and goals, "需求至少 1 条目标")
    _require(isinstance(success_criteria, list) and success_criteria,
             "需求至少 1 条成功标准")
    for sc in success_criteria:
        _require(isinstance(sc, dict) and sc.get("id") and sc.get("statement")
                 and sc.get("verify_method"),
                 "成功标准必须含 id/statement/verify_method：%r" % (sc,))
    payload = {"brief_version": "v1", "goals": goals,
               "non_goals": non_goals or [], "constraints": constraints or [],
               "success_criteria": success_criteria}
    return store.append_event("brief.frozen", {"item_id": item_id}, payload,
                              actor=USER_ACTOR, idem=idem)


def freeze_card(store, item_id, recommended, discarded, acceptance_method,
                risks=None, evidence_refs=None, card_version="c1", idem=None):
    item = _item(store, item_id)
    evs = _evs(store, item_id)
    _require_event(evs, "brief.frozen", "先冻结需求，再出推荐卡")
    _require(isinstance(recommended, dict) and recommended.get("option")
             and recommended.get("why"), "推荐方案需含 option/why")
    _require(isinstance(discarded, list) and discarded, "至少 1 个舍弃方案")
    for d in discarded:
        _require(isinstance(d, dict) and d.get("option") and d.get("why"),
                 "舍弃方案需含 option/why")
    _require(bool(str(acceptance_method or "").strip()), "推荐卡需写验收办法")
    payload = {"card_version": card_version, "brief_version": "v1",
               "recommended": recommended, "discarded": discarded,
               "risks": risks or [], "acceptance_method": acceptance_method,
               "evidence_refs": evidence_refs or [],
               "disconfirm_refs": [],
               "source_kind": "rule_assisted",
               "label": "规则辅助（非模型结论）",
               "outcome_kind": item.get("outcome_kind")}
    return store.append_event("card.frozen", {"item_id": item_id}, payload,
                              actor=SYSTEM_ACTOR, idem=idem)


def select_card(store, item_id, card_version, idem=None):
    evs = _evs(store, item_id)
    card = _require_event(evs, "card.frozen", "先出推荐卡，再选卡")
    _require(str(card["payload"].get("card_version")) == str(card_version),
             "选卡版本与冻结卡不一致：%r ≠ %r"
             % (card_version, card["payload"].get("card_version")))
    return store.append_event("card.selected", {"item_id": item_id},
                              {"card_version": str(card_version)},
                              actor=USER_ACTOR, idem=idem)


def sign_grant(store, item_id, card_version, actions,
               valid_until=None, idem=None):
    evs = _evs(store, item_id)
    sel = _require_event(evs, "card.selected", "先选卡，再签授权")
    _require(str(sel["payload"].get("card_version")) == str(card_version),
             "授权卡版本与本人选卡不一致")
    _require(isinstance(actions, list) and actions, "授权动作不能为空")
    bad = [a for a in actions if a not in ALLOWED_ACTIONS]
    _require(not bad, "首版只允许隔离 4 动作，越权：%r" % bad)
    if not valid_until:
        valid_until = (datetime.now(BEIJING) + timedelta(days=1)).isoformat(
            timespec="seconds")
    payload = {"grant_id": "GRT-%s-%03d" % (time.strftime("%Y%m%d"),
                                            len(evs) + 1),
               "card_version": str(card_version), "actions": actions,
               "env": "isolated",
               "target": {"paths": [WORKSPACE_REL % item_id]},
               "valid_until": valid_until,
               "limits": {"max_cost_cny": 0, "reversibility_required": True},
               "signer": {"id": "laotian", "method": "session_nonce",
                          "nonce_id": "NONCE-%s" % time.strftime("%H%M%S")}}
    return store.append_event("grant.signed", {"item_id": item_id}, payload,
                              actor=USER_ACTOR, idem=idem)


def create_workorder(store, item_id, idem=None):
    evs = _evs(store, item_id)
    _require_event(evs, "brief.frozen", "先冻结需求")
    card = _require_event(evs, "card.frozen", "先出推荐卡")
    sel = _require_event(evs, "card.selected", "先本人选卡")
    grant = _require_event(evs, "grant.signed", "先本人签授权")
    _require(grant["payload"].get("card_version") == sel["payload"].get("card_version"),
             "授权与选卡版本不一致")
    _require("prototype.generate" in (grant["payload"].get("actions") or []),
             "授权未包含 prototype.generate")
    _require(_grant_valid(grant["payload"]), "授权已过期，请重新签准")
    payload = {"workorder_id": "WO-%s-%03d" % (time.strftime("%Y%m%d"),
                                               len(evs) + 1),
               "grant_id": grant["payload"].get("grant_id"),
               "card_version": card["payload"].get("card_version"),
               "action": "prototype.generate",
               "task_spec": "在隔离工作区生成单页原型并做静态自检",
               "prerequisites_checked": True,
               "authority_refs": [],
               "note": "真实消费者/正式工单未接通，认领只能标隔离验证"}
    return store.append_event("workorder.created", {"item_id": item_id},
                              payload, actor=SYSTEM_ACTOR, idem=idem)


def claim(store, item_id, consumer_id="DEV-ISOLATED", method="simulated",
          isolation_label=None, idem=None):
    evs = _evs(store, item_id)
    wo = _require_event(evs, "workorder.created", "先建工单")
    _require(method == "simulated",
             "G1 未接通真实消费者，只允许 simulated 认领（隔离验证）")
    _require(bool(str(isolation_label or "").strip()),
             "隔离模拟认领必须带标注（未获真实认领）")
    payload = {"workorder_id": wo["payload"].get("workorder_id"),
               "claim": {"consumer_id": consumer_id, "seat": consumer_id,
                         "method": method, "claimed_at": _now()}}
    if method == "simulated":
        payload["isolation_label"] = isolation_label
    return store.append_event("workorder.claimed", {"item_id": item_id},
                              payload, actor={"kind": "consumer",
                                              "id": consumer_id,
                                              "seat": consumer_id},
                              idem=idem)


def _grant_valid(grant_payload: Dict[str, Any]) -> bool:
    try:
        until = datetime.fromisoformat(str(grant_payload.get("valid_until")))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=BEIJING)
    return datetime.now(BEIJING) < until


def execute(store, item_id, idem=None):
    item = _item(store, item_id)
    evs = _evs(store, item_id)
    brief = _require_event(evs, "brief.frozen", "先冻结需求，再执行")
    card = _require_event(evs, "card.frozen", "先出推荐卡，再执行")
    grant = _require_event(evs, "grant.signed", "先签授权，再执行")
    _require_event(evs, "workorder.created", "先建工单，再执行")
    _require_event(evs, "workorder.claimed", "先认领，再执行")
    _require("prototype.generate" in (grant["payload"].get("actions") or []),
             "授权未包含 prototype.generate，拒绝执行")
    _require(_grant_valid(grant["payload"]), "授权已过期，请重新签准")
    ws = _workspace_dir(store, item_id)
    os.makedirs(ws, exist_ok=True)
    version = "a%d" % (len([e for e in evs if e.get("type") == "artifact.produced"]) + 1)
    title = item.get("title") or "零界软件成果"
    goals = brief["payload"].get("goals") or []
    criteria = brief["payload"].get("success_criteria") or []
    recommended = card["payload"].get("recommended") or {}
    feedback = ""
    last_verdict = _find(evs, "verdict.recorded")
    if last_verdict and last_verdict["payload"].get("feedback"):
        feedback = str(last_verdict["payload"]["feedback"])
    page = _render_page(title, goals, criteria, recommended, feedback, version)
    path = os.path.join(ws, "index.html")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    data = page.encode("utf-8")
    sha = "sha256:" + hashlib.sha256(data).hexdigest()
    # 隔离自检：不依赖浏览器/网络，只做静态断言
    checks = [("file_exists", os.path.isfile(path)),
              ("size>200", len(data) > 200),
              ("has_artifact_marker", ('data-artifact="%s"' % version) in page),
              ("has_first_goal", (goals[0] in page) if goals else False),
              ("has_feedback_attr", "data-feedback=" in page),
              ("feedback_consistent", (html.escape(feedback) in page) if feedback
               else ("暂无返工反馈" in page))]
    ok = all(passed for _, passed in checks)
    log = json.dumps({"checks": checks, "ok": ok, "version": version,
                      "sha256": sha}, ensure_ascii=False, indent=1)
    with open(os.path.join(ws, "test.log"), "w", encoding="utf-8") as fh:
        fh.write(log + "\n")
    out_sha = "sha256:" + hashlib.sha256(log.encode("utf-8")).hexdigest()
    art = store.append_event(
        "artifact.produced", {"item_id": item_id},
        {"artifact_id": "ART-%s-%s" % (item_id, version), "version": version,
         "workorder_id": _find(evs, "workorder.created")["payload"].get("workorder_id"),
         "path": path, "path_rel": os.path.join("items", item_id, "workspace", "index.html"),
         "sha256": sha, "bytes": len(data),
         "preview_uri": None,  # 预览路由 G1b 再接；先不挂死链
         "rollback": {"method": "delete_dir",
                      "recipe": "撤回=删除 %s（进回收站）；事件与指纹保留对账" % ws}},
        actor=CONSUMER_ACTOR, idem=idem)
    test = store.append_event(
        "test.recorded", {"item_id": item_id},
        {"evidence_id": "TES-%s-%s" % (item_id, version),
         "artifact_id": art["payload"]["artifact_id"],
         "type": "unit", "command": "os_software_flow.static_selfcheck",
         "result": "pass" if ok else "fail",
         "output_sha256": out_sha,
         "executed_by": "os_software_flow.selfcheck（非独立QA）"},
        actor=CONSUMER_ACTOR)
    return {"artifact": art["payload"], "test": test["payload"], "ok": ok}


def _render_page(title, goals, criteria, recommended, feedback, version) -> str:
    g = "".join("<li>%s</li>" % html.escape(str(x)) for x in goals)
    c = "".join("<li>%s —— 验证：%s</li>"
                % (html.escape(str(sc.get("statement"))),
                   html.escape(str(sc.get("verify_method"))))
                for sc in criteria)
    fb = html.escape(feedback) if feedback else "（首版，暂无返工反馈）"
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>%s</title></head><body data-artifact=\"%s\">"
        "<h1>%s</h1><p>隔离环境产物 · 版本 %s</p>"
        "<h2>目标</h2><ul>%s</ul>"
        "<h2>验收标准</h2><ul>%s</ul>"
        "<h2>推荐方案</h2><p>%s：%s</p>"
        "<h2>反馈修订</h2><p data-feedback=\"%s\">%s</p>"
        "<p>撤回：删除本目录即回到执行前；指纹见 test.log。</p>"
        "</body></html>" % (html.escape(title), version, html.escape(title),
                            version, g, c,
                            html.escape(str(recommended.get("option"))),
                            html.escape(str(recommended.get("why"))), fb, fb))


def open_trial(store, item_id, idem=None):
    evs = _evs(store, item_id)
    art = _require_event(evs, "artifact.produced", "先产出成果，再打开试用")
    test = _require_event(evs, "test.recorded", "先有测试回执，再打开试用")
    _require(test["payload"].get("result") == "pass", "测试未通过，不能进入试用")
    return store.append_event("trial.opened", {"item_id": item_id},
                              {"artifact_id": art["payload"].get("artifact_id"),
                               "opened_by": "laotian"},
                              actor=USER_ACTOR, idem=idem)


def record_verdict(store, item_id, decision, feedback="", idem=None):
    evs = _evs(store, item_id)
    art = _require_event(evs, "artifact.produced", "先有成果，再验收")
    _require_event(evs, "trial.opened", "先试用，再给验收回执")
    _require(decision in ("accepted", "changes_requested"),
             "验收结论必须是 accepted / changes_requested")
    if decision == "changes_requested":
        _require(bool(str(feedback or "").strip()), "返工必须写一句修改意见")
    last = _find(evs, "verdict.recorded")
    if last and last["payload"].get("decision") == "accepted":
        revised = [e for e in evs if e.get("type") == "brief.revised"
                   and e.get("seq", 0) > last.get("seq", 0)]
        _require(bool(revised), "已验收；如需修改请先返工（rework）")
    return store.append_event("verdict.recorded", {"item_id": item_id},
                              {"verdict_id": "VER-%s-%03d" % (time.strftime("%Y%m%d"),
                                                              len(evs) + 1),
                               "artifact_id": art["payload"].get("artifact_id"),
                               "decision": decision, "feedback": feedback},
                              actor=USER_ACTOR, idem=idem)


def rework(store, item_id, feedback, idem=None):
    """返工第一步：升需求 v2 + 推荐卡 c2；后续选卡/签授权/工单/执行由本人逐步走。"""
    evs = _evs(store, item_id)
    last = _require_event(evs, "verdict.recorded", "没有验收回执，无法返工")
    _require(last["payload"].get("decision") == "changes_requested",
             "最近一次验收不是返工，无需 rework")
    _require(bool(str(feedback or "").strip()), "返工需要修改意见")
    prev_brief = _require_event(evs, "brief.frozen", "缺少需求版本")
    prev_card = _require_event(evs, "card.frozen", "缺少推荐卡")
    store.append_event("brief.revised", {"item_id": item_id},
                       {"brief_version": "v2", "parent_version": "v1",
                        "changes": [feedback],
                        "goals": prev_brief["payload"].get("goals"),
                        "success_criteria": prev_brief["payload"].get("success_criteria")},
                       actor=USER_ACTOR)
    store.append_event("card.frozen", {"item_id": item_id},
                       {**prev_card["payload"], "card_version": "c2",
                        "brief_version": "v2",
                        "acceptance_method": "%s（返工后复验）"
                        % prev_card["payload"].get("acceptance_method")},
                       actor=SYSTEM_ACTOR)
    return {"brief_version": "v2", "card_version": "c2",
            "next": "本人选卡 c2 → 签授权 → 建工单/认领 → 执行 a2"}

# ---------------------------------------------------------------- 判据
def chain_report(store, item_id) -> Dict[str, Any]:
    item = _item(store, item_id)
    evs = _evs(store, item_id)
    checks: List[Dict[str, Any]] = []

    def fail(cid, msg):
        checks.append({"id": cid, "status": "FAIL", "detail": msg})

    def gate(gid, ok, detail):
        return {"gate": gid, "status": "PASS" if ok else "BLOCKED",
                "detail": detail}

    def gate_status(gid, status, detail):
        return {"gate": gid, "status": status, "detail": detail}

    brief = _find(evs, "brief.frozen") or _find(evs, "brief.revised")
    card = _find(evs, "card.frozen")
    sel = _find(evs, "card.selected")
    grant = _find(evs, "grant.signed")
    wo = _find(evs, "workorder.created")
    clm = _find(evs, "workorder.claimed")
    art = _find(evs, "artifact.produced")
    test = _find(evs, "test.recorded")
    trial = _find(evs, "trial.opened")
    verdict = _find(evs, "verdict.recorded")
    base = os.path.realpath(store.base_dir)
    gates: Dict[str, Dict[str, Any]] = {}
    gates["G0"] = gate("G0", bool(item), "事项已建档")

    if brief:
        try:
            _require_user(brief, "需求冻结")
        except C.ContractError as exc:
            fail("G1_ACTOR", str(exc))
        criteria = brief["payload"].get("success_criteria") or []
        if not criteria or any(not sc.get("verify_method") for sc in criteria):
            fail("G1_CRITERIA", "成功标准缺失或未写 verify_method")
        gates["G1"] = gate("G1", not any(c["id"].startswith("G1_") for c in checks),
                           "本人冻结需求（v%s）" % brief["payload"].get("brief_version"))
    else:
        gates["G1"] = gate("G1", False, "需求未冻结")

    if card and card["payload"].get("recommended") and card["payload"].get("discarded"):
        src = card["payload"].get("source_kind") or "unknown"
        detail = "推荐卡来源：%s" % src
        if src == "rule_assisted":
            gates["G2"] = gate_status("G2", "ISOLATED_ONLY",
                                      detail + "（仅规则辅助，非模型结论）")
        elif src == "model_call":
            gates["G2"] = gate("G2", True, detail)
        else:
            fail("G2_SOURCE", "推荐卡来源未声明或非法：%r" % src)
            gates["G2"] = gate("G2", False, detail)
    else:
        gates["G2"] = gate("G2", False, "推荐卡缺失或字段不全")

    if sel and card and str(sel["payload"].get("card_version")) == \
            str(card["payload"].get("card_version")):
        try:
            _require_user(sel, "选卡")
        except C.ContractError as exc:
            fail("G3_ACTOR", str(exc))
        gates["G3"] = gate("G3", True, "本人选卡 %s" % sel["payload"].get("card_version"))
    else:
        gates["G3"] = gate("G3", False, "未本人选卡或版本与冻结卡不一致")

    if grant:
        try:
            _require_user(grant, "授权签准")
        except C.ContractError as exc:
            fail("G4_ACTOR", str(exc))
        gp = grant["payload"]
        bad = [a for a in (gp.get("actions") or []) if a not in ALLOWED_ACTIONS]
        if bad:
            fail("G4_SCOPE", "越权动作：%r" % bad)
        if gp.get("env") != "isolated":
            fail("G4_ENV", "首版只允许 isolated，实际 %r" % gp.get("env"))
        signer = gp.get("signer") or {}
        if signer.get("method") not in ("session_nonce", "local_password_session") \
                or not (signer.get("nonce_id") or signer.get("session_id")):
            fail("G4_SIGNER", "签准方式不可信或缺 nonce 证据")
        if not _grant_valid(gp):
            fail("G4_EXPIRED", "授权已过期（valid_until=%s）" % gp.get("valid_until"))
        if card and str(gp.get("card_version")) != str(card["payload"].get("card_version")):
            fail("G4_CARD", "授权未绑定当前卡版本")
        gates["G4"] = gate("G4", not any(c["id"].startswith("G4_") for c in checks),
                           "本人签准 %d 个隔离动作" % len(gp.get("actions") or []))
    else:
        gates["G4"] = gate("G4", False, "未签授权")

    if wo and clm:
        method = (clm["payload"].get("claim") or {}).get("method")
        if method == "real":
            fail("G5_REAL_NOT_ALLOWED", "G1 未接通真实消费者，real 认领不可采信")
            gates["G5"] = gate("G5", False, "real 认领不可采信（未接通消费者注册）")
        elif method == "simulated" and clm["payload"].get("isolation_label"):
            gates["G5"] = gate_status("G5", "ISOLATED_ONLY",
                                      "隔离模拟认领（未获真实认领），不得称已派工")
        else:
            fail("G5_LABEL", "隔离认领缺 isolation_label")
            gates["G5"] = gate("G5", False, "认领标注缺失")
    else:
        gates["G5"] = gate("G5", False, "无工单或未认领")

    if art and test:
        ap = art["payload"]
        path = os.path.realpath(str(ap.get("path") or ""))
        if not path.startswith(base + os.sep):
            fail("G6_PATH", "产物路径越出账本根：%s" % path)
        if not str(ap.get("sha256") or "").startswith("sha256:"):
            fail("G6_SHA", "产物缺 sha256 指纹")
        if not (ap.get("rollback") or {}).get("recipe"):
            fail("G6_ROLLBACK", "产物缺撤回配方")
        if str(test["payload"].get("artifact_id")) != str(ap.get("artifact_id")):
            fail("G6_TEST_TARGET", "测试回执未指向本产物")
        if test["payload"].get("result") != "pass" or \
                not str(test["payload"].get("output_sha256") or "").startswith("sha256:"):
            fail("G6_TEST", "测试未通过或缺输出指纹")
        gates["G6"] = gate("G6", not any(c["id"].startswith("G6_") for c in checks),
                           "隔离产物 %s + 测试回执" % ap.get("version"))
    else:
        gates["G6"] = gate("G6", False, "无产物或测试回执")

    if trial and verdict:
        try:
            _require_user(trial, "试用")
            _require_user(verdict, "验收回执")
        except C.ContractError as exc:
            fail("G7_ACTOR", str(exc))
        if verdict["payload"].get("decision") == "accepted":
            gates["G7"] = gate("G7", not any(c["id"].startswith("G7_") for c in checks),
                               "本人验收 accepted")
        else:
            gates["G7"] = gate("G7", False, "本人要求返工（REWORK）")
    else:
        gates["G7"] = gate("G7", False, "未试用或未验收")

    if any(c["status"] == "FAIL" for c in checks):
        verdict_v, code = "FAIL", 1
    elif any(g["status"] == "BLOCKED" for g in gates.values()):
        verdict_v, code = "BLOCKED", 3
    elif any(g["status"] == "ISOLATED_ONLY" for g in gates.values()):
        verdict_v, code = "ISOLATED_ONLY", 3
    else:
        verdict_v, code = "PASS", 0
    return {"gates": gates, "checks": checks, "verdict": verdict_v,
            "exit_code": code, "event_count": len(evs),
            "honesty": {"card_source": (card or {}).get("payload", {}).get("source_kind"),
                        "claim": ((clm or {}).get("payload", {}).get("claim") or {}).get("method")}}