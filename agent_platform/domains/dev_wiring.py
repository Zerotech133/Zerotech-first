# -*- coding: utf-8 -*-
"""M1 软件开发域接线（域 #5）· TaskStore done 验收门。

真源：TaskStore（engine/tasks.py）——data/org/tasks/tasks_<YYYYMM>.jsonl
append-only 部门任务账本，已有 RBAC / 状态机 / closure_note 强制。
本模块**不复制引擎逻辑**（Task≠WorkItem，M0 判例），只做增量受控：

- 自由转移（open/assigned/doing/blocked/cancelled/rejected）
  → 直通 TaskStore.move（RBAC + 状态机已覆盖，不重复造）；
- **done（验收关闭）→ 必须 Approval**：done 是交付承诺（用户级），
  幂等键 dev:<task_id>:done；凭据先落，再走 store.move；
  崩溃窗口（凭据落、move 未执行）→ catch-up 补执行；
- open → 直通 store.open（开单低风险，RBAC 已管）。

审计局限（诚实声明）：TaskStore.append-only 快照行是完整审计流，
本模块账本只增 done 凭据维度；绕门 done（直接调 store.move）可由
audit_report 检出——任务为 done 而无凭据 → violation。

CLI：list / report / move / done
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval, Artifact, fact_hash_of  # noqa: E402
from engine.approvals import ApprovalStore        # noqa: E402
from engine.artifacts import ArtifactStore        # noqa: E402
from engine.tasks import TaskStore, TaskError  # noqa: E402,F401  # TaskError 为对外 re-export
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402

TASKS_BASE = _AGENT_PLATFORM / "data" / "org" / "tasks"

WIRING_TS = "2026-10-17T14:05:00"   # 接线生效时刻：此前 done 为存量收编


def _db_path() -> Path:
    return _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"


def _artifacts_db() -> Path:
    return _AGENT_PLATFORM / "data" / "artifacts" / "artifacts.db"


class DevWiringError(Exception):
    """受控操作非法。"""


def _store(tasks_base: Path = None) -> TaskStore:
    return TaskStore(tasks_base or TASKS_BASE)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def open_task(to_dept: str, title: str, body: str, severity: str = "P2",
              opened_by: str = "A0") -> dict:
    """开单直通（RBAC 在引擎侧）。"""
    return _store().open(to_dept, title, body, severity=severity,
                         opened_by=opened_by)


def move_task(task_id: str, new_status: str, by: str,
              note: str = None) -> dict:
    """自由转移直通；done 是受门状态，强制走 close_done。"""
    if new_status == "done":
        raise DevWiringError(
            "done 是验收关闭（交付承诺），须走 close_done（Approval 凭据）")
    return _store().move(task_id, new_status, by, note=note)


def register_closure_artifact(task_id: str, closure_note: str, by: str,
                              artifact_store: ArtifactStore = None
                              ) -> Tuple[Artifact, bool]:
    """M3-P2：done 验收关闭时登记 closure 产物（幂等，登记不设门）。

    closure_note 是 TaskStore 账本内嵌产物（无独立文件）——Artifact
    path 留空，source_system/source_ref 定位（dev.tasks + task_id），
    content_hash=sha256(closure_note)。同 note 重复登记=重放零副作用。
    """
    chash = fact_hash_of(closure_note.encode("utf-8"))
    own_store = artifact_store is None
    store = artifact_store or ArtifactStore(_artifacts_db())
    try:
        return store.record(Artifact(
            kind="closure_note",
            path="",
            fact_hash=chash,
            content_hash=chash,
            source_system="dev.tasks",
            source_ref=task_id,
            produced_by=by,
        ))
    finally:
        if own_store:
            store.close()


def close_done(task_id: str, by: str, closure_note: str,
               store: ApprovalStore = None,
               tasks_base: Path = None,
               artifact_store: ArtifactStore = None) -> Tuple[dict, bool]:
    """验收关闭（受门）：凭据先落 → store.move(done)。

    返回 (task_after, replayed)。
    - 幂等重放：凭据在且任务已 done → replayed=True 零写入；
    - 崩溃窗口 catch-up：凭据在、任务未 done → 补执行 move；
    - done 强制 closure_note（引擎纪律，提前校验保凭据语义完整）；
    - M3-P2：关闭达成（首次或 catch-up 补执行）时自动登记 closure
      产物（幂等登记不设门；artifact_store/tasks_base 可注入测试）。
    """
    if not by:
        raise DevWiringError("验收人 by 必填（审批可追溯）")
    if not (closure_note or "").strip():
        raise DevWiringError("done 强制 closure_note（防口头完成）")
    ts = _store(tasks_base)
    task = next((t for t in ts.ls() if t.get("task_id") == task_id), None)
    if task is None:
        raise DevWiringError("任务单不存在：%s" % task_id)
    cur = task["status"]
    if cur == "done":
        # 已 done：无凭据=绕门 done（审计会报）；有凭据=正常重放
        probe = store.list(target_id="dev:%s" % task_id) if store else []
        if probe:
            return task, True
        raise DevWiringError(
            "任务 %s 已 done 但账本无凭据（疑似绕门），拒绝背书" % task_id)
    if cur != "doing":
        # 凭据先行与引擎 move 校验必须同构：VALID_TRANSITIONS 仅
        # doing→done。open/assigned/blocked 放行会先落凭据、后 move
        # 恒败（TaskError），账本留下永远无法消费的死凭据。
        raise DevWiringError(
            "任务 %s 当前态 %s 不可验收 done（仅 doing→done，"
            "先经引擎流转到 doing）" % (task_id, cur))

    own_store = store is None
    store = store or ApprovalStore(_db_path())
    try:
        key = "dev:%s:done" % task_id
        approval, replayed = store.record(Approval(
            target_type="task",
            target_id="dev:%s" % task_id,
            from_status=cur,
            to_status="done",
            decision="approved",
            decided_by=by,
            reason=closure_note,
            decided_at=_now_iso(),
            idempotency_key=key,
        ))
        if replayed:
            # catch-up：凭据在、状态未达 → 补执行
            if cur != "done":
                task = ts.move(task_id, "done", by, note=closure_note)
                register_closure_artifact(task_id, closure_note, by,
                                          artifact_store)   # M3-P2 补登记
            return task, True
        task = ts.move(task_id, "done", by, note=closure_note)
        register_closure_artifact(task_id, closure_note, by,
                                  artifact_store)           # M3-P2 自动登记
        return task, False
    finally:
        if own_store:
            store.close()


def projection() -> dict:
    """只读投影：全任务 + done 凭据标记。"""
    ts = _store()
    tasks = ts.ls()
    db = _db_path()
    done_keys = set()
    if db.exists():
        store = ApprovalStore(db)
        try:
            done_keys = {c.idempotency_key for c in store.list()
                         if c.idempotency_key.endswith(":done")}
        finally:
            store.close()
    items = []
    for t in tasks:
        items.append({
            "task_id": t.get("task_id"),
            "title": t.get("title"),
            "assigned_dept": t.get("assigned_dept"),
            "status": t.get("status"),
            "severity": t.get("severity"),
            "opened_by": t.get("opened_by"),
            "has_done_credential":
                ("dev:%s:done" % t.get("task_id")) in done_keys,
            "history_len": len(t.get("history", [])),
        })
    return {"domain": "dev", "count": len(items), "tasks": items}


def audit_report() -> dict:
    """越门/缺凭据审计（只读回放）。

    - 任务 done 而无 dev:<id>:done 凭据 → bypass_done（FAIL）；
    - done 凭据而任务未 done → pending_execution（崩溃窗口）；
    - 凭据与 done 一致 → consistent。

