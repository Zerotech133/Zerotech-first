# -*- coding: utf-8 -*-
"""Mission 阶段3：结果采集 → 经验候选 → 策略建议。

安全边界：
- 只读 work/missions/*/{plan.json,state.json,dry_run.json}；
- 只写 work/experience/{candidates,accepted}；
- 候选默认 status=candidate、requires_signature=true；
- 未提供 signed_by 不允许入 accepted 经验库；不自动改策略/提示词/代码。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MISSIONS = REPO / "work" / "missions"
EXPERIENCE = REPO / "work" / "experience"
CANDIDATES = EXPERIENCE / "candidates"
ACCEPTED = EXPERIENCE / "accepted.jsonl"


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _dt(v):
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


def _cycle_sec(t: dict):
    a, b = _dt(t.get("started_at")), _dt(t.get("done_at"))
    if a and b:
        return max(0.0, (b - a).total_seconds())
    return None


def collect(mission_id: str = "") -> list:
    out = []
    for p in sorted(MISSIONS.glob("*/state.json")):
        try:
            st = _load(p)
        except Exception:
            continue
        if not isinstance(st, dict):
            continue
        mid = str(st.get("mission_id") or p.parent.name)
        if mission_id and mid != mission_id:
            continue
        plan = _load(p.parent / "plan.json") or {}
        dry = _load(p.parent / "dry_run.json") or {}
        tasks = st.get("tasks") or {}
        done = [t for t in tasks.values() if t.get("status") == "done"]
        failed = [t for t in tasks.values() if t.get("status") == "gate_failed"]
        quality_failed = [t for t in tasks.values() if t.get("status") == "quality_failed"]
        cycles = [c for c in (_cycle_sec(t) for t in done) if c is not None]
        rec = {
            "schema": "mission_outcome/v1",
            "mission_id": mid,
            "name": plan.get("name") or "",
            "source": plan.get("source") or "unknown",
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "parallel_limit": st.get("parallel_limit"),
            "tasks_total": len(tasks),
            "tasks_done": len(done),
            "tasks_gate_failed": len(failed),
            "tasks_quality_failed": len(quality_failed),
            "tasks_blocked": sum(1 for t in tasks.values() if t.get("status") == "blocked"),
            "tasks_doing": sum(1 for t in tasks.values() if t.get("status") in ("queued", "doing", "review")),
            "gate_pass_rate": round(len(done) / (len(done) + len(failed) + len(quality_failed)), 4)
                             if (done or failed or quality_failed) else None,
            "avg_cycle_sec": round(sum(cycles) / len(cycles), 1) if cycles else None,
            "conflicts": len(dry.get("conflicts") or []),
            "task_records": [
                {"task_id": tid, "owner": t.get("owner"), "status": t.get("status"),
                 "attempts": t.get("attempts", 0), "model": t.get("model"),
                 "ticket_id": t.get("ticket_id"),
                 "gate_tail": str(t.get("gate_detail") or "")[-300:]}
                for tid, t in tasks.items()
            ],
        }
        out.append(rec)
    return out


def summarize(records: list) -> dict:
    tasks = sum(r["tasks_total"] for r in records)
    done = sum(r["tasks_done"] for r in records)
    failed = sum(r["tasks_gate_failed"] for r in records)
    quality_failed = sum(r.get("tasks_quality_failed", 0) for r in records)
    cycles = [r["avg_cycle_sec"] for r in records if r.get("avg_cycle_sec") is not None]
    models, owners, sources = {}, {}, {}
    for r in records:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
        for t in r.get("task_records") or []:
            models[t.get("model") or "auto"] = models.get(t.get("model") or "auto", 0) + 1
            owners[t.get("owner") or "?"] = owners.get(t.get("owner") or "?", 0) + 1
    return {
        "missions": len(records),
        "tasks": tasks,
        "done": done,
        "gate_failed": failed,
        "quality_failed": quality_failed,
        "gate_pass_rate": round(done / (done + failed + quality_failed), 4)
                          if (done + failed + quality_failed) else None,
        "avg_cycle_sec": round(sum(cycles) / len(cycles), 1) if cycles else None,
        "conflicts": sum(r["conflicts"] for r in records),
        "sources": sources,
        "models": models,
        "owners": owners,
    }


def suggestions(metrics: dict) -> list:
    out = []
    sample = metrics.get("missions", 0)
    rate = metrics.get("gate_pass_rate")
    if sample == 0:
        return [{"id": "S0", "priority": "info", "evidence": "暂无 mission 样本",
                 "action": "先用阶段1/2跑 1-2 个 mission 再评估", "auto_apply": False}]
    if rate is not None and rate < 0.8:
        out.append({"id": "S1", "priority": "high",
                    "evidence": "评审门通过率 %.1f%%（done=%s gate_failed=%s）"
                                % (rate * 100, metrics.get("done"), metrics.get("gate_failed")),
                    "action": "缩小任务粒度/把验收条件前移到 plan；失败任务先补证据再重派",
                    "auto_apply": False})
    if metrics.get("conflicts", 0) > 0:
        out.append({"id": "S2", "priority": "high",
                    "evidence": "写作用域冲突 %s 处" % metrics["conflicts"],
                    "action": "planner 拆任务时按写作用域拆分，冲突任务改为串行/同 owner",
                    "auto_apply": False})
    sources = metrics.get("sources") or {}
    if sources.get("fallback", 0) > sources.get("llm", 0):
        out.append({"id": "S3", "priority": "medium",
                    "evidence": "plan 来源 fallback=%s / llm=%s"
                                % (sources.get("fallback", 0), sources.get("llm", 0)),
                    "action": "检查强模型/网关可用性；planner 输出加 schema 校验与重试",
                    "auto_apply": False})
    if metrics.get("quality_failed", 0) > 0:
        out.append({"id": "S5", "priority": "high",
                    "evidence": "质量失败 %s 条（Worker 产出未过 grounding/quality 门）"
                                % metrics.get("quality_failed"),
                    "action": "planner 规则要求 checks；Worker 只允许带 allowed_commands/checks 的任务",
                    "rule": {"require_checks": True}, "auto_apply": False})
    if not out:
        out.append({"id": "S4", "priority": "info",
                    "evidence": "样本通过率正常",
                    "action": "继续积累样本；下轮再比较模型/owner 策略", "auto_apply": False})
    for s in out:
        s["requires_human"] = True
    return out


def evolve() -> dict:
    records = collect()
    metrics = summarize(records)
    sugg = suggestions(metrics)
    payload = {
        "schema": "mission_experience_candidate/v1",
        "candidate_id": "cand-" + hashlib.sha1(
            (time.time_ns().__str__() + str(len(records))).encode(), usedforsecurity=False).hexdigest()[:10],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "candidate",
        "requires_signature": True,
        "records": records,
        "metrics": metrics,
        "suggestions": sugg,
        "note": "候选经验；未经 signed_by 签字不得进入 accepted 经验库，不自动应用策略",
    }
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    path = CANDIDATES / (payload["candidate_id"] + ".json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (EXPERIENCE / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                            encoding="utf-8")
    return payload


def list_candidates() -> list:
    out = []
    for p in sorted(CANDIDATES.glob("*.json")):
        d = _load(p)
        if isinstance(d, dict):
            out.append({"candidate_id": d.get("candidate_id"), "generated_at": d.get("generated_at"),
                        "status": d.get("status"), "metrics": d.get("metrics"),
                        "suggestions": len(d.get("suggestions") or [])})
    return out


def promote(candidate_id: str, signed_by: str, note: str = "") -> dict:
    signed_by = str(signed_by or "").strip()
    if not signed_by:
        return {"ok": False, "error": "signed_by_required"}
    p = CANDIDATES / (str(candidate_id) + ".json")
    if not p.exists():
        return {"ok": False, "error": "candidate_not_found"}
    d = _load(p) or {}
    d["status"] = "accepted"
    d["signed_by"] = signed_by
    d["signed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    d["sign_note"] = note
    ACCEPTED.parent.mkdir(parents=True, exist_ok=True)
    with ACCEPTED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"ok": True, "candidate_id": candidate_id, "accepted_path": str(ACCEPTED)}


def load_latest() -> dict:
    p = EXPERIENCE / "latest.json"
    return _load(p) or {}
