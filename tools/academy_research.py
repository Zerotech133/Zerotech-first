# -*- coding: utf-8 -*-
"""研究院课题台账与状态流转（K-ORG-126）。

统一研究语言：问题 -> 对象 -> 假设 -> 证据 -> 验证 -> 结论 -> 行动 -> 风险。
本模块只负责登记、校验和流转，不替研究院做结论。
"""
import copy
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "agent_platform" / "data" / "academy"
TOPICS = DATA / "research_topics.json"

STAGES = ["intake", "hypothesis", "evidence", "validation", "review", "joint_review", "decision", "closed"]
STAGE_NAMES = {
    "intake": "需求进入",
    "hypothesis": "假设登记",
    "evidence": "证据登记",
    "validation": "验证执行",
    "review": "研究院评审",
    "joint_review": "跨部门联评",
    "decision": "田丰拍板",
    "closed": "结果分流",
}
DECISIONS = ["补证", "Go", "No-Go", "试点", "产品化", "入库", "终止"]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_topics(create=False):
    if not TOPICS.exists():
        if not create:
            return []
        DATA.mkdir(parents=True, exist_ok=True)
        TOPICS.write_text("[]", encoding="utf-8")
    try:
        rows = json.loads(TOPICS.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_topics(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = TOPICS.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TOPICS)


def validate_topic(topic):
    required = ["question", "object", "hypothesis", "evidence_scope", "method", "action", "risks"]
    missing = [key for key in required if not str(topic.get(key, "")).strip()]
    if not topic.get("owner"):
        missing.append("owner")
    if not topic.get("direction"):
        missing.append("direction")
    return {"ok": not missing, "missing": missing}


def create_topic(payload):
    topic = dict(payload or {})
    topic_id = topic.get("topic_id") or "AC-%s-%s" % (time.strftime("%Y%m%d"), uuid.uuid4().hex[:6].upper())
    topic.update({
        "topic_id": topic_id,
        "status": topic.get("status", "intake"),
        "decision": topic.get("decision", ""),
        "created": topic.get("created", _now()),
        "updated": _now(),
        "history": topic.get("history", [{"at": _now(), "stage": "intake", "by": topic.get("owner", "ACAD1"), "note": "课题登记"}]),
    })
    check = validate_topic(topic)
    if not check["ok"]:
        return {"ok": False, "err": "课题字段不完整", "missing": check["missing"], "topic": topic}
    rows = load_topics(create=True)
    if any(row.get("topic_id") == topic_id for row in rows):
        return {"ok": False, "err": "课题编号已存在", "topic_id": topic_id}
    rows.append(topic)
    save_topics(rows)
    return {"ok": True, "topic": topic}


def advance_topic(topic_id, stage, by="ACAD1", note="", decision=""):
    if stage not in STAGES:
        return {"ok": False, "err": "未知阶段", "allowed": STAGES}
    rows = load_topics()
    for topic in rows:
        if topic.get("topic_id") == topic_id:
            old = topic.get("status", "intake")
            old_index = STAGES.index(old)
            new_index = STAGES.index(stage)
            if new_index < old_index:
                return {"ok": False, "err": "不允许回退阶段", "current": old}
            if new_index > old_index + 1:
                return {"ok": False, "err": "必须按业务流程逐阶段推进", "current": old, "next": STAGES[old_index + 1]}
            if stage == "closed" and decision not in DECISIONS:
                return {"ok": False, "err": "关闭课题必须选择合法决策", "allowed": DECISIONS}
            topic["status"] = stage
            if decision:
                topic["decision"] = decision
            topic["updated"] = _now()
            topic.setdefault("history", []).append({"at": _now(), "stage": stage, "by": by, "note": note, "decision": decision})
            save_topics(rows)
            return {"ok": True, "topic": topic}
    return {"ok": False, "err": "课题不存在", "topic_id": topic_id}


def worker_stage_transition(topic, worker, meeting_ref=None, approved=False, by="ACAD1", now=None, decision=""):
    """根据 Worker 验收结果生成或执行单步课题阶段推进。

    默认只返回提案，不修改输入；只有 ``approved=True`` 才返回可落盘的新课题。
    这是人工审批门：Worker 不能通过本入口自动推进研究院课题。
    """
    source = copy.deepcopy(topic or {})
    worker = worker or {}
    result = {"ok": False, "advanced": False, "approved": bool(approved), "topic": source}
    status = source.get("status", "intake")
    if status not in STAGES:
        result["err"] = "课题当前阶段无效"
        return result
    if status == "closed":
        result["err"] = "课题已关闭"
        return result
    if status == "decision" and approved and decision not in DECISIONS:
        result["err"] = "决策阶段推进到 closed 必须选择合法决策"
        result["allowed"] = DECISIONS
        return result
    acceptance = (worker or {}).get("acceptance_result") or {}
    if acceptance.get("passed") is not True:
        result["err"] = "Worker 未通过验收，不推进课题阶段"
        return result
    if str(worker.get("execution_status", "")).lower() != "done":
        result["err"] = "Worker 未完成或处于 review 状态，不推进课题阶段"
        return result
    if worker.get("topic_id") != source.get("topic_id"):
        result["err"] = "Worker 与课题不匹配"
        return result
    if worker.get("stage") != status:
        result["err"] = "Worker 不是课题当前阶段工单"
        return result
    next_stage = STAGES[STAGES.index(status) + 1]
    refs = {key: worker.get(key) for key in ("meeting_ref", "trace_id", "output_ref") if worker.get(key)}
    if meeting_ref:
        refs["meeting_ref"] = meeting_ref
    if not {"meeting_ref", "trace_id", "output_ref"}.issubset(refs):
        result["err"] = "缺少 meeting_ref、trace_id 或 output_ref 引用"
        return result
    result.update({"ok": True, "advanced": bool(approved), "next_stage": next_stage, "references": refs})
    if not approved:
        result["err"] = "等待人工审批"
        return result
    source["status"] = next_stage
    if decision:
        source["decision"] = decision
    source["updated"] = now or _now()
    source.setdefault("history", []).append({
        "at": now or _now(), "stage": next_stage, "by": by,
        "note": "Worker 验收通过，人工批准阶段推进",
        "worker_refs": refs,
    })
    result["topic"] = source
    return result


def advance_topic_from_worker(topic_id, worker, meeting_ref=None, approved=False, by="ACAD1", decision=""):
    """显式落盘入口；默认不写 research_topics.json，需人工批准才写入。"""
    rows = load_topics()
    topic = next((row for row in rows if row.get("topic_id") == topic_id), None)
    if not topic:
        return {"ok": False, "err": "课题不存在", "topic_id": topic_id}
    result = worker_stage_transition(topic, worker, meeting_ref=meeting_ref, approved=approved, by=by, decision=decision)
    if result.get("ok") and result.get("advanced"):
        for index, row in enumerate(rows):
            if row.get("topic_id") == topic_id:
                rows[index] = result["topic"]
                break
        save_topics(rows)
    return result


def summary():
    rows = load_topics()
    return {"n": len(rows), "by_stage": {stage: sum(1 for x in rows if x.get("status") == stage) for stage in STAGES}, "topics": rows}


if __name__ == "__main__":
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
