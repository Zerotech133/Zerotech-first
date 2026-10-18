# -*- coding: utf-8 -*-
"""复盘简报只读投影（驾驶舱·复盘舱数据面二期 R5）。

读 automation_runs/retro_brief/briefs/YYYY-MM.jsonl（tools/retro_brief.py
生成的 append-only 简报账本）→ 模块级纯函数：

- load_brief_latest()  → GET /api/brief/latest 载荷
- load_brief_list(limit) → GET /api/brief/list 载荷（limit≤50）

**纯只读 + fail-visible**（api_tickets 判例）：简报目录尚未建立（计划任务
AgentRetrospectBrief 首跑前是常态）不报错，返回 available:false + reason；
坏行跳过计数，宁少勿假。

红线：
- 不 import 引擎链 / tools 生成器——直接读 jsonl 文件面（8091 进程无
  sys.path 引擎依赖）；
- 字段投影白名单（list 面不投 j_items/anomalies 长文，防卡面泄漏）；
- 不提供任何写路径（简报写侧单源 = retro_brief 生成器）。

Windows 编码判例：jsonl 是 UTF-8（生成器写侧 ensure_ascii=False）。
"""

import json
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 简报账本目录（automation_runs/retro_brief/briefs/）
BRIEFS_DIR = _REPO_ROOT / "automation_runs" / "retro_brief" / "briefs"

#: list 投影默认条数
LIST_LIMIT_DEFAULT = 20
#: list 投影条数上限（契约：limit≤50）
LIST_LIMIT_MAX = 50

#: list 投影字段白名单
_LIST_FIELDS = ("date", "kind", "verdict", "p1_count", "generated_at")

#: 无产物时的预期 reason（AgentRetrospectBrief 首跑前是常态）
PENDING_REASON = "AgentRetrospectBrief 首跑后自动点亮（每日 18:05）"


def _clamp_limit(limit):
    """limit 收敛到 [1, 50]（契约硬约束）。"""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return LIST_LIMIT_DEFAULT
    return max(1, min(LIST_LIMIT_MAX, n))


def _read_month(path):
    """读单月简报文件 → (有效简报列表, 坏行数)；读失败返回 ([], -1)。"""
    briefs, bad = [], 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return [], -1
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            bad += 1  # 坏行跳过，宁少勿假
            continue
        if isinstance(rec, dict) and rec.get("date"):
            briefs.append(rec)
        else:
            bad += 1
    return briefs, bad


def _month_files(briefs_dir):
    """月度文件按名降序（YYYY-MM.jsonl 字典序 = 时间序）。"""
    return sorted(Path(briefs_dir).glob("????-??.jsonl"), reverse=True)


def load_brief_latest(briefs_dir=None):
    """读最新一条简报（只读 + fail-visible）。

    契约字段（前端依赖，名不可改）：
    {ok, available, reason?, generated_at, brief:{...生成器简报结构}|None}
    """
    d = Path(briefs_dir) if briefs_dir else BRIEFS_DIR
    now = datetime.now().isoformat(timespec="seconds")
    if not d.is_dir():
        return {"ok": True, "available": False, "reason": PENDING_REASON,
                "generated_at": now, "brief": None}
    files = _month_files(d)
    if not files:
        return {"ok": True, "available": False, "reason": PENDING_REASON,
                "generated_at": now, "brief": None}
    for f in files:  # 当月 → 上月 …，取首个含有效行的文件的末行（最新）
        briefs, bad = _read_month(f)
        if bad < 0:
            return {"ok": False, "available": False,
                    "reason": "简报读取失败: %s" % f.name,
                    "generated_at": now, "brief": None}
        if briefs:
            return {"ok": True, "available": True, "generated_at": now,
                    "brief": briefs[-1]}
    return {"ok": True, "available": False,
            "reason": "简报文件暂无有效行", "generated_at": now,
            "brief": None}


def load_brief_list(limit=LIST_LIMIT_DEFAULT, briefs_dir=None):
    """读简报历史（新在前，append-only 可回看）。

    契约字段：
    {ok, items:[{date,kind,verdict,p1_count,generated_at}], count, meta}
    limit 收敛到 [1, 50]；月文件缺失视为「该月无简报」而非错误。
    """
    d = Path(briefs_dir) if briefs_dir else BRIEFS_DIR
    n = _clamp_limit(limit)
    meta = {"limit": n, "files_scanned": 0, "skipped_bad": 0}
    items = []
    if not d.is_dir():
        return {"ok": True, "items": items, "count": 0, "meta": meta}
    for f in _month_files(d):
        meta["files_scanned"] += 1
        briefs, bad = _read_month(f)
        if bad < 0:
            return {"ok": False, "items": [], "count": 0,
                    "meta": dict(meta, error="简报读取失败: %s" % f.name)}
        meta["skipped_bad"] += bad
        for rec in reversed(briefs):  # 行倒序 = 新在前
            items.append({
                "date": rec.get("date"),
                "kind": rec.get("kind"),
                "verdict": rec.get("verdict"),
                "p1_count": len(rec.get("p1_issues") or []),
                "generated_at": rec.get("generated_at"),
            })
            if len(items) >= n:
                return {"ok": True, "items": items, "count": len(items),
                        "meta": meta}
    return {"ok": True, "items": items, "count": len(items), "meta": meta}


class Handler:
    """portal_api.Handler 多继承 mixin（主会话负责挂载路由）。

    GET /api/brief/latest           → load_brief_latest()
    GET /api/brief/list?limit=N     → load_brief_list(limit)
    """

    def _brief_latest(self, qs):
        self._json(load_brief_latest())

    def _brief_list(self, qs):
        qs = qs or ""
        limit = LIST_LIMIT_DEFAULT
        for part in qs.split("&"):
            if part.startswith("limit="):
                limit = _clamp_limit(part[6:])
        self._json(load_brief_list(limit=limit))
