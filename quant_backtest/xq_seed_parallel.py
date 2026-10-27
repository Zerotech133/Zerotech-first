# -*- coding: utf-8 -*-
"""雪球历史播种并行版（方案一，2026-10-18 老田拍板）：双实例奇偶种子分片。

与 xq_seed_auto.py（单实例串行）同款推进判据，三点分片化改造：
  1. 种子分片：A=偶数位 [20000,45000,105000,150000,190000,230000]，
     B=奇数位 [30000,75000,130000,170000,210000]——11 种子两实例并行，
     预计追平 5-7 天 → 3-4 天；
  2. 游标分片：--cursor-file 传 seed_cursor_A.json / _B.json 给
     xq_collect_1000.py（save_seed_cursor 是整文件读改写，双实例共写
     单文件必互踩——collect 需加该参数，2026-10-19 实施）；
  3. 状态/锁分片：seed_auto_state_A/B.json + seed_auto_A/B.lock。

共享资源与纪律：
  - xq_seed_status.log 双实例共享追加（短行 <4KB append 原子，行首带
    [A]/[B] 实例标记，ASCII 排障判例不变）；
  - 数据面 pk=status_id upsert + seen 去重，双写无冲突；
  - 请求速率 ×2 为代理池安全圈内（方案一拍板口径），不再叠加类目并发；
  - 推进判据照抄单实例版：挖穿（全类目 exhausted）/ 贫矿（连续 3 轮
    净增 <50）/ 被挡（rc=1 连续 6 轮才放弃，不计贫矿）。

用法：python xq_seed_parallel.py --instance A   （automation/计划任务触发）
rc：0=本轮正常结束（含 done 跳过）；1=异常。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_ROOT = r"C:\aios\automation_runs"
BASE_DIR = os.path.join(RUNS_ROOT, "xq_backfill")
LOCK_STALE_SEC = 45 * 60
PY = r"C:\Program Files\Python311\python.exe"
TARGET = 1000
LOW_THRESHOLD = 50
LOW_LIMIT = 3
BLOCK_LIMIT = 6
CATS = (102, 104, 105, 111)

# 与 xq_seed_auto.SEED_QUEUE 同源同序；奇偶分片保证两实例种子集不相交
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
SHARDS = {
    "A": [s for i, s in enumerate(SEED_QUEUE) if i % 2 == 0],
    "B": [s for i, s in enumerate(SEED_QUEUE) if i % 2 == 1],
}


def paths_for(instance: str) -> dict:
    return {
        "state": os.path.join(BASE_DIR, "seed_auto_state_%s.json" % instance),
        "lock": os.path.join(BASE_DIR, "seed_auto_%s.lock" % instance),
        "cursor": os.path.join(BASE_DIR, "seed_cursor_%s.json" % instance),
    }


def status(instance: str, msg: str) -> None:
    line = "%s [%s] %s" % (
        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        instance,
        msg,
    )
    print(line, flush=True)
    # v1.0.1 延迟求值（10-18 疑案2 判例）：模块级常量在 import 时按真实
    # BASE_DIR 求值，测试 monkeypatch BASE_DIR 后仍写生产日志（[A] 泄漏）。
    # 改为调用时拼接——patch BASE_DIR 即整体隔离，旧版测试也无法再污染。
    log_path = os.path.join(BASE_DIR, "xq_seed_status.log")
    with open(log_path, "a", encoding="ascii", errors="replace") as f:
        f.write(line + "\n")


def load_state(p_state: str) -> dict:
    try:
        with open(p_state, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "queue_idx": 0,
            "done": False,
            "low_streak": 0,
            "blocked_streak": 0,
            "rounds": [],
        }


def save_state(p_state: str, st: dict) -> None:
    tmp = p_state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p_state)


def db_count() -> int:
    import sqlite3

    db = os.path.join(HERE, "data", "xueqiu", "xueqiu_info.db")
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    n = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
    con.close()
    return n


def seed_exhausted(p_cursor: str, seed: int) -> bool:
    """该 seed 在分片断点档中全部类目 exhausted=true。"""
    try:
        with open(p_cursor, "r", encoding="utf-8") as f:
            data = json.load(f)
        bucket = data.get(str(seed)) or {}
        states = [bucket.get(str(c), {}).get("exhausted") for c in CATS]
        return all(states) and len(states) == len(CATS)
    except Exception:
        return False


def lock_acquire(p_lock: str, instance: str) -> bool:
    import time as _t

    try:
        if os.path.exists(p_lock):
            age = _t.time() - os.path.getmtime(p_lock)
            if age < LOCK_STALE_SEC:
                return False
            status(instance, "stale lock age=%dmin, break it" % (age // 60))
        os.makedirs(os.path.dirname(p_lock), exist_ok=True)
        with open(p_lock, "w", encoding="ascii") as f:
            f.write(datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return True
    except Exception as e:
        status(instance, "lock error: %r (fail-visible, skip round)" % e)
        return False


def lock_release(p_lock: str) -> None:
    try:
        if os.path.exists(p_lock):
            os.remove(p_lock)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xq_seed_parallel.py", exc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True, choices=sorted(SHARDS))
    args = ap.parse_args(argv)
    inst = args.instance
    queue = SHARDS[inst]
    p = paths_for(inst)

    os.makedirs(BASE_DIR, exist_ok=True)
    st = load_state(p["state"])
    if st.get("done"):
        status(inst, "skip done=true (shard queue finished) total=%d" % db_count())
        return 0
    idx = st.get("queue_idx", 0)
    if idx >= len(queue):
        st["done"] = True
        save_state(p["state"], st)
        status(inst, "SHARD QUEUE DONE total=%d" % db_count())
        return 0
    if not lock_acquire(p["lock"], inst):
        status(inst, "skip lock held (another round active or fresh)")
        return 0
    seed = queue[idx]
    base = db_count()
    stamp = datetime.now().strftime("%m%d_%H%M")
    out_path = os.path.join(BASE_DIR, "seed_round_%s_%s_%d.out" % (stamp, inst, seed))
    err_path = os.path.join(BASE_DIR, "seed_round_%s_%s_%d.err" % (stamp, inst, seed))
    status(
        inst,
        "round start seed=%d (shard queue %d/%d) base=%d target=%d"
        % (seed, idx + 1, len(queue), base, TARGET),
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
                "--cursor-file",
                p["cursor"],
            ],
            stdout=fo,
            stderr=fe,
            cwd=HERE,
        )
    after = db_count()
    added = after - base
    status(inst, "round end rc=%d seed=%d added=%d total=%d" % (rc, seed, added, after))
    if rc not in (0, 1):
        status(inst, "round abnormal rc=%d (crash), no state change" % rc)
        lock_release(p["lock"])
        return 1
    st.setdefault("rounds", []).append(
        {"at": datetime.now().strftime("%m-%d %H:%M"), "seed": seed, "added": added}
    )
    st["rounds"] = st["rounds"][-30:]
    advanced = False
    if seed_exhausted(p["cursor"], seed):
        advanced = True
        status(inst, "seed %d exhausted -> advance shard queue" % seed)
    if rc == 1 and added < LOW_THRESHOLD:
        st["blocked_streak"] = st.get("blocked_streak", 0) + 1
        if st["blocked_streak"] >= BLOCK_LIMIT and not advanced:
            advanced = True
            st["blocked_streak"] = 0
            status(
                inst,
                "seed %d blocked %d rounds -> give up, advance" % (seed, BLOCK_LIMIT),
            )
        else:
            status(
                inst,
                "seed %d blocked round (rc=1 added=%d) streak=%d, stay"
                % (seed, added, st["blocked_streak"]),
            )
    elif added < LOW_THRESHOLD:
        st["low_streak"] = st.get("low_streak", 0) + 1
        if st["low_streak"] >= LOW_LIMIT and not advanced:
            advanced = True
            st["low_streak"] = 0
            status(
                inst,
                "seed %d barren (%d low rounds) -> advance shard queue"
                % (seed, LOW_LIMIT),
            )
    else:
        st["low_streak"] = 0
        st["blocked_streak"] = 0
    if advanced:
        st["queue_idx"] = idx + 1
        if st["queue_idx"] >= len(queue):
            st["done"] = True
            status(inst, "SHARD QUEUE DONE total=%d" % after)
    save_state(p["state"], st)
    lock_release(p["lock"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
