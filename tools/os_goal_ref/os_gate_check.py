# -*- coding: utf-8 -*-
"""OS 首版「单事项闭环」合同校验器 v1.0（WP0 参考实现，非生产代码）

只读一个事项目录下的 events.jsonl（append-only 事件账本），逐项校验：
  1) 事件信封完整性 + sha256 哈希链（防篡改/防补写）
  2) 序号、时间、item_id 一致性（防跨事项拼接）
  3) 状态机（防跳状态：无认领不得执行、无产物不得试用、无试用不得验收）
  4) 引用与版本（推荐卡→需求版本、授权→卡版本、工单→授权、产物→工单、回执→产物）
  5) 授权边界（动作白名单、隔离环境、有效期、本人签准方式）
  6) 诚实性（真模型调用/规则代行/mock 必须可区分；代行不得冒充模型结论）
  7) 七道闸门 G0-G7 与最终判定

退出码：
  0 = 全链通过（G0-G7 全 PASS，且用户本人验收 accepted）
  3 = 未通过但链路诚实（BLOCKED：权威前置未核验 / ISOLATED_ONLY：只有隔离认领）
  1 = 合同失败（存在 FAIL 检查项）
  2 = 输入错误（文件缺失/无法解析）

设计纪律：
  - 只读，不写任何文件；零第三方依赖（jsonschema 可选，不依赖）。
  - 页面文案、数据库状态、单测 PASS 均不能替代本校验器的证据链。
  - 与仓库既有 ResultEvidence 契约对齐：验收回执 receipt_uri 为空即视为
    unverified，不得算作已验收（见 portal/gen_result_evidence.py 注释）。

用法：
  python os_gate_check.py <item_dir>          # 人类可读
  python os_gate_check.py <item_dir> --json   # JSON（给页面/CI）
  python os_gate_check.py --help
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = "os-item/1.0"
LEDGER_NAME = "events.jsonl"

#: 首版允许的动作白名单（隔离、可逆、非交易）。任何不在表内的动作一律拒绝。
ALLOWED_ACTIONS = {
    "prototype.generate",   # 在隔离工作区生成可试用原型
    "prototype.preview",    # 打开只读预览
    "prototype.test",       # 运行自动化测试
    "workspace.cleanup",    # 撤回/清理隔离产物
}
FORBIDDEN_ACTIONS_HINT = ("production.", "trade.", "deploy.", "external.send",
                          "data.write.prod", "payment.", "publish.")
#: 首版只允许隔离环境
ALLOWED_ENV = {"isolated"}
#: 隔离产物根前缀（相对仓库根）；产物路径必须被限制在事项目录内
ISOLATION_ROOT = "portal/data/os_items/"
#: 用户本人签准方式（服务端会话+一次性 nonce；不接受前端自报 confirmed_by）
TRUSTED_SIGNER_METHODS = {"session_nonce", "local_password_session"}
#: 事件类型 → 合法前驱状态集合（状态机，防跳级）
#: 状态：CLARIFYING→ARGUMENT→AUTHORIZE→PREFLIGHT→EXECUTING→TRIAL_READY→ACCEPTED
#:       另有 REWORK / BLOCKED / FAILED / CANCELLED 旁路
S = {
    "NEW": "NEW",
    "CLARIFYING": "CLARIFYING",
    "ARGUMENT": "ARGUMENT",
    "AUTHORIZE": "AUTHORIZE",
    "PREFLIGHT": "PREFLIGHT",
    "EXECUTING": "EXECUTING",
    "TRIAL_READY": "TRIAL_READY",
    "ACCEPTED": "ACCEPTED",
    "REWORK": "REWORK",
    "BLOCKED": "BLOCKED",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
}
TRANSITIONS: Dict[str, Tuple[Iterable[str], str]] = {
    "item.created": ((S["NEW"],), S["CLARIFYING"]),
    "brief.drafted": ((S["CLARIFYING"], S["REWORK"]), S["CLARIFYING"]),
    "brief.frozen": ((S["CLARIFYING"], S["REWORK"]), S["ARGUMENT"]),
    "argument.recorded": ((S["ARGUMENT"],), S["ARGUMENT"]),
    "card.drafted": ((S["ARGUMENT"],), S["ARGUMENT"]),
    "card.frozen": ((S["ARGUMENT"],), S["AUTHORIZE"]),
    "card.selected": ((S["AUTHORIZE"],), S["AUTHORIZE"]),
    "grant.signed": ((S["AUTHORIZE"],), S["PREFLIGHT"]),
    "gate.blocked": ((S["PREFLIGHT"], S["AUTHORIZE"], S["EXECUTING"],
                      S["CLARIFYING"], S["ARGUMENT"]), S["BLOCKED"]),
    "workorder.created": ((S["PREFLIGHT"],), S["PREFLIGHT"]),
    "workorder.claimed": ((S["PREFLIGHT"],), S["EXECUTING"]),
    "artifact.produced": ((S["EXECUTING"],), S["EXECUTING"]),
    "test.recorded": ((S["EXECUTING"],), S["EXECUTING"]),
    "trial.opened": ((S["TRIAL_READY"],), S["TRIAL_READY"]),
    "verdict.recorded": ((S["TRIAL_READY"],), S["ACCEPTED"]),  # 具体去向按 decision 再判
    "workorder.failed": ((S["EXECUTING"],), S["FAILED"]),
    "item.cancelled": ((S["CLARIFYING"], S["ARGUMENT"], S["AUTHORIZE"],
                        S["PREFLIGHT"], S["EXECUTING"], S["TRIAL_READY"]),
                       S["CANCELLED"]),
}
#: 需要「本人」actor 的事件类型（agent 不得代为签准/验收）
USER_ONLY_EVENTS = {"brief.frozen", "card.selected", "grant.signed",
                    "trial.opened", "verdict.recorded"}
REQUIRED_ROLES = {"诸葛亮", "赵云", "田丰"}


# ---------------------------------------------------------------- 基础工具

def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def event_hash(ev: Dict[str, Any]) -> str:
    body = {k: v for k, v in ev.items() if k != "hash"}
    return sha256_text(canonical(body))


def load_events(item_dir: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    path = os.path.join(item_dir, LEDGER_NAME)
    if not os.path.isfile(path):
        alt = item_dir if os.path.isfile(item_dir) else None
        if alt:
            path = alt
        else:
            return [], "事件账本不存在: %s" % path
    events: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return events, "第 %d 行不是合法 JSON: %s" % (lineno, exc)
                if not isinstance(rec, dict):
                    return events, "第 %d 行不是 JSON 对象" % lineno
                events.append(rec)
    except OSError as exc:
        return [], "读取失败: %s" % exc
    return events, None


class Report:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []
        self.gates: Dict[str, Dict[str, Any]] = {
            "G%d" % i: {"gate": "G%d" % i, "status": "BLOCKED", "detail": "",
                        "evidence": []} for i in range(8)}
        self.state = S["NEW"]
        self.item_id: Optional[str] = None
        self.verdict = "FAIL"
        self.exit_code = 1
        self.honesty: Dict[str, Any] = {
            "model_calls": 0, "rule_assisted": 0, "missing_roles": 0,
            "mock_in_evidence": False, "user_verdict": False,
        }

    def add(self, cid: str, status: str, detail: str = "",
            seq: Optional[int] = None) -> None:
        """status: PASS / WARN / FAIL"""
        item = {"id": cid, "status": status, "detail": detail}
        if seq is not None:
            item["seq"] = seq
        self.checks.append(item)

    def fail(self, cid: str, detail: str, seq: Optional[int] = None) -> None:
        self.add(cid, "FAIL", detail, seq)

    def gate(self, gid: str, status: str, detail: str = "",
             evidence: Optional[List[int]] = None) -> None:
        self.gates[gid]["status"] = status
        self.gates[gid]["detail"] = detail
        if evidence:
            self.gates[gid]["evidence"] = sorted(set(evidence))

    def failed(self) -> bool:
        return any(c["status"] == "FAIL" for c in self.checks)


# ---------------------------------------------------------------- 主流程

def evaluate(item_dir: str) -> Report:
    events, err = load_events(item_dir)
    return evaluate_events(events, err)


def evaluate_events(events: List[Dict[str, Any]],
                    load_error: Optional[str] = None) -> Report:
    """对已载入的事件列表求值（测试/自检用，不碰文件系统）。"""
    rep = Report()
    if load_error:
        rep.fail("INPUT", load_error)
        rep.verdict = "INPUT_ERROR"
        rep.exit_code = 2
        return rep
    if not events:
        rep.fail("INPUT", "事件账本为空")
        rep.verdict = "INPUT_ERROR"
        rep.exit_code = 2
        return rep

    _check_envelope(rep, events)
    _walk(rep, events)

    # ---- 汇总
    if rep.failed():
        rep.verdict = "FAIL"
        rep.exit_code = 1
        return rep
    g = rep.gates
    statuses = [g["G%d" % i]["status"] for i in range(8)]
    if all(s == "PASS" for s in statuses):
        rep.verdict, rep.exit_code = "PASS", 0
        return rep
    # 链路诚实但未到「全绿」：区分隔离降级 / 阻断
    if any(s == "ISOLATED_ONLY" for s in statuses):
        rep.verdict, rep.exit_code = "ISOLATED_ONLY", 3
    else:
        rep.verdict, rep.exit_code = "BLOCKED", 3
    return rep


def _check_envelope(rep: Report, events: List[Dict[str, Any]]) -> None:
    required = ("schema_version", "seq", "event_id", "item_id", "type", "at",
                "actor", "refs", "payload", "hash")
    first_bad = None
    prev_hash = None
    expect_seq = 1
    last_at = ""
    item_ids = set()
    for ev in events:
        missing = [k for k in required if k not in ev]
        if missing and first_bad is None:
            first_bad = "事件缺少字段 %s（seq=%s）" % (missing, ev.get("seq"))
            continue
        if missing:
            continue
        item_ids.add(str(ev.get("item_id")))
        seq = ev.get("seq")
        if seq != expect_seq:
            rep.fail("SEQ_ORDER", "序号断裂：期望 %d 实际 %r" % (expect_seq, seq),
                     seq if isinstance(seq, int) else None)
        expect_seq += 1
        if prev_hash is not None and ev.get("prev_hash") != prev_hash:
            rep.fail("CHAIN_HASH", "prev_hash 不连续（seq=%s）" % seq, seq)
        want = event_hash(ev)
        if ev.get("hash") != want:
            rep.fail("CHAIN_HASH", "事件内容与 hash 不符（seq=%s）——疑似篡改" % seq,
                     seq if isinstance(seq, int) else None)
        prev_hash = ev.get("hash")
        at = str(ev.get("at") or "")
        if last_at and at < last_at:
            rep.fail("TIME_ORDER", "时间回退（seq=%s: %s < %s）" % (seq, at, last_at), seq)
        last_at = at or last_at
    if first_bad:
        rep.fail("ENVELOPE_REQUIRED", first_bad)
    if len(item_ids) > 1:
        rep.fail("ITEM_CONSISTENCY", "同一账本出现多个 item_id: %s" % sorted(item_ids))
    if len(item_ids) == 1:
        rep.item_id = next(iter(item_ids))
    if not any(c["id"] == "ENVELOPE_REQUIRED" for c in rep.checks):
        rep.add("ENVELOPE_REQUIRED", "PASS", "%d 条事件信封完整" % len(events))


def _walk(rep: Report, events: List[Dict[str, Any]]) -> None:
    state = S["NEW"]
    ev_by_seq: Dict[int, Dict[str, Any]] = {}
    # 业务对象
    brief: Optional[Dict[str, Any]] = None
    args: Optional[Dict[str, Any]] = None
    card: Optional[Dict[str, Any]] = None
    selected_card: Optional[str] = None
    grant: Optional[Dict[str, Any]] = None
    workorder: Optional[Dict[str, Any]] = None
    claimed = False
    claim_method: Optional[str] = None
    artifact: Optional[Dict[str, Any]] = None
    test_pass = False
    trial = False
    verdict: Optional[Dict[str, Any]] = None
    prereq_blocked = False
    idem_keys: Dict[str, int] = {}
    g_evidence: Dict[str, List[int]] = {k: [] for k in rep.gates}
    allowed_gate = False

    for ev in events:
        seq = ev.get("seq")
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        refs = ev.get("refs") or {}
        actor = ev.get("actor") or {}
        if isinstance(seq, int):
            ev_by_seq[seq] = ev
        # ---- 幂等键
        idem = ev.get("idempotency_key")
        if idem:
            if idem in idem_keys:
                rep.fail("IDEMPOTENCY",
                         "重复提交：idempotency_key=%s 已在 seq=%s 出现（seq=%s）"
                         % (idem, idem_keys[idem], seq), seq)
            else:
                idem_keys[idem] = seq if isinstance(seq, int) else 0
            # 重复键时仍继续走语义校验，避免掩盖主链问题

        # ---- 状态机
        if etype not in TRANSITIONS:
            rep.fail("EVENT_TYPE", "未知事件类型 %r（seq=%s）" % (etype, seq), seq)
            continue
        froms, to = TRANSITIONS[etype]
        if state not in froms:
            rep.fail("TRANSITION",
                     "非法状态转移：%s → %s（当前状态 %s，seq=%s）"
                     % (state, etype, state, seq), seq)
        # ---- actor 信任
        if etype in USER_ONLY_EVENTS and actor.get("kind") != "user":
            rep.fail("ACTOR_TRUST",
                     "%s 必须由本人执行，实际 actor.kind=%r（seq=%s）"
                     % (etype, actor.get("kind"), seq), seq)

        # ---- 分支处理
        if etype == "item.created":
            rep.gate("G0", "PASS", "事项已建档", [seq])
            g_evidence["G0"].append(seq)
        elif etype == "brief.drafted":
            brief = _brief(payload)
            if not brief["goals"]:
                rep.fail("BRIEF_GOALS", "需求版本缺少目标（seq=%s）" % seq, seq)
        elif etype == "brief.frozen":
            brief = _brief(payload)
            if not brief["goals"]:
                rep.fail("BRIEF_GOALS", "冻结的需求缺少目标（seq=%s）" % seq, seq)
            if not brief["success_criteria"]:
                rep.fail("BRIEF_CRITERIA", "冻结的需求缺少验收标准（seq=%s）" % seq, seq)
            for sc in brief["success_criteria"]:
                if not sc.get("verify_method"):
                    rep.fail("BRIEF_CRITERIA",
                             "验收标准 %r 缺 verify_method（seq=%s）"
                             % (sc.get("id"), seq), seq)
            if actor.get("kind") == "user":
                rep.gate("G1", "PASS", "需求 %s 由本人冻结" % brief["version"], [seq])
                g_evidence["G1"].append(seq)
            rep.add("BRIEF_FROZEN", "PASS", "版本 %s" % brief["version"], seq)
        elif etype == "argument.recorded":
            args = payload
            arg_ver = str(payload.get("brief_version") or "")
            if brief and arg_ver != brief["version"]:
                rep.fail("VERSION_MATCH",
                         "论证针对需求 %r，当前冻结版本 %r（seq=%s）"
                         % (arg_ver, brief["version"], seq), seq)
            roles = payload.get("roles") or []
            if not isinstance(roles, list) or len(roles) < 3:
                rep.fail("ARGUMENT_ROLES",
                         "论证角色不足 3 席（实际 %r，seq=%s）"
                         % (len(roles) if isinstance(roles, list) else roles, seq), seq)
            seen_roles = set()
            has_counter = False
            real = rule = missing = 0
            for role in roles:
                if not isinstance(role, dict):
                    rep.fail("ARGUMENT_ROLES", "角色记录不是对象（seq=%s）" % seq, seq)
                    continue
                rname = str(role.get("role") or "")
                seen_roles.add(rname)
                kind = role.get("source_kind")
                if kind == "model_call":
                    call = role.get("call") or {}
                    need = ("provider", "model", "call_id", "prompt_sha256",
                            "response_sha256")
                    if not all(call.get(k) for k in need) or not call.get("raw_available"):
                        rep.fail("MODEL_CALL",
                                 "角色 %s 声称真模型调用，但调用记录不全（seq=%s）"
                                 % (rname, seq), seq)
                    real += 1
                elif kind == "rule_assisted":
                    if not (role.get("rules_ref") or (role.get("call") or {}).get("label")):
                        rep.fail("RULE_LABEL",
                                 "角色 %s 为规则代行但未标注规则依据（seq=%s）"
                                 % (rname, seq), seq)
                    rule += 1
                elif kind == "missing":
                    if not role.get("failure_reason"):
                        rep.fail("MODEL_MISSING",
                                 "角色 %s 标 missing 但无失败原因（seq=%s）"
                                 % (rname, seq), seq)
                    missing += 1
                elif kind in ("mock", "template", "fixed_script"):
                    rep.fail("MOCK",
                             "角色 %s 使用预置内容冒充结论（seq=%s）" % (rname, seq), seq)
                    rep.honesty["mock_in_evidence"] = True
                else:
                    rep.fail("SOURCE_KIND",
                             "角色 %s source_kind 非法：%r（seq=%s）"
                             % (rname, kind, seq), seq)
                if role.get("counter_arguments"):
                    has_counter = True
            rep.honesty["model_calls"] += real
            rep.honesty["rule_assisted"] += rule
            rep.honesty["missing_roles"] += missing
            lack = REQUIRED_ROLES - seen_roles
            if lack:
                rep.fail("ARGUMENT_ROLES",
                         "缺少角色：%s（seq=%s）" % ("/".join(sorted(lack)), seq), seq)
            if not has_counter:
                rep.fail("ARGUMENT_COUNTER",
                         "论证没有任何反证/反方观点（seq=%s）" % seq, seq)
            if real + rule == 0:
                rep.gate("G2", "BLOCKED", "全部角色无有效结论（模型/规则均缺失）", [seq])
            elif missing and real == 0:
                rep.gate("G2", "ISOLATED_ONLY",
                         "仅规则辅助、无真模型调用；只能称规则结论", [seq])
            else:
                rep.gate("G2", "PASS",
                         "真模型 %d 席 / 规则代行 %d 席 / 缺失 %d 席"
                         % (real, rule, missing), [seq])
            g_evidence["G2"].append(seq)
        elif etype == "card.drafted":
            if brief and str(payload.get("brief_version") or "") != brief["version"]:
                rep.fail("VERSION_MATCH", "推荐卡需求版本与冻结版不一致（seq=%s）" % seq, seq)
        elif etype == "card.frozen":
            card = payload
            if args is None:
                rep.fail("CARD_ARGUMENT",
                         "推荐卡没有对应的论证记录（seq=%s）" % seq, seq)
            if brief and str(payload.get("brief_version") or "") != brief["version"]:
                rep.fail("VERSION_MATCH",
                         "推荐卡 %r 针对需求 %r，冻结版为 %r（seq=%s）"
                         % (payload.get("card_version"), payload.get("brief_version"),
                            brief["version"], seq), seq)
            if not payload.get("recommended"):
                rep.fail("CARD_RECOMMENDED", "推荐卡没有推荐方案（seq=%s）" % seq, seq)
            if not (payload.get("discarded") or []):
                rep.fail("CARD_DISCARDED", "推荐卡未写舍弃方案及理由（seq=%s）" % seq, seq)
            if not (payload.get("acceptance_method") or "").strip():
                rep.fail("CARD_ACCEPTANCE", "推荐卡未写验收办法（seq=%s）" % seq, seq)
            ev_refs = payload.get("evidence_refs") or []
            if not ev_refs:
                rep.fail("CARD_EVIDENCE", "推荐卡没有证据引用（seq=%s）" % seq, seq)
            if not (payload.get("disconfirm_refs") or []):
                rep.fail("CARD_EVIDENCE", "推荐卡没有反证引用（seq=%s）" % seq, seq)
            if args and args.get("roles") and rep.honesty["missing_roles"] \
                    and not (payload.get("unknowns") or []):
                rep.fail("INSUFFICIENT_DISCLOSURE",
                         "存在缺失角色，但推荐卡未披露未知项（seq=%s）" % seq, seq)
            rep.gate("G3", "PASS" if (payload.get("recommended") and ev_refs)
                     else "BLOCKED", "推荐卡 %s" % payload.get("card_version"), [seq])
            g_evidence["G3"].append(seq)
        elif etype == "card.selected":
            selected_card = str(payload.get("card_version") or "")
            if card and selected_card != str(card.get("card_version")):
                rep.fail("VERSION_MATCH",
                         "选择卡 %r 与冻结卡 %r 不一致（seq=%s）"
                         % (selected_card, card.get("card_version"), seq), seq)
            if card and not rep.failed():
                rep.gate("G3", "PASS", "本人选择 %s" % selected_card, [seq])
            g_evidence["G3"].append(seq)
        elif etype == "grant.signed":
            grant = payload
            gv = str(payload.get("card_version") or "")
            if not selected_card:
                rep.fail("STALE_CARD",
                         "本人尚未选择推荐卡就签了授权（seq=%s）" % seq, seq)
            if selected_card and gv != selected_card:
                rep.fail("STALE_CARD",
                         "授权针对卡 %r，本人选的是 %r（seq=%s）"
                         % (gv, selected_card, seq), seq)
            if card and gv != str(card.get("card_version")):
                rep.fail("VERSION_MATCH", "授权卡版本与冻结卡不一致（seq=%s）" % seq, seq)
            actions = payload.get("actions") or []
            bad = [a for a in actions if a not in ALLOWED_ACTIONS]
            if bad:
                rep.fail("GRANT_SCOPE", "越权动作：%s（seq=%s）" % (bad, seq), seq)
            env = payload.get("env")
            if env not in ALLOWED_ENV:
                rep.fail("ENV_ISOLATED",
                         "首版仅允许 isolated，实际 env=%r（seq=%s）" % (env, seq), seq)
            signer = payload.get("signer") or {}
            if signer.get("id") != "laotian" or signer.get("method") not in TRUSTED_SIGNER_METHODS:
                rep.fail("ACTOR_TRUST",
                         "授权签准方式不可信：%r（seq=%s）"
                         % (signer.get("method"), seq), seq)
            if not (signer.get("nonce_id") or signer.get("session_id")):
                rep.fail("ACTOR_TRUST", "授权缺少会话/一次性 nonce 证据（seq=%s）" % seq, seq)
            if not (payload.get("valid_until") or "").strip():
                rep.fail("GRANT_EXPIRY", "授权缺少有效期（seq=%s）" % seq, seq)
            if actions and not bad and env in ALLOWED_ENV:
                rep.gate("G4", "PASS",
                         "本人签准动作 %d 项，环境 isolated，有效期至 %s"
                         % (len(actions), payload.get("valid_until")), [seq])
            g_evidence["G4"].append(seq)
        elif etype == "gate.blocked":
            prereq_blocked = True
            rep.gate("G4", "BLOCKED",
                     "阻断在 %s：%s"
                     % (payload.get("gate"), payload.get("reason_code")), [seq])
            g_evidence["G4"].append(seq)
        elif etype == "workorder.created":
            workorder = payload
            if grant and str(payload.get("grant_id") or "") != str(grant.get("grant_id")):
                rep.fail("REF_INTEGRITY", "工单未引用有效授权（seq=%s）" % seq, seq)
            use_action = str(payload.get("action") or "")
            if grant and use_action and use_action not in (grant.get("actions") or []):
                rep.fail("GRANT_SCOPE",
                         "工单动作 %r 不在授权动作清单内（seq=%s）"
                         % (use_action, seq), seq)
            if grant and str(ev.get("at") or "") >= str(grant.get("valid_until") or ""):
                rep.fail("GRANT_EXPIRED",
                         "授权已于 %s 到期，工单创建于更晚（seq=%s）"
                         % (grant.get("valid_until"), seq), seq)
            if not payload.get("prerequisites_checked"):
                if not prereq_blocked:
                    rep.fail("UNVERIFIED_PREREQ",
                             "未核验权威前置却直接派工（seq=%s）" % seq, seq)
            g_evidence["G5"].append(seq)
        elif etype == "workorder.claimed":
            claimed = True
            claim = payload.get("claim") or {}
            claim_method = claim.get("method")
            if not claim.get("consumer_id"):
                rep.fail("CLAIM_CONSUMER", "认领记录缺少真实消费者（seq=%s）" % seq, seq)
            if claim_method not in ("real", "simulated"):
                rep.fail("CLAIM_CONSUMER", "认领方式非法：%r（seq=%s）"
                         % (claim_method, seq), seq)
            if claim_method == "simulated" and not payload.get("isolation_label"):
                rep.fail("CLAIM_LABEL", "隔离模拟认领未标注（seq=%s）" % seq, seq)
            if claim_method == "real":
                rep.gate("G5", "PASS", "真实消费者 %s 认领" % claim.get("consumer_id"), [seq])
            else:
                rep.gate("G5", "ISOLATED_ONLY",
                         "隔离模拟认领（%s），不得称真实派工成功"
                         % payload.get("isolation_label"), [seq])
            g_evidence["G5"].append(seq)
        elif etype == "artifact.produced":
            artifact = payload
            path = str(payload.get("path") or "").replace("\\", "/")
            if not path.startswith(ISOLATION_ROOT):
                rep.fail("ARTIFACT_CONFINEMENT",
                         "产物路径越出隔离根：%r（seq=%s）" % (path, seq), seq)
            if workorder and str(payload.get("workorder_id") or "") != str(workorder.get("workorder_id")):
                rep.fail("REF_INTEGRITY",
                         "产物引用的工单与已认领工单不一致（seq=%s）" % seq, seq)
            if not str(payload.get("sha256") or "").startswith("sha256:"):
                rep.fail("ARTIFACT_HASH", "产物缺少 sha256 指纹（seq=%s）" % seq, seq)
            if not (payload.get("rollback") or {}).get("recipe"):
                rep.fail("ARTIFACT_ROLLBACK", "产物缺少撤回办法（seq=%s）" % seq, seq)
            if not workorder:
                rep.fail("REF_INTEGRITY", "产物未引用工单（seq=%s）" % seq, seq)
            g_evidence["G6"].append(seq)
        elif etype == "test.recorded":
            if not artifact:
                rep.fail("TEST_EVIDENCE", "测试回执在产物之前（seq=%s）" % seq, seq)
            if str(payload.get("result") or "").lower() != "pass":
                rep.gate("G6", "BLOCKED",
                         "测试未通过：%r" % payload.get("result"), [seq])
            else:
                test_pass = True
            if not (payload.get("output_sha256") or "").startswith("sha256:"):
                rep.fail("TEST_EVIDENCE", "测试回执缺少输出指纹（seq=%s）" % seq, seq)
            g_evidence["G6"].append(seq)
        elif etype == "trial.opened":
            trial = True
            if artifact and str(payload.get("artifact_id") or "") != str(artifact.get("artifact_id")):
                rep.fail("REF_INTEGRITY",
                         "试用的产物与已交付产物不一致（seq=%s）" % seq, seq)
            rep.gate("G7", "BLOCKED", "已打开试用，等待本人反馈", [seq])
            g_evidence["G7"].append(seq)
        elif etype == "verdict.recorded":
            verdict = payload
            decision = str(payload.get("decision") or "")
            if actor.get("kind") != "user":
                rep.fail("VERDICT_NOT_USER",
                         "验收回执必须由本人给出（seq=%s）" % seq, seq)
            if not trial:
                rep.fail("TRIAL_NOT_OPENED", "没有试用记录就先有验收回执（seq=%s）" % seq, seq)
            if not artifact:
                rep.fail("NO_ARTIFACT", "没有产物就收到验收回执（seq=%s）" % seq, seq)
            if artifact and str(payload.get("artifact_id") or "") != str(artifact.get("artifact_id")):
                rep.fail("REF_INTEGRITY",
                         "验收回执引用的产物与已交付产物不一致（seq=%s）" % seq, seq)
            if decision == "accepted":
                if trial and artifact:
                    rep.gate("G7", "PASS", "本人验收 accepted", [seq])
                    rep.honesty["user_verdict"] = True
                else:
                    rep.gate("G7", "BLOCKED", "验收缺少试用或产物证据", [seq])
            elif decision == "changes_requested":
                rep.gate("G7", "BLOCKED",
                         "本人要求返工，应开新需求版本而不是报完成", [seq])
                state = S["REWORK"]
                continue
            else:
                rep.fail("VERDICT_DECISION", "未知验收结论：%r（seq=%s）"
                         % (decision, seq), seq)
            g_evidence["G7"].append(seq)
        elif etype == "workorder.failed":
            rep.gate("G6", "BLOCKED", "执行失败：%s"
                     % ((payload.get("failure") or {}).get("message")), [seq])

        # ---- 推进状态
        if etype != "verdict.recorded" or state != S["REWORK"]:
            state = _advance(etype, to, state, payload) if etype != "verdict.recorded" \
                else (S["ACCEPTED"] if str((payload or {}).get("decision")) == "accepted"
                      else state)
        # 派生：产物+测试通过 → 可试用（不依赖显式事件）
        if artifact and test_pass and state == S["EXECUTING"]:
            state = S["TRIAL_READY"]
            if rep.gates["G6"]["status"] == "BLOCKED":
                rep.gates["G6"]["status"] = "PASS"
            rep.gate("G6", "PASS", "产物 %s + 测试 PASS"
                     % artifact.get("artifact_id"), g_evidence["G6"])
            rep.gate("G7", "BLOCKED", "待本人打开试用")
    rep.state = state

    # ---- 收尾闸门
    if rep.gates["G1"]["status"] != "PASS" and not rep.failed():
        rep.gate("G1", "BLOCKED", "需求尚未由本人冻结")
    if not selected_card:
        rep.gate("G3", "BLOCKED", "尚未由本人选定推荐卡")
    if rep.gates["G5"]["status"] != "PASS" and rep.gates["G5"]["status"] != "ISOLATED_ONLY":
        rep.gate("G5", "BLOCKED", "尚无真实消费者认领——不得显示已派工")
    if not artifact:
        rep.gate("G6", "BLOCKED", "尚无隔离产物")
    if not trial:
        rep.gate("G7", "BLOCKED", "本人尚未试用")
    elif not verdict:
        rep.gate("G7", "BLOCKED", "本人试用后尚未给出验收回执")
    if grant:
        rep.add("GRANT_SCOPE", "PASS",
                "环境 %s / 动作 %d 项 / 到期 %s"
                % (grant.get("env"), len(grant.get("actions") or []),
                   grant.get("valid_until")))
    rep.add("SCHEMA", "PASS", "事件数 %d，终态 %s" % (len(events), state))


def _brief(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": str(payload.get("brief_version") or ""),
        "goals": payload.get("goals") or [],
        "success_criteria": payload.get("success_criteria") or [],
    }


def _advance(etype: str, default_to: str, state: str,
             payload: Dict[str, Any]) -> str:
    if etype == "verdict.recorded":
        return state
    return default_to


def render_text(rep: Report) -> str:
    lines = ["=" * 72,
             "OS 单事项闭环合同校验 · %s" % (rep.item_id or "<未知 item>"),
             "schema: %s   终态: %s   判定: %s (exit %d)"
             % (SCHEMA_VERSION, rep.state, rep.verdict, rep.exit_code),
             "=" * 72, "闸门："]
    for gid in ["G%d" % i for i in range(8)]:
        g = rep.gates[gid]
        lines.append("  %-3s %-12s %s" % (gid, g["status"], g["detail"]))
    fails = [c for c in rep.checks if c["status"] == "FAIL"]
    warns = [c for c in rep.checks if c["status"] == "WARN"]
    lines.append("检查：%d 项，FAIL %d，WARN %d"
                 % (len(rep.checks), len(fails), len(warns)))
    for c in rep.checks:
        if c["status"] != "PASS":
            lines.append("  [%s] %s: %s%s"
                         % (c["status"], c["id"], c["detail"],
                            " (seq=%s)" % c["seq"] if "seq" in c else ""))
    lines.append("诚实性：真模型 %d 席 / 规则代行 %d 席 / 缺失 %d 席 / "
                 "mock 入证 %s / 本人验收 %s"
                 % (rep.honesty["model_calls"], rep.honesty["rule_assisted"],
                    rep.honesty["missing_roles"], rep.honesty["mock_in_evidence"],
                    rep.honesty["user_verdict"]))
    return "\n".join(lines)


def _selftest() -> int:
    """内存自检：5 个正例 + 20 个负例，不写文件。返回 0=全符合预期。"""
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import os_samples as S  # 同目录；仅在自检模式导入

    cases: List[Tuple[str, List[Dict[str, Any]], str, int]] = [
        ("P1_pass_real_claim", S.seal(S.base_events("real")), "PASS", 0),
        ("P2_isolated_only_simulated_claim",
         S.seal(S.base_events("simulated")), "ISOLATED_ONLY", 3),
        ("P3_blocked", S._blocked_events(), "BLOCKED", 3),
        ("P4_rule_only", S.seal(S.base_events("real", rule_only=True)),
         "ISOLATED_ONLY", 3),
    ]
    rework = S.base_events("real")
    S.find(rework, "verdict.recorded")["payload"]["decision"] = "changes_requested"
    S.find(rework, "verdict.recorded")["payload"]["feedback"] = "标题要改"
    cases.append(("P5_rework_not_accepted", S.seal(rework), "BLOCKED", 3))
    for name, fn, _cid in S.NEGATIVES:
        if name == "N10_tamper":
            cases.append((name, fn(S.seal(S.base_events("real"))), "FAIL", 1))
        else:
            cases.append((name, S.seal(fn(S.base_events("real"))), "FAIL", 1))

    bad = 0
    print("%-40s %-14s %-14s %s" % ("样本", "期望", "实际", "结果"))
    print("-" * 78)
    for name, events, want_v, want_x in cases:
        rep = evaluate_events(events)
        ok = rep.verdict == want_v and rep.exit_code == want_x
        if not ok:
            bad += 1
        print("%-40s %-14s %-14s %s"
              % (name, "%s/%d" % (want_v, want_x),
                 "%s/%d" % (rep.verdict, rep.exit_code), "OK" if ok else "MISMATCH"))
        if not ok:
            print("    FAIL 明细：%s" % [c for c in rep.checks if c["status"] == "FAIL"])
            print("    闸门：%s" % {k: v["status"] for k, v in rep.gates.items()})
    print("-" * 78)
    print("自检 %d 例：%d 符合预期，%d 不符" % (len(cases), len(cases) - bad, bad))
    return 0 if bad == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="OS 单事项闭环合同校验器（只读）")
    ap.add_argument("item_dir", nargs="?", help="事项目录（含 events.jsonl）或 events.jsonl 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="内存自检（4 正例 + 20 负例，不写文件）")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.item_dir:
        ap.error("需要 item_dir 或 --selftest")
    rep = evaluate(args.item_dir)
    if args.json:
        print(json.dumps({
            "ok": rep.verdict == "PASS",
            "item_id": rep.item_id,
            "schema_version": SCHEMA_VERSION,
            "state": rep.state,
            "verdict": rep.verdict,
            "exit_code": rep.exit_code,
            "gates": list(rep.gates.values()),
            "checks": rep.checks,
            "honesty": rep.honesty,
        }, ensure_ascii=False, indent=1))
    else:
        print(render_text(rep))
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
