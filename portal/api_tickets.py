# -*- coding: utf-8 -*-
"""工单只读投影（驾驶舱一期·复盘舱数据面）。

读 agent_platform/data/org/tasks/tasks_YYYYMM.jsonl（TaskStore 按月账本）
→ GET /api/org/tasks 投影最近 N 条工单摘要。**纯只读 + fail-visible**：
读不到/坏行不抛错，返回 ok:false + error 说明，HTTP 恒 200（§8.1 判例，
与 api_execution 同契约）。

红线：
- 不 import engine/tasks.py（避免 8091 进程 sys.path 依赖引擎链）——直接
  读 jsonl 文件面，字段投影宁少勿假；
- errors="replace" 逐行读（api_capability _read_jsonl 判例），坏行跳过计数；
- 不提供任何写路径（工单写侧单源 = TaskStore，驾驶舱只看）。

Windows 编码判例：jsonl 是 UTF-8（TaskStore 写侧 ensure_ascii=False）。
"""

import json
from datetime import datetime
from pathlib import Path
import logging

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: TaskStore 按月账本目录（agent_platform/data/org/tasks/）
TASKS_DIR = _REPO_ROOT / "agent_platform" / "data" / "org" / "tasks"

#: 默认投影条数（复盘舱一张卡够了，宁少勿假）
DEFAULT_LIMIT = 20

#: 投影字段白名单（body/history 不投——卡面用不上，防泄漏长文）
_FIELDS = ("task_id", "title", "status", "severity",
           "opened_by", "assigned_dept", "created_at", "due")


def load_tickets(limit=DEFAULT_LIMIT, tasks_dir=None):
    """读工单按月账本，返回 (rows, ok, error, meta)。

    月度文件降序读（当月 → 上月 …），行倒序（最新单在前），凑满 limit
    即停。月文件缺失视为「该月无单」而非错误（月初常态）。

    Returns:
        tuple[list[dict], bool, str | None, dict]:
        (投影行, ok, 错误说明, meta{months_scanned, skipped_bad, total_files})
    """
    d = Path(tasks_dir) if tasks_dir else TASKS_DIR
    meta = {"months_scanned": 0, "skipped_bad": 0, "total_files": 0}
    if not d.is_dir():
        return [], False, "工单目录不存在: %s" % d, meta
    files = sorted(d.glob("tasks_*.jsonl"), reverse=True)
    meta["total_files"] = len(files)
    if not files:
        return [], False, "无 tasks_*.jsonl 账本文件", meta

    rows = []
    for f in files:
        meta["months_scanned"] += 1
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as e:
            return rows, False, "读取失败 %s: %s" % (f.name, e), meta
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                meta["skipped_bad"] += 1  # 坏行跳过，宁少勿假
                continue
            if not isinstance(rec, dict) or not rec.get("task_id"):
                meta["skipped_bad"] += 1
                continue
            rows.append({k: rec.get(k) for k in _FIELDS})
            if len(rows) >= limit:
                return rows, True, None, meta
    return rows, True, None, meta


def build_tickets_payload(limit=DEFAULT_LIMIT, tasks_dir=None):
    """GET /api/org/tasks 载荷（只读 + fail-visible，HTTP 恒 200）。"""
    now = datetime.now()
    rows, ok, error, meta = load_tickets(limit=limit, tasks_dir=tasks_dir)
    counts = {"open": 0, "doing": 0, "done": 0, "other": 0}
    for r in rows:
        s = r.get("status") or "other"
        counts[s] = counts.get(s, 0) + 1 if s in counts else counts["other"] + 1
    return {
        "read_only": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "ok": ok,
        "error": error,
        "count": len(rows),
        "status_counts": counts,
        "tasks": rows,
        "meta": meta,
    }


class Handler:
    """portal_api.Handler 多继承 mixin：GET /api/org/tasks 分发。

    支持 ?limit=N（1..100 截断）与 ?dept=dev 过滤（复盘舱默认看 dev）。
    """

    def _org_tasks(self, qs):
        qs = qs or ""
        limit = DEFAULT_LIMIT
        for part in qs.split("&"):
            if part.startswith("limit="):
                try:
                    limit = max(1, min(100, int(part[6:])))
                except ValueError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_tickets.py", exc)
        dept = None
        for part in qs.split("&"):
            if part.startswith("dept="):
                dept = part[5:] or None
        payload = build_tickets_payload(limit=limit)
        if dept:
            payload["tasks"] = [t for t in payload["tasks"]
                                if t.get("assigned_dept") == dept]
            payload["count"] = len(payload["tasks"])
        self._json(payload)
