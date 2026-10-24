# -*- coding: utf-8 -*-
"""OS 目标—事项—成果合同 v1.1（G0 内核判据，纯函数、只读、可单测）

范围（G0）：
- 事件信封 + 哈希链校验；
- 事业目标 Goal（本人创建、假设带状态、状态迁移留痕）；
- 事项 Item（必须挂 Goal、声明四类成果之一、why_now/attempt_no 必填）；
- 成果 Outcome（类型与事项一致、content_ref + sha256、citations 显式声明）；
- 跨面引用 Citation 三值规则：推断不得当事实、同源不得升级、BP/内容/研究不得空引用；
- 汇总判定：FAIL（违规）/ BLOCKED（缺前置）/ PASS（骨架可用）。

不在 G0：授权（grant）、工单/认领、执行器、验收回执、两轴 B 阶段总判
（这些在 G1+ 按判据 v3.1 接入；G0 的 PASS 只表示"账本与判据骨架可用"，不表示 B 阶段通过）。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "os-item/1.1"
GOAL_ID_RE = re.compile(r"^GOL-\d{8}-\d{4}$")
ITEM_ID_RE = re.compile(r"^ITM-\d{8}-\d{4}$")
OUTCOME_KINDS = ("software", "business_plan", "content", "research")
SURFACES = ("software", "business_plan", "content", "research", "external")
SOURCE_KINDS = ("data", "document", "commit", "conversation", "inference")
CONFIDENCES = ("fact", "hypothesis")
ASSUMPTION_STATES = ("hypothesis", "validated", "falsified")
EVENT_TYPES = ("goal.created", "goal.updated", "item.created",
               "outcome.produced", "item.cancelled",
               "brief.frozen", "brief.revised", "card.frozen",
               "card.selected", "grant.signed", "workorder.created",
               "workorder.claimed", "artifact.produced", "test.recorded",
               "trial.opened", "verdict.recorded")
FLOW_TYPES = ("brief.frozen", "brief.revised", "card.frozen",
              "card.selected", "grant.signed", "workorder.created",
              "workorder.claimed", "artifact.produced", "test.recorded",
              "trial.opened", "verdict.recorded")
CITATION_REQUIRED = ("business_plan", "content", "research")
REQUIRED_GOAL_FIELDS = ("goal_id", "statement", "target_user", "assumptions",
                        "success_signals")


class ContractError(ValueError):
    """合同违规（API 映射 400/422）。"""


class DuplicateKey(ContractError):
    """幂等键重复（API 映射 409）。"""


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def event_hash(ev: Dict[str, Any]) -> str:
    body = {k: v for k, v in ev.items() if k != "hash"}
    return sha256_text(canonical(body))


def validate_envelope(ev: Dict[str, Any], prev_hash: Optional[str],
                      expect_seq: int) -> List[str]:
    """信封 + 链 + 序号校验；返回错误列表（空=通过）。"""
    errors: List[str] = []
    required = ("schema_version", "seq", "event_id", "type", "at", "actor",
                "refs", "payload", "hash")
    for key in required:
        if key not in ev:
            errors.append("缺少字段 %s" % key)
    if errors:
        return errors
    if ev.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version 必须为 %s" % SCHEMA_VERSION)
    if ev.get("seq") != expect_seq:
        errors.append("序号不连续：期望 %d 实际 %r" % (expect_seq, ev.get("seq")))
    if ev.get("type") not in EVENT_TYPES:
        errors.append("未知事件类型 %r" % ev.get("type"))
    if prev_hash is not None and ev.get("prev_hash") != prev_hash:
        errors.append("prev_hash 不连续（seq=%s）" % ev.get("seq"))
    if ev.get("hash") != event_hash(ev):
        errors.append("事件 hash 与内容不符（seq=%s，疑似篡改）" % ev.get("seq"))
    actor = ev.get("actor") or {}
    if actor.get("kind") not in ("user", "agent", "system", "consumer"):
        errors.append("actor.kind 非法：%r" % actor.get("kind"))
    if not str(actor.get("id") or "").strip():
        errors.append("actor.id 为空")
    if not isinstance(ev.get("refs"), dict) or not isinstance(ev.get("payload"), dict):
        errors.append("refs/payload 必须是对象")
    return errors


# ------------------------------------------------------------------ 语义

def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractError(msg)


def check_goal(payload: Dict[str, Any], actor: Dict[str, Any]) -> None:
    _need(actor.get("kind") == "user", "事业目标必须由本人创建（actor.kind=user）")
    missing = [k for k in REQUIRED_GOAL_FIELDS if not payload.get(k)]
    _need(not missing, "目标缺少字段：%s" % missing)
    _need(bool(GOAL_ID_RE.match(str(payload.get("goal_id")))),
          "goal_id 格式应为 GOL-YYYYMMDD-NNNN")
    _need(bool(str(payload.get("statement") or "").strip()), "目标陈述为空")
    _need(bool(str(payload.get("target_user") or "").strip()), "目标服务对象为空")
    assumptions = payload.get("assumptions") or []
    _need(isinstance(assumptions, list) and len(assumptions) >= 1,
          "目标至少需要 1 条假设")
    for a in assumptions:
        _need(isinstance(a, dict), "假设必须是对象")
        _need(bool(str(a.get("id") or "").strip()), "假设缺少 id")
        _need(bool(str(a.get("text") or "").strip()), "假设缺少 text")
        _need(a.get("status") in ASSUMPTION_STATES,
              "假设状态必须是 hypothesis/validated/falsified：%r"
              % (a.get("status"),))
    signals = payload.get("success_signals") or []
    _need(isinstance(signals, list) and len(signals) >= 1,
          "目标至少需要 1 条成功信号")


def check_goal_update(payload: Dict[str, Any], goal: Dict[str, Any]) -> None:
    _need(bool(str(payload.get("assumption_id") or "").strip()), "假设迁移缺少 assumption_id")
    _need(payload.get("status") in ASSUMPTION_STATES, "假设迁移状态非法")
    ids = {str(a.get("id")) for a in (goal.get("assumptions") or [])}
    _need(str(payload.get("assumption_id")) in ids,
          "目标中不存在该假设：%r" % payload.get("assumption_id"))


def check_item(payload: Dict[str, Any], goal: Optional[Dict[str, Any]]) -> None:
    _need(bool(ITEM_ID_RE.match(str(payload.get("item_id") or ""))),
          "item_id 格式应为 ITM-YYYYMMDD-NNNN")
    _need(bool(str(payload.get("title") or "").strip()), "事项标题为空")
    _need(bool(str(payload.get("why_now") or "").strip()), "事项缺少 why_now")
    _need(payload.get("outcome_kind") in OUTCOME_KINDS,
          "outcome_kind 必须是 %s" % list(OUTCOME_KINDS))
    attempt = payload.get("attempt_no")
    _need(isinstance(attempt, int) and attempt >= 1, "attempt_no 必须是 ≥1 的整数")
    _need(goal is not None, "事项必须挂在已存在的目标下（不得无目标开事项）")


def check_citations(citations: Any, kind: str,
                    first_conf: Dict[str, str]) -> None:
    """引用三值规则；first_conf 记录 source_ref 首次置信度（跨成果不得升级）。"""
    _need(citations is not None, "成果必须显式声明 citations（可为空数组）")
    _need(isinstance(citations, list), "citations 必须是数组")
    if kind in CITATION_REQUIRED:
        _need(len(citations) >= 1, "%s 类成果必须至少 1 条外部引用" % kind)
    for c in citations:
        _need(isinstance(c, dict), "引用必须是对象：%r" % (c,))
        for key in ("source_surface", "source_ref", "source_kind",
                    "claim", "confidence"):
            _need(bool(str(c.get(key) or "").strip()), "引用缺少字段 %s：%r" % (key, c))
        _need(c["source_surface"] in SURFACES,
              "未知来源面：%r" % c["source_surface"])
        _need(c["source_kind"] in SOURCE_KINDS,
              "未知来源类型：%r" % c["source_kind"])
        _need(c["confidence"] in CONFIDENCES,
              "未知置信度：%r" % c["confidence"])
        _need(not (c["source_kind"] == "inference" and c["confidence"] == "fact"),
              "推断不得直接作为事实引用：%r" % c["source_ref"])
        prev = first_conf.get(c["source_ref"])
        _need(not (prev == "hypothesis" and c["confidence"] == "fact"),
              "同一来源 %r 由 hypothesis 被转述为 fact（不得升级）" % c["source_ref"])
        first_conf.setdefault(c["source_ref"], c["confidence"])


def check_outcome(payload: Dict[str, Any], item: Optional[Dict[str, Any]],
                  first_conf: Dict[str, str]) -> None:
    _need(item is not None, "成果必须属于已存在的事项")
    _need(payload.get("outcome_kind") == item.get("outcome_kind"),
          "成果类型 %r 与事项声明 %r 不一致"
          % (payload.get("outcome_kind"), item.get("outcome_kind")))
    _need(bool(str(payload.get("version") or "").strip()), "成果缺少 version")
    _need(bool(str(payload.get("content_ref") or "").strip()), "成果缺少 content_ref")
    _need(str(payload.get("sha256") or "").startswith("sha256:"),
          "成果缺少 sha256 指纹（sha256:…）")
    check_citations(payload.get("citations"), item.get("outcome_kind"), first_conf)


# ------------------------------------------------------------------ 求值

def evaluate(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对整本账求值：返回 checks / gates / verdict / exit_code。"""
    checks: List[Dict[str, Any]] = []
    goals: Dict[str, Dict[str, Any]] = {}
    items: Dict[str, Dict[str, Any]] = {}
    outcomes: List[Dict[str, Any]] = []
    first_conf: Dict[str, str] = {}
    events_by_seq: Dict[int, Dict[str, Any]] = {}

    def fail(cid: str, detail: str, seq: Optional[int] = None) -> None:
        checks.append({"id": cid, "status": "FAIL", "detail": detail,
                       **({"seq": seq} if seq is not None else {})})

    def ok(cid: str, detail: str) -> None:
        checks.append({"id": cid, "status": "PASS", "detail": detail})

    prev, expect = None, 1
    for ev in events:
        seq = ev.get("seq")
        errs = validate_envelope(ev, prev, expect)
        prev = ev.get("hash")
        expect += 1
        if errs:
            fail("ENVELOPE", "；".join(errs), seq)
            continue
        events_by_seq[seq] = ev
        payload, refs, actor = ev["payload"], ev["refs"], ev["actor"]
        etype = ev["type"]
        try:
            if etype == "goal.created":
                check_goal(payload, actor)
                _need(payload["goal_id"] not in goals, "goal_id 重复：%r" % payload["goal_id"])
                goals[payload["goal_id"]] = dict(payload)
            elif etype == "goal.updated":
                gid = payload.get("goal_id")
                _need(gid in goals, "目标不存在：%r" % gid)
                check_goal_update(payload, goals[gid])
                for a in goals[gid]["assumptions"]:
                    if str(a.get("id")) == str(payload["assumption_id"]):
                        a["status"] = payload["status"]
            elif etype == "item.created":
                gid = refs.get("goal_id")
                check_item(payload, goals.get(gid))
                _need(payload["item_id"] not in items,
                      "item_id 重复：%r" % payload["item_id"])
                items[payload["item_id"]] = {"payload": dict(payload),
                                             "goal_id": gid, "seq": seq}
            elif etype == "outcome.produced":
                iid = refs.get("item_id")
                item = items.get(iid)
                check_outcome(payload, item["payload"] if item else None, first_conf)
                outcomes.append({"item_id": iid, "seq": seq, "payload": dict(payload)})
            elif etype == "item.cancelled":
                iid = refs.get("item_id")
                _need(iid in items, "事项不存在：%r" % iid)
                items[iid]["cancelled"] = True
            elif etype in FLOW_TYPES:
                pass  # G1 流程语义由 os_software_flow 判定；G0 判据不越权
        except ContractError as exc:
            fail("SEMANTIC", "%s: %s" % (etype, exc), seq)

    if not checks or not any(c["status"] == "FAIL" for c in checks):
        ok("ENVELOPE", "信封/链/语义 %d 条事件全部通过" % len(events))
    gates = {
        "CHAIN": "PASS" if any(c["id"] == "ENVELOPE" and c["status"] == "PASS"
                               for c in checks) else "FAIL",
        "GOAL": "PASS" if goals else "BLOCKED",
        "ITEM": "PASS" if items else "BLOCKED",
        "OUTCOME": "PASS" if outcomes else "BLOCKED",
        "CITATION": "PASS" if not any(c["status"] == "FAIL" for c in checks) else "FAIL",
    }
    if any(c["status"] == "FAIL" for c in checks):
        verdict, code = "FAIL", 1
    elif not goals or not items or not outcomes:
        verdict, code = "BLOCKED", 3
    else:
        verdict, code = "PASS", 0
    return {"checks": checks, "gates": gates, "verdict": verdict,
            "exit_code": code, "goals": len(goals), "items": len(items),
            "outcomes": len(outcomes)}


