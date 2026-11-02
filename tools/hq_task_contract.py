#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-02 原子领取（checkout）契约 v1——纯函数、默认不着盘。

状态机：
  parked/ready/todo → doing（claim）
  doing → review（submit）
  review → done（complete）
  doing/review → parked（release/abort）
  done/aborted → 终态（重开走 L2）

并发：
  - `claim` 对单任务做 CAS：只有 status 可领取且 owner 为空/本人时才成功；
  - 生产写入必须由唯一 HQ 服务持文件锁 + 临时文件 + os.replace；
  - 本模块只返回决策/新任务对象，不写 tasks.json。

CLI（只读演练）：
  python -X utf8 tools/hq_task_contract.py --id <task_id> --owner D1
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / ".worktrees" / "tasks.json"
CLAIMABLE = {"parked", "ready", "todo", ""}
ACTIVE = {"doing", "review"}
TERMINAL = {"done", "aborted"}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now()


def _lease(now: datetime, minutes: int) -> str:
    return (_now(now) + timedelta(minutes=max(1, minutes))).strftime("%Y-%m-%d %H:%M:%S")


def claim(tasks: dict, task_id: str, owner: str, now: datetime | None = None,
          lease_minutes: int = 120) -> dict:
    """尝试领取任务；返回 {ok, code, task?, previous_status?}，不修改入参。"""
    if not owner:
        return {"ok": False, "code": "owner_required"}
    t = tasks.get(task_id) if isinstance(tasks, dict) else None
    if not isinstance(t, dict):
        return {"ok": False, "code": "not_found"}
    st = str(t.get("status") or "")
    cur_owner = str(t.get("owner") or "")
    if st in TERMINAL:
        return {"ok": False, "code": "terminal", "status": st}
    if st in ACTIVE:
        if cur_owner == owner:
            # 同一 owner 重复领取 = 续租/幂等
            new = deepcopy(t)
            new["lease_until"] = _lease(now, lease_minutes)
            new["revision"] = int(t.get("revision") or 0) + 1
            return {"ok": True, "code": "renewed", "task": new, "previous_status": st}
        return {"ok": False, "code": "busy", "owner": cur_owner, "status": st}
    if st not in CLAIMABLE:
        return {"ok": False, "code": "not_claimable", "status": st}
    if cur_owner and cur_owner != owner:
        return {"ok": False, "code": "owned_by_other", "owner": cur_owner}
    new = deepcopy(t)
    new["status"] = "doing"
    new["owner"] = owner
    new["claimed_at"] = _now(now).strftime("%Y-%m-%d %H:%M:%S")
    new["lease_until"] = _lease(now, lease_minutes)
    new["revision"] = int(t.get("revision") or 0) + 1
    return {"ok": True, "code": "claimed", "task": new, "previous_status": st}


def release(tasks: dict, task_id: str, owner: str, now: datetime | None = None) -> dict:
    t = tasks.get(task_id) if isinstance(tasks, dict) else None
    if not isinstance(t, dict):
        return {"ok": False, "code": "not_found"}
    st = str(t.get("status") or "")
    if st not in ACTIVE:
        return {"ok": False, "code": "not_active", "status": st}
    if str(t.get("owner") or "") != owner:
        return {"ok": False, "code": "not_owner", "owner": t.get("owner")}
    new = deepcopy(t)
    new["status"] = "parked"
    new["owner"] = ""
    new.pop("lease_until", None)
    new["released_at"] = _now(now).strftime("%Y-%m-%d %H:%M:%S")
    new["revision"] = int(t.get("revision") or 0) + 1
    return {"ok": True, "code": "released", "task": new}


def submit_review(tasks: dict, task_id: str, owner: str) -> dict:
    t = tasks.get(task_id) if isinstance(tasks, dict) else None
    if not isinstance(t, dict):
        return {"ok": False, "code": "not_found"}
    if str(t.get("status") or "") != "doing":
        return {"ok": False, "code": "not_doing"}
    if str(t.get("owner") or "") != owner:
        return {"ok": False, "code": "not_owner"}
    new = deepcopy(t)
    new["status"] = "review"
    new["revision"] = int(t.get("revision") or 0) + 1
    return {"ok": True, "code": "submitted", "task": new}


def is_lease_active(task: dict, now: datetime | None = None) -> bool:
    lu = str((task or {}).get("lease_until") or "")
    if not lu:
        return False
    try:
        return datetime.strptime(lu, "%Y-%m-%d %H:%M:%S") > _now(now)
    except Exception:  # noqa: BLE001
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-02 原子领取演练（只读）")
    ap.add_argument("--id", required=True)
    ap.add_argument("--owner", required=True)
    args = ap.parse_args(argv)
    try:
        tasks = json.loads(TASKS.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": "tasks_unreadable", "error": str(exc)}, ensure_ascii=False))
        return 1
    res = claim(tasks, args.id, args.owner)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
