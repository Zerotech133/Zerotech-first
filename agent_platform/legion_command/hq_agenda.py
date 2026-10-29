# -*- coding: utf-8 -*-
"""会前议程 v1（P1）：收编 8798 议会厅的议题/留言/预约（只读）+ 留言追加。

数据源沿用 `portal/data/`（council_new_topics.json / council_messages.jsonl /
council_scheduled.jsonl），不迁移、不删除 8798 存量；8798 转只读归档。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_jsonl(p: Path, limit: int = 200):
    rows = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                rows.append(d)
    except OSError:
        pass
    return rows[-limit:]


def _r(v, n: int) -> str:
    return str(v or "")[:n]


def load_agenda(dirpath) -> dict:
    d = Path(dirpath)
    topics = _read_json(d / "council_new_topics.json", [])
    if not isinstance(topics, list):
        topics = []
    messages = _read_jsonl(d / "council_messages.jsonl", 200)
    scheduled = _read_jsonl(d / "council_scheduled.jsonl", 20)
    archives = 0
    am = d / "council_meetings"
    if am.exists():
        try:
            archives = sum(1 for x in am.rglob("*") if x.is_file())
        except OSError:
            archives = 0
    recent_events = []
    try:
        # acceptance_done = 黄忠日更验收联动（WO-HZ-MEET-20261030）：会前议程可见
        # 「今天验收单出了什么结论」，与 merge_done/guard_fail 同列。
        for row in _read_jsonl(d / "dev_events.jsonl", 400):
            if row.get("kind") in ("merge_done", "guard_fail", "meeting_saved",
                                   "meeting_dispatched", "acceptance_done"):
                recent_events.append(row)
    except OSError:
        recent_events = []
    recent_events = recent_events[-10:]
    return {
        "ok": True,
        "recent_events": recent_events,
        "events_count": len(recent_events),
        "source": "council_ops(8798) 收编只读；8798 转只读归档、不再启动",
        "topics": topics[-20:],
        "messages": messages[-50:],
        "scheduled": scheduled[-10:],
        "archive_files": archives,
        "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def append_message(dirpath, req) -> tuple[int, dict]:
    if not isinstance(req, dict):
        return 400, {"ok": False, "error": "bad_request"}
    topic = _r(req.get("topic"), 120).strip()
    text = _r(req.get("text"), 2000).strip()
    if not topic:
        return 400, {"ok": False, "error": "bad_topic"}
    if not text:
        return 400, {"ok": False, "error": "bad_text"}
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "topic": topic,
        "seat": _r(req.get("seat") or "laotian", 24),
        "kind": _r(req.get("kind") or "hq", 16),
        "text": text,
    }
    p = Path(dirpath) / "council_messages.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return 500, {"ok": False, "error": "message_save_failed"}
    return 200, {"ok": True, "message": row}
