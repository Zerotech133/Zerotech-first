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
import logging

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


def register(
    kind, channel, target, why, decision_id=None, evidence="dead_letter", payload=None
):
    """登记一条死信 → dead_id（失败 None，不抛）。evidence 缺省
    dead_letter；传入非法级别也醒目归 dead_letter（死信行不冒充其他证据）。"""
    try:
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "dead_id": str(uuid.uuid4()),
            "kind": str(kind or "?")[:80],
            "channel": str(channel or "?")[:40],
            "target": str(target or "")[:120],
            "why": str(why or "")[:300],
            "decision_id": decision_id,
            "evidence": evidence
            if evidence in ("api_accepted", "receiver_verified", "dead_letter")
            else "dead_letter",
            "redelivered_at": None,
            "redelivery_message_id": None,
            "payload": (str(payload)[:2000] if payload else None),
        }
        _append(row)
        return row["dead_id"]
    except Exception:
        return None


def mark_redelivered(dead_id, ok, message_id=None, why=""):
    """补达回执：追加独立补达行（不改动原行），redelivery_of 引用原 dead_id。
    → True/False。"""
    try:
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "redelivery_of": dead_id,
            "action": "redelivery",
            "ok": bool(ok),
            "message_id": str(message_id)[:128] if message_id else None,
            "why": str(why or "")[:300],
        }
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
    return originals[: max(0, int(limit))]


# ── N6（派工单 2026-10-19 / P0-4）：死信 24h 计数（只读，供观测面） ──
_COUNT_CACHE = {"ts": 0.0, "n": -1, "hours": 0}


def count_since(hours=24):
    """近 N 小时死信条数（60s 缓存防大文件频读）。

    按 ts 字段解析（缺 ts/损坏行跳过不计）；文件缺失=0（合法空态）；
    读取异常=-1（观测面显示 unknown，不猜数、不假绿）。"""
    import time as _t

    now = _t.time()
    if now - _COUNT_CACHE["ts"] < 60.0 and _COUNT_CACHE["hours"] == hours:
        return _COUNT_CACHE["n"]
    n = -1
    try:
        if not os.path.exists(DEAD_PATH):
            n = 0
        else:
            cutoff = datetime.fromtimestamp(now - hours * 3600)
            cnt = 0
            with open(DEAD_PATH, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        # 只计原始死信：补达行（action=redelivery / 无 dead_id）不是死信
                        if str(row.get("action") or "") == "redelivery" or not row.get(
                            "dead_id"
                        ):
                            continue
                        ts = datetime.fromisoformat(str(row.get("ts") or "")[:19])
                        if ts >= cutoff:
                            cnt += 1
                    except (ValueError, TypeError):
                        continue
            n = cnt  # 完整读完才落计数——读中断/异常保持 -1，不假绿
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/dead_letter.py", exc)
    _COUNT_CACHE.update({"ts": now, "n": n, "hours": hours})
    return n