存量收编判例（承 M0）：接线生效时刻（WIRING_TS）前关闭的 done 任务
记 grandfathered（历史存量，非绕门）；此后关闭而无凭据才是
bypass_done（FAIL）。
    """
    ts = _store()
    tasks = {t["task_id"]: t for t in ts.ls()}
    store = ApprovalStore(_db_path())
    try:
        creds = [c for c in store.list()
                 if c.target_id.startswith("dev:")]
    finally:
        store.close()
    violations = []
    checks = []
    for c in creds:
        tid = c.target_id.split(":", 1)[-1]
        task = tasks.get(tid)
        if task is None:
            violations.append({"kind": "credential_task_missing",
                               "approval_id": c.approval_id,
                               "task_id": tid})
            continue
        done = task.get("status") == "done"
        checks.append({
            "approval_id": c.approval_id,
            "task_id": tid,
            "decided_by": c.decided_by,
            "state": ("consistent" if done else "pending_execution"),
        })
    for tid, t in tasks.items():
        if t.get("status") != "done":
            continue
        if ("dev:%s:done" % tid) in {c.idempotency_key for c in creds}:
            continue
        done_at = ""
        for h in reversed(t.get("history", [])):
            if h.get("action", "").startswith("done"):
                done_at = h.get("at", "")
                break
        if done_at and done_at < WIRING_TS:
            continue  # 存量：接线生效时刻前关闭，收编不违规
        violations.append({
            "kind": "bypass_done",
            "task_id": tid,
            "detail": ("任务 done 但无验收凭据"
                       + ("（接线生效 %s 后关闭）" % WIRING_TS
                          if done_at else "（无法判定关闭时刻）")),
            "done_at": done_at,
        })
    # 交叉校验：凭据轮次 vs history 痕迹（dev=trace 域，action 即状态）
    traces = {}
    for tid, t in tasks.items():
        sts = [h.get("action") for h in (t.get("history") or [])
               if isinstance(h, dict)
               and isinstance(h.get("action"), str)
               and not h["action"].startswith("open")]
        traces["dev:%s" % tid] = {"statuses": sts, "consumed_keys": set()}
    cross_v = cross_check("dev", creds, traces,
                          {"dev:%s" % tid: t.get("status", "")
                           for tid, t in tasks.items()})
    return {
        "domain": "dev",
        "task_count": len(tasks),
        "credentials": checks,
        "violations": violations,
        "cross_check": cross_v,
        "verdict": verdict_for(
            ([{"severity": "FAIL"}] if violations else []) + cross_v),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 软件开发域接线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("report")
    p_mv = sub.add_parser("move")
    p_mv.add_argument("task_id"); p_mv.add_argument("new_status")
    p_mv.add_argument("--by", required=True); p_mv.add_argument("--note", default="")
    p_dn = sub.add_parser("done")
    p_dn.add_argument("task_id"); p_dn.add_argument("--by", required=True)
    p_dn.add_argument("--note", required=True, help="closure_note（强制）")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(projection(), ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print(json.dumps(audit_report(), ensure_ascii=False, indent=2))
    elif args.cmd == "move":
        print(json.dumps(move_task(args.task_id, args.new_status,
                                   args.by, args.note or None),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "done":
        task, replayed = close_done(args.task_id, args.by, args.note)
        print(json.dumps({"task": task, "replayed": replayed},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