def selftest() -> int:
    """内存自检：正例 1 条 + 负例 6 条（不写盘）。"""
    def seal(evs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out, prev = [], None
        for i, ev in enumerate(evs, 1):
            rec = dict(ev)
            rec["schema_version"] = SCHEMA_VERSION
            rec["seq"] = i
            rec["event_id"] = "EVT-G0-%03d" % i
            rec["at"] = "2026-10-24T09:20:00+08:00"
            rec["prev_hash"] = prev
            rec["hash"] = event_hash(rec)
            prev = rec["hash"]
            out.append(rec)
        return out

    goal = {"goal_id": "GOL-20261024-0001", "statement": "把零界做成持续创业者的个人事业OS",
            "target_user": "连续创业者", "assumptions": [
                {"id": "A1", "text": "用户愿为长期团队付费", "status": "hypothesis"}],
            "success_signals": ["四类成果各验收一次"]}
    item = {"item_id": "ITM-20261024-0001", "title": "做一页可试用原型",
            "why_now": "老田今天要试用", "outcome_kind": "software", "attempt_no": 1}
    cite = {"source_surface": "research", "source_ref": "RES-001",
            "source_kind": "data", "claim": "样本内胜率 55%", "confidence": "fact"}
    cases = []

    def build(kind="software", citations=None, goal_user=True):
        actor = {"kind": "user" if goal_user else "agent", "id": "laotian"}
        return seal([
            {"type": "goal.created", "actor": actor, "refs": {}, "payload": goal},
            {"type": "item.created", "actor": {"kind": "user", "id": "laotian"},
             "refs": {"goal_id": goal["goal_id"]}, "payload": item},
            {"type": "outcome.produced", "actor": {"kind": "user", "id": "laotian"},
             "refs": {"item_id": item["item_id"]},
             "payload": {"outcome_kind": kind, "version": "v1",
                         "content_ref": "portal/data/os_goals/ITM-1/a1.html",
                         "sha256": "sha256:" + "a" * 64,
                         "citations": citations if citations is not None else []}},
        ])

    cases.append(("valid_software", build(), "PASS"))
    cases.append(("goal_by_agent", build(goal_user=False), "FAIL"))
    cases.append(("kind_mismatch", build(kind="content", citations=[cite]), "FAIL"))
    cases.append(("bp_no_citation", build(kind="business_plan", citations=[]), "FAIL"))
    bad_infer = {"source_surface": "content", "source_ref": "C1",
                 "source_kind": "inference", "claim": "用户会付费", "confidence": "fact"}
    cases.append(("inference_as_fact", build(kind="business_plan", citations=[bad_infer]), "FAIL"))
    upgrade = [
        {"source_surface": "content", "source_ref": "C2", "source_kind": "conversation",
         "claim": "用户提到成本", "confidence": "hypothesis"},
        {"source_surface": "business_plan", "source_ref": "C2",
         "source_kind": "conversation", "claim": "用户确认付费", "confidence": "fact"},
    ]
    cases.append(("citation_upgrade", build(kind="business_plan", citations=upgrade), "FAIL"))
    bad = 0
    for name, evs, want in cases:
        got = evaluate(evs)["verdict"]
        if got != want:
            bad += 1
            print("MISMATCH", name, want, got)
    print("contract selftest %d 例，%d 不符" % (len(cases), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
