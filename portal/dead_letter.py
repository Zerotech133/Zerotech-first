# -*- coding: utf-8 -*-
"""K-ORG-201 W38：送达死信账（dead_letters.jsonl）。

投递失败（hub 闸内直发失败 / 决策执行 attempt 失败）落一行死信，待补达；
补达时 mark_redelivered 追加**独立补达行**（redelivery_of 引用原 dead_id），
不改原行——补达成功不冒充原发送成功（送达证据诚实分级）。list 时把同
dead_id 的补达行挂到原行 redeliveries 数组联动展示。

落盘判例承 push_flow.py：O_APPEND + 单次 os.write 整行 + threading.Lock
（只保护同进程；fail-safe 吞异常不抛，死信丢失可容忍、主链路不可断）。
"""
import json
import os
import threading
import uuid
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
DEAD_PATH = os.path.join(WORK, "dead_letters.jsonl")
_lock = threading.Lock()


def _append(row):
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    os.makedirs(WORK, exist_ok=True)
    with _lock:
        fd = os.open(DEAD_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def register(kind, channel, target, why, decision_id=None,
             evidence="dead_letter"):
    """登记一条死信 → dead_id（失败 None，不抛）。evidence 缺省
    dead_letter；传入非法级别也醒目归 dead_letter（死信行不冒充其他证据）。"""
    try:
        row = {"ts": datetime.now().isoformat(timespec="seconds"),
               "dead_id": str(uuid.uuid4()),
               "kind": str(kind or "?")[:80],
               "channel": str(channel or "?")[:40],
               "target": str(target or "")[:120],
               "why": str(why or "")[:300],
               "decision_id": decision_id,
               "evidence": evidence if evidence in
                           ("api_accepted", "receiver_verified", "dead_letter")
                           else "dead_letter",
               "redelivered_at": None,
               "redelivery_message_id": None}
        _append(row)
        return row["dead_id"]
    except Exception:
        return None


def mark_redelivered(dead_id, ok, message_id=None, why=""):
    """补达回执：追加独立补达行（不改动原行），redelivery_of 引用原 dead_id。
    → True/False。"""
    try:
        row = {"ts": datetime.now().isoformat(timespec="seconds"),
               "redelivery_of": dead_id,
               "action": "redelivery",
               "ok": bool(ok),
               "message_id": str(message_id)[:128] if message_id else None,
               "why": str(why or "")[:300]}
        _append(row)
        return True
    except Exception:
        return False


def list_dead_letters(limit=50):
    """最近 N 条死信倒序；同 dead_id 补达行挂原行 redeliveries 数组
    （原行自身字段不动）。文件缺失/损坏行跳过，返回空表不抛。"""
    try:
        with open(DEAD_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    originals, redeliveries = [], {}
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if r.get("action") == "redelivery":
            redeliveries.setdefault(r.get("redelivery_of"), []).append(r)
        else:
            originals.append(r)
    for row in originals:
        linked = redeliveries.get(row.get("dead_id"))
        if linked:
            row["redeliveries"] = linked
    originals.reverse()
    return originals[:max(0, int(limit))]
