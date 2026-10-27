# -*- coding: utf-8 -*-
"""雪球历史播种自动轮 wrapper：夜班定时任务入口（与 xq_backfill_auto 同款哲学）。

职责：
  1. 读状态 seed_auto_state.json——done=true 直接退出（队列挖完，历史扩容收官）；
  2. 取当前 seed → 跑 xq_collect_1000.py --seed S --target 1000（独立输出文件）；
  3. 推进判据（满足其一即切下一个 seed）：
     a. 挖穿：seed_cursor.json 中该 seed 全部类目 exhausted=true；
     b. 贫矿：该 seed 连续 3 轮净增 < 50（仅 rc=0 正常轮计入，重叠区/无效号段）；
     c. 被挡：rc=1（限流/封禁）不计贫矿不推进，连续 6 轮被挡才放弃该段；
  4. 全队列推进完 → done=true 永久停；
  5. 状态日志 ASCII（排障判例），45min 自愈锁防并发。

seed 队列依据（探针 v0.2 锚点）：feed 序号空间从远(2014?)到近，
30000/45000 已有 R1/R2 存量但未挖穿，重扫浪费少量请求（seen 去重）换语义简单。

用法：python xq_seed_auto.py   （无参数，由 automation/计划任务触发）
rc：0=本轮正常结束（含 done 跳过）；1=异常。
"""

import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_ROOT = r"C:\aios\automation_runs"
BASE_DIR = os.path.join(RUNS_ROOT, "xq_backfill")
STATE_PATH = os.path.join(BASE_DIR, "seed_auto_state.json")
LOCK_PATH = os.path.join(BASE_DIR, "seed_auto.lock")
SEED_CURSOR_PATH = os.path.join(BASE_DIR, "seed_cursor.json")
LOCK_STALE_SEC = 45 * 60
LOG_DIR = BASE_DIR
STATUS_LOG = os.path.join(LOG_DIR, "xq_seed_status.log")
PY = r"C:\Program Files\Python311\python.exe"
TARGET = 1000
LOW_THRESHOLD = 50
LOW_LIMIT = 3
BLOCK_LIMIT = 6  # 连续被挡（rc=1 限流/封禁）轮数上限——放弃该段前的容忍窗口
CATS = (102, 104, 105, 111)
SEED_QUEUE = [
    20000,
    30000,
    45000,
    75000,
    105000,
    130000,
    150000,
    170000,
    190000,
    210000,
    230000,
]


def status(msg: str) -> None:
    line = "%s %s" % (datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), msg)
    print(line, flush=True)
    with open(STATUS_LOG, "a", encoding="ascii", errors="replace") as f:
        f.write(line + "\n")


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"queue_idx": 0, "done": False, "low_streak": 0, "rounds": []}


def save_state(st: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def db_count() -> int:
    import sqlite3

    db = os.path.join(HERE, "data", "xueqiu", "xueqiu_info.db")
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    n = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
    con.close()
    return n


def seed_exhausted(seed: int) -> bool:
    """该 seed 在断点档中全部类目 exhausted=true（空页到底）才算挖穿。"""
    try:
        with open(SEED_CURSOR_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        bucket = data.get(str(seed)) or {}
        states = [bucket.get(str(c), {}).get("exhausted") for c in CATS]
        return all(states) and len(states) == len(CATS)
    except Exception:
        return False


def lock_acquire() -> bool:
    import time as _t

    try:
        if os.path.exists(LOCK_PATH):
            age = _t.time() - os.path.getmtime(LOCK_PATH)
            if age < LOCK_STALE_SEC:
                return False
            status("stale lock age=%dmin, break it" % (age // 60))
        os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
        with open(LOCK_PATH, "w", encoding="ascii") as f:
            f.write(datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return True
    except Exception as e:
        status("lock error: %r (fail-visible, skip round)" % e)
        return False


def lock_release() -> None:
    try:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
    except Exception:
        pass


def main() -> int:
    os.makedirs(LOG_DIR, exist_ok=True)
    st = load_state()
    if st.get("done"):
        status("skip done=true (seed queue finished) total=%d" % db_count())
        return 0
    idx = st.get("queue_idx", 0)
    if idx >= len(SEED_QUEUE):
        st["done"] = True
        save_state(st)
        status("SEED QUEUE DONE total=%d" % db_count())
        return 0
    if not lock_acquire():
        status("skip lock held (another round active or fresh)")
        return 0
    seed = SEED_QUEUE[idx]
    base = db_count()
    stamp = datetime.now().strftime("%m%d_%H%M")
    out_path = os.path.join(LOG_DIR, "seed_round_%s_%d.out" % (stamp, seed))
    err_path = os.path.join(LOG_DIR, "seed_round_%s_%d.err" % (stamp, seed))
    status(
        "round start seed=%d (queue %d/%d) base=%d target=%d"
        % (seed, idx + 1, len(SEED_QUEUE), base, TARGET)
    )
    with (
        open(out_path, "w", encoding="utf-8") as fo,
        open(err_path, "w", encoding="utf-8") as fe,
    ):
        rc = subprocess.call(
            [
                PY,
                "-X",
                "utf8",
                os.path.join(HERE, "xq_collect_1000.py"),
                "--seed",
                str(seed),
                "--target",
                str(TARGET),
            ],
            stdout=fo,
            stderr=fe,
            cwd=HERE,
        )
    after = db_count()
    added = after - base
    status("round end rc=%d seed=%d added=%d total=%d" % (rc, seed, added, after))
    if rc not in (0, 1):
        status("round abnormal rc=%d (crash), no state change" % rc)
        lock_release()
        return 1
    st.setdefault("rounds", []).append(
        {"at": datetime.now().strftime("%m-%d %H:%M"), "seed": seed, "added": added}
    )
    st["rounds"] = st["rounds"][-30:]
    advanced = False
    if seed_exhausted(seed):
        advanced = True
        status("seed %d exhausted -> advance queue" % seed)
    if rc == 1 and added < LOW_THRESHOLD:
        # 采集器 ABORT（限流/封禁）≠ 贫矿：不计 low_streak、不推进——
        # 否则 feed 域临时限流 3 轮就能把整个队列错误烧穿并误写 done（10-18 实证）。
        # 连续 BLOCK_LIMIT 轮被挡才放弃该段（防死循环 + 给限流恢复留 ~3h 窗口）。
        st["blocked_streak"] = st.get("blocked_streak", 0) + 1
        if st["blocked_streak"] >= BLOCK_LIMIT and not advanced:
            advanced = True
            st["blocked_streak"] = 0
            status(
                "seed %d blocked %d rounds -> give up, advance (blocked NOT barren)"
                % (seed, BLOCK_LIMIT)
            )
        else:
            status(
                "seed %d blocked round (rc=1 added=%d) streak=%d, stay"
                % (seed, added, st["blocked_streak"])
            )
    elif added < LOW_THRESHOLD:
        st["low_streak"] = st.get("low_streak", 0) + 1
        if st["low_streak"] >= LOW_LIMIT and not advanced:
            advanced = True
            st["low_streak"] = 0
            status(
                "seed %d barren (%d low rounds) -> advance queue" % (seed, LOW_LIMIT)
            )
    else:
        st["low_streak"] = 0
        st["blocked_streak"] = 0
    if advanced:
        st["queue_idx"] = idx + 1
        if st["queue_idx"] >= len(SEED_QUEUE):
            st["done"] = True
            status("SEED QUEUE DONE total=%d" % after)
    save_state(st)
    lock_release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
