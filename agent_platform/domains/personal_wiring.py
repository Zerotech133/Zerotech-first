# -*- coding: utf-8 -*-
"""M1 个人协同域接线 · schedules 受控写路径（域 #3）。

真源：agent_platform/data/schedules.json（个人/军团定时排程）。
既有写入口 portal 8093 api_legion_ops_v2.schedules_op（add/delete 无门）
保持不动（旧链）；本模块是新增受控路径，纪律与 gzh_wiring 一致：

六铁律（承 GZH/Academy 判例）：
1. 状态真源不动——schedules.json 仍是唯一真源，条目自带 enabled 字段；
2. 无 Approval 不 delete——删除不可逆，必须先落凭据（幂等键
   sched:<sid>:delete）再改真源；凭据先于写，崩溃窗口 catch-up；
3. pause/resume（enabled 翻转）= 正常工作流，走本模块单一写路径并
   追加域内 history，凭据不泛滥；
4. 无源 ID 不伪造——sid 不存在显式报错，不静默；
5. 时间保持域内 naive 本地格式（对齐 created 字段，M0 判例不补 UTC）；
6. history 字段随条目持久（legion_scheduler.load 只读 cron/enabled/
   weekdays_only，多余字段无害）；portal 旧链 add/delete 会整条移除
   history——审计局限：绕门删除只能从账本凭据侧回放（凭据在、条目
   不在=已执行；凭据在、条目还在=待执行），无消失前快照，诚实记录。

CLI：list / pause / resume / delete / report
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval            # noqa: E402
from engine.approvals import ApprovalStore        # noqa: E402

SCHED_FILE = _AGENT_PLATFORM / "data" / "schedules.json"


def _db_path() -> Path:
    return _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"


class PersonalWiringError(Exception):
    """受控转移非法（未知 id / 已同态 / 幂等冲突）。"""


def _now_cn() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def load_schedules() -> List[dict]:
    try:
        rows = json.loads(SCHED_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return rows if isinstance(rows, list) else []


def save_schedules(rows: List[dict]) -> None:
    SCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(SCHED_FILE)  # 原子替换，防半写


def find_schedule(sid: str) -> Optional[dict]:
    for r in load_schedules():
        if r.get("id") == sid:
            return r
    return None


def _append_history(item: dict, action: str, by: str, note: str) -> None:
    hist = item.setdefault("history", [])
    hist.append({"at": _now_cn(), "action": action, "by": by,
                 "note": note})


def projection() -> dict:
    """只读投影：条目 + 每条是否有 delete 凭据。"""
    items = []
    for r in load_schedules():
        items.append({
            "id": r.get("id", ""),
            "agent": r.get("agent", ""),
            "desc": r.get("desc", ""),
            "cron": r.get("cron", ""),
            "weekdays_only": bool(r.get("weekdays_only", True)),
            "enabled": bool(r.get("enabled", False)),
            "worker": bool(r.get("worker", False)),
            "created": r.get("created", ""),
            "history_len": len(r.get("history", [])),
        })
    return {"domain": "personal", "count": len(items), "schedules": items}


def set_enabled(sid: str, enabled: bool, by: str, note: str = ""
                ) -> dict:
    """pause/resume：enabled 翻转（正常工作流，无需凭据）。"""
    if not by:
        raise PersonalWiringError("操作人 by 必填（可追溯）")
    rows = load_schedules()
    item = next((r for r in rows if r.get("id") == sid), None)
    if item is None:
        raise PersonalWiringError("schedule %s 不存在" % sid)
    if bool(item.get("enabled", False)) == enabled:
        raise PersonalWiringError(
            "schedule %s 已处于 enabled=%s，无变化" % (sid, enabled))
    action = "pause" if not enabled else "resume"
    item["enabled"] = enabled
    _append_history(item, action, by, note or action)
    save_schedules(rows)
    return item


def delete_schedule(sid: str, by: str, reason: str = "",
                    store: ApprovalStore = None) -> Tuple[dict, bool]:
    """删除排程（不可逆 → 必须 Approval 凭据）。

    返回 (deleted_item, replayed)。
    - sid 不存在 → PersonalWiringError，零写入；
    - 幂等重放（同凭据键已存在）→ 按账本凭据确认删除语义：
      条目已不在 = replayed=True（崩溃后重试安全）；
      条目还在 = 凭据已落但写未发生 → 补执行（catch-up）；
    - 首次删除 → 落凭据成功后才从真源移除条目。
    """
    if not by:
        raise PersonalWiringError("批准人 by 必填（审批可追溯）")
    rows = load_schedules()
    item = next((r for r in rows if r.get("id") == sid), None)
    if item is None:
        # 已不在真源：查凭据，有凭据=幂等重放，无凭据=无源 ID。
        # store 可能未传（CLI 默认路径）——必须先建 store 再探查，
        # 否则首次删除后的重试会误报"不存在"（幂等重放失效，2026-10-17 判例）。
        own_probe = store is None
        store = store or ApprovalStore(_db_path())
        try:
            probe = store.list(target_id="sched:%s" % sid)
        finally:
            if own_probe:
                store.close()
        if probe:
            return probe[-1].__dict__, True
        raise PersonalWiringError("schedule %s 不存在（无凭据，不伪造）"
                                  % sid)

    own_store = store is None
    store = store or ApprovalStore(_db_path())
    try:
        key = "sched:%s:delete" % sid
        approval, replayed = store.record(Approval(
            target_type="schedule",
            target_id="sched:%s" % sid,
            from_status="enabled" if item.get("enabled") else "disabled",
            to_status="deleted",
            decision="approved",
            decided_by=by,
            reason=reason,
            decided_at=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            idempotency_key=key,
        ))
        if replayed:
            # 崩溃窗口 catch-up：凭据在而条目还在 → 补执行
            rows = [r for r in rows if r.get("id") != sid]
            save_schedules(rows)
            return item, True

        save_schedules([r for r in rows if r.get("id") != sid])
        return item, False
    finally:
        if own_store:
            store.close()


def audit_report() -> dict:
    """越门/待执行审计（只读回放）。

    - delete 凭据存在且条目仍在 → pending_execution（warning，
      属崩溃窗口待 catch-up 或有意保留待确认）；
    - delete 凭据存在且条目不在 → executed（ok）；
    - 本域无「消失前快照」，绕门删除（portal 旧链）无法从本域侧
      直接检出——局限诚实声明，绕门痕迹需 portal 审计心跳侧对比。
    """
    items = load_schedules()
    present = {r.get("id") for r in items}
    store = ApprovalStore(_db_path())
    try:
        creds = store.list(target_type="schedule")
    finally:
        store.close()
    checks = []
    for c in creds:
        sid = c.target_id.split(":", 1)[-1]
        executed = sid not in present
        checks.append({
            "approval_id": c.approval_id,
            "target_id": c.target_id,
            "decided_by": c.decided_by,
            "decided_at": c.decided_at,
            "state": "executed" if executed else "pending_execution",
        })
    violations = []  # 本域侧无消失快照，检测不到越门删除（见 docstring 局限）
    return {
        "domain": "personal",
        "schedule_count": len(items),
        "delete_credentials": checks,
        "violations": violations,
        "verdict": "PASS",
        "limitation": "无消失前快照：portal 旧链 delete 无法从本域侧检出",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 个人协同域接线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("report")
    p_set = sub.add_parser("pause"); p_set.add_argument("sid")
    p_set.add_argument("--by", required=True); p_set.add_argument("--note", default="")
    p_res = sub.add_parser("resume"); p_res.add_argument("sid")
    p_res.add_argument("--by", required=True); p_res.add_argument("--note", default="")
    p_del = sub.add_parser("delete"); p_del.add_argument("sid")
    p_del.add_argument("--by", required=True); p_del.add_argument("--reason", default="")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(projection(), ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print(json.dumps(audit_report(), ensure_ascii=False, indent=2))
    elif args.cmd == "pause":
        print(json.dumps(set_enabled(args.sid, False, args.by, args.note),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "resume":
        print(json.dumps(set_enabled(args.sid, True, args.by, args.note),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "delete":
        deleted, replayed = delete_schedule(args.sid, args.by, args.reason)
        print(json.dumps({"deleted": deleted, "replayed": replayed},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
