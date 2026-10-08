# -*- coding: utf-8 -*-
"""任务单系统（K-ORG-010 P2-1）：跨部门协作的唯一通道——禁直读记忆，只走任务单

存储：data/org/tasks/tasks_<YYYYMM>.jsonl，append-only——每次操作追加完整快照行，
task 当前态 = 该 task_id 的最后一行（对齐影子账本/事件账的快照行纪律）。
RBAC：操作者 by 格式 "A0"（全权）或 "dept/<dept>/<agent>"——move 仅限 A0 或
任务 assigned_dept 的成员；open 仅 A0 与部门成员。

状态机（K-ORG-010 §二）：
    open → assigned → doing → done（强制 closure_note）
                     doing → blocked（必写原因）→ doing
    open/assigned → cancelled（开单方）；rejected（被派方，须写理由）
    P0 事件可直达 doing（p0=True 跳过派单等待）
"""
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

VALID_TRANSITIONS = {
    "open": {"assigned", "doing", "cancelled", "rejected"},
    "assigned": {"doing", "blocked", "cancelled", "rejected"},
    "doing": {"blocked", "done"},
    "blocked": {"doing", "cancelled"},
    "done": set(), "rejected": set(), "cancelled": set(),
}
SEVERITIES = ("P0", "P1", "P2")
DEPTS = ("dev", "invest", "strategy", "trading")


class TaskError(Exception):
    pass


class PermissionDenied(TaskError):
    pass


class TaskStore:
    def __init__(self, base):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def _file(self, ts=None):
        stamp = (ts or datetime.now()).strftime("%Y%m")
        return self.base / ("tasks_%s.jsonl" % stamp)

    def _load_index(self):
        """task_id → 最新快照行（读全部月度文件，按行序后者覆盖）"""
        idx = {}
        for f in sorted(self.base.glob("tasks_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    idx[r["task_id"]] = r
                except (json.JSONDecodeError, KeyError):
                    continue
        return idx

    def _check_perm(self, task, by, action):
        if by == "A0":
            return
        if not by.startswith("dept/"):
            raise PermissionDenied("操作者格式须为 A0 或 dept/<dept>/<agent>：%r" % by)
        dept = by.split("/")[1]
        if action in ("move", "reject") and dept != task.get("assigned_dept"):
            raise PermissionDenied("%s 无权操作 %s 的任务单（属 %s）" % (
                dept, task["task_id"], task.get("assigned_dept")))

    def open(self, to_dept, title, body, severity="P2", opened_by="A0",
             assigned_agent=None, due=None, p0=False):
        if to_dept not in DEPTS:
            raise TaskError("未知部门 %r（可选：%s）" % (to_dept, "/".join(DEPTS)))
        if severity not in SEVERITIES:
            raise TaskError("severity 须为 %s" % "/".join(SEVERITIES))
        now = datetime.now()
        with self.lock:
            idx = self._load_index()
            seq = len(idx) + 1
            task_id = "T-%s-%03d" % (now.strftime("%Y%m%d"), seq)
            task = {
                "task_id": task_id, "opened_by": opened_by,
                "assigned_dept": to_dept, "assigned_agent": assigned_agent,
                "title": title, "body": body, "severity": severity,
                "status": "doing" if (p0 and severity == "P0") else "open",
                "created_at": now.isoformat(timespec="seconds"),
                "due": due, "closure_note": None,
                "history": [{"at": now.isoformat(timespec="seconds"), "by": opened_by,
                             "action": "open" if not p0 else "open(P0直入doing)"}],
            }
            with open(self._file(now), "a", encoding="utf-8") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
        return task_id

    def ls(self, dept=None, status=None):
        out = [t for t in self._load_index().values()
               if (dept is None or t["assigned_dept"] == dept)
               and (status is None or t["status"] == status)]
        return sorted(out, key=lambda t: (t["severity"], t["task_id"]))

    def move(self, task_id, new_status, by, note=None):
        task = self._load_index().get(task_id)
        if not task:
            raise TaskError("任务单不存在：%s" % task_id)
        cur = task["status"]
        if new_status not in VALID_TRANSITIONS.get(cur, set()):
            raise TaskError("非法转移 %s → %s（当前态 %s 允许：%s）" % (
                cur, new_status, cur, "/".join(sorted(VALID_TRANSITIONS[cur])) or "终态"))
        if new_status == "done" and not (note or "").strip():
            raise TaskError("done 强制 closure_note（防口头完成）")
        if new_status in ("blocked", "rejected") and not (note or "").strip():
            raise TaskError("%s 必须写原因" % new_status)
        self._check_perm(task, by, "move")
        now = datetime.now()
        task["status"] = new_status
        if new_status in ("done", "blocked", "rejected"):
            task["closure_note"] = note
        task["history"].append({"at": now.isoformat(timespec="seconds"),
                                "by": by, "action": new_status, "note": note})
        with self.lock:
            with open(self._file(now), "a", encoding="utf-8") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
        return task
