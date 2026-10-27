# -*- coding: utf-8 -*-
"""雪球深挖自动轮 wrapper：定时任务入口。

职责：
  1. 读状态文件 C:/aios/automation_runs/xq_backfill/backfill_state.json——done=true 直接退出（深挖已补完，
     日常增量走 18:00 XueqiuCrawl 生产轮，本 wrapper 不再发请求）；
  2. 跑一轮 xq_collect_1000.py --target 500（输出独立文件，不碰生产日志）；
  3. 解析本轮净增：>=50 视为空间存活（重置低产计数）；<50 累计低产计数；
     连续 2 轮低产 → 写 done=true，停深挖；
  4. 状态日志 ASCII（计划任务排障判例），轮次明细 UTF-8 独立文件。

用法：python xq_backfill_auto.py   （无参数，由计划任务每 30 分钟触发）
rc：0=本轮正常结束（含 done 跳过）；1=异常（由计划任务告警链处理）。
"""

import json
import os
import subprocess
import sys
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_ROOT = r"C:\aios\automation_runs"  # 定时任务产物统一根（2026-10-18 老田拍板：产物/日志/临时文件不再散落）
STATE_PATH = os.path.join(RUNS_ROOT, "xq_backfill", "backfill_state.json")
LOCK_PATH = os.path.join(RUNS_ROOT, "xq_backfill", "backfill.lock")
LOCK_STALE_SEC = 45 * 60  # 锁超过 45 分钟视为残留（正常轮 10-20 分钟），自愈抢断
LOG_DIR = os.path.join(RUNS_ROOT, "xq_backfill")
STATUS_LOG = os.path.join(LOG_DIR, "xq_backfill_status.log")
PY = r"C:\Program Files\Python311\python.exe"
TARGET = 500
LOW_THRESHOLD = 50  # 单轮净增低于此 = 见顶信号
LOW_LIMIT = 2  # 连续 N 轮低产 = 缺口补完


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
        return {"done": False, "consecutive_low": 0, "rounds": []}


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


def lock_acquire() -> bool:
    """非阻塞互斥锁：存在且新鲜则拒绝并发轮；残留旧锁自愈抢断。"""
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
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xq_backfill_auto.py", exc)


def main() -> int:
    os.makedirs(LOG_DIR, exist_ok=True)
    st = load_state()
    if st.get("done"):
        status(
            "skip done=true (backfill finished, daily 18:00 job covers)"
            " total=%d" % db_count()
        )
        return 0
    if not lock_acquire():
        status("skip lock held (another round active or fresh)")
        return 0
    base = db_count()
    stamp = datetime.now().strftime("%m%d_%H%M")
    out_path = os.path.join(LOG_DIR, "xq_backfill_round_%s.out" % stamp)
    err_path = os.path.join(LOG_DIR, "xq_backfill_round_%s.err" % stamp)
    status("round start base=%d target=%d" % (base, TARGET))
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
                "--target",
                str(TARGET),
            ],
            stdout=fo,
            stderr=fe,
            cwd=HERE,
        )
    after = db_count()
    added = after - base
    status("round end rc=%d added=%d total=%d" % (rc, added, after))
    if rc not in (0, 1):
        status("round abnormal rc=%d (crash), no state change" % rc)
        lock_release()
        return 1
    st.setdefault("rounds", []).append(
        {"at": datetime.now().strftime("%m-%d %H:%M"), "added": added}
    )
    st["rounds"] = st["rounds"][-30:]
    if added < LOW_THRESHOLD:
        st["consecutive_low"] = st.get("consecutive_low", 0) + 1
        status(
            "low yield round (%d<%d) consecutive=%d/%d"
            % (added, LOW_THRESHOLD, st["consecutive_low"], LOW_LIMIT)
        )
    else:
        st["consecutive_low"] = 0
    if st["consecutive_low"] >= LOW_LIMIT:
        st["done"] = True
        status("BACKFILL DONE (cursor exhausted after %d low rounds)" % LOW_LIMIT)
    save_state(st)
    lock_release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
