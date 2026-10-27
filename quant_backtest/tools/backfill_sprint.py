# -*- coding: utf-8 -*-
"""雪球分类存量追平 · 错峰批量 runner（backfill sprint）。

背景（2026-10-18 数据线第四轮）：xq_status 17,338 行存量，xq_status_classified
仅 ~12% 覆盖；HOURLY automation 每轮 ~1500 追平太慢。本工具在错峰窗口
（缺省 13:30-17:30，避开午高峰与 18:00-19:30 生产链/日报窗口）内分片批量
跑 xq_classify.run(500)，带单实例锁、写库让路语义、进度落盘。

复用 xq_classify（import 级，不复制逻辑）：
  - run(limit)：每次自建连接、NOT IN 幂等取未标行，适合分片循环续跑；
  - ensure_table：幂等建派生表。

让路语义关键点：xq_classify.log 与 automation 共写 xq_classify_status.log，
故不能简单按「mtime 60 秒内」判定（自己刚写完必然触发）。采用基线对比：
  - 首片前（本进程尚未写过）：日志 60 秒内有写入 → 视为外部写者活跃 → 让路；
  - 之后每片结束记录基线 mtime；下片前 mtime != 基线 → 外部有新写入 → 让路；
    探测前先暂停 OBSERVE_SEC 秒拉宽窗口，否则我们分片期间的外部写入会被
    基线吸收（探测窗只有毫秒级），持续并行的写者永远探测不到；
  - 让路 = 等 60s 后把基线对齐当前再试（写者停了就能续），连续 3 次让路退出。

退出码：0 正常结束/无事可做；2 窗口外；3 锁冲突；4 连续让路退出；1 参数/异常。

用法：
  python tools/backfill_sprint.py                       # 缺省 2000 条，13:30-17:30
  python tools/backfill_sprint.py --limit 4000          # 单批上限
  python tools/backfill_sprint.py --window "20:00-23:00"
  python tools/backfill_sprint.py --limit 1 --force     # 小样验证（绕过窗口）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # 项目根，import xq_classify
import xq_classify  # noqa: E402

# 本任务自有产物根（lock/sprint log），对齐 2026-10-18 拍板「定时任务产物
# 统一落 <任务名> 目录」，与 wrapper 日志同处 automation_runs/XueqiuBackfillSprint。
# 【红线】STATUS_LOG（让路探测对象）必须留在 xq_classify 的共享状态日志原地
# automation_runs/xq_classify/——它是 automation 与本 runner 的共享写日志，
# 挪走探测就永久失效；两个常量相互独立，禁止合并/联动。
RUN_DIR = Path(r"C:\aios\automation_runs\XueqiuBackfillSprint")
LOCK_PATH = RUN_DIR / "backfill_sprint.lock"
SPRINT_LOG = RUN_DIR / "backfill_sprint.log"
STATUS_LOG = xq_classify.STATUS_LOG  # 共享状态日志（让路探测对象，原地不动）
DB_PATH = xq_classify.DB_PATH

DEFAULT_WINDOW = "13:30-17:30"
SHARD_SIZE = 500  # 每片行数（对齐 xq_classify 每日轮判例）
YIELD_SLEEP_SEC = 60  # 让路等待
YIELD_MAX = 3  # 连续让路上限
FRESH_WRITE_SEC = 60  # 启动时日志「新鲜写入」阈值
OBSERVE_SEC = 5  # 片间隙观察窗：外部写者活跃探测（每日循环走查补丁）
BUSY_TIMEOUT = 30

RC_OK, RC_ERROR, RC_OUT_OF_WINDOW, RC_LOCKED, RC_YIELDED_OUT = 0, 1, 2, 3, 4


# ---------------------------------------------------------------- logging


def slog(msg: str) -> None:
    """ASCII 安全进度日志（判例：计划任务产物落盘排障）。"""
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        line = "%s | %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        safe = line.encode("ascii", "replace").decode("ascii")
        with SPRINT_LOG.open("a", encoding="utf-8") as f:
            f.write(safe + "\n")
        print(safe)
    except Exception:
        pass


# ---------------------------------------------------------------- window


def parse_window(spec: str) -> tuple:
    """'HH:MM-HH:MM' -> (start_min, end_min)。支持跨夜（start > end）。

    非法格式抛 ValueError（main 捕获转退出码 2 并注明）。
    """
    try:
        a, b = spec.split("-", 1)
        h1, m1 = a.strip().split(":")
        h2, m2 = b.strip().split(":")
        start = int(h1) * 60 + int(m1)
        end = int(h2) * 60 + int(m2)
    except Exception:
        raise ValueError("bad window spec: %r (want HH:MM-HH:MM)" % spec)
    if not (0 <= start < 1440 and 0 <= end < 1440):
        raise ValueError("window out of range: %r" % spec)
    return start, end


def in_window(ts: float, win: tuple) -> bool:
    """当前时刻是否在窗口内（跨夜窗口按环形区间判定）。"""
    start, end = win
    cur = datetime.fromtimestamp(ts)
    cur_min = cur.hour * 60 + cur.minute
    if start <= end:
        return start <= cur_min < end
    return cur_min >= start or cur_min < end  # 跨夜：22:00-02:00


# ---------------------------------------------------------------- lock


def is_pid_alive(pid: int) -> bool:
    """跨平台 pid 探活（Windows 用 OpenProcess，不能用 os.kill——
    Windows 上 os.kill 非 0 信号等价 TerminateProcess，会误杀）。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                k32.CloseHandle(h)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_lock_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except Exception:
        return -1


def acquire_lock(path: Path = LOCK_PATH, alive=None) -> bool:
    """O_EXCL 原子建锁 + pid 写入。锁被占：持锁进程活着 → False（让路）；
    死进程陈锁 → 接管（删旧重建，重试仍失败视为竞争 → False）；
    锁文件存在但 pid 字段不可读 → 不可判定残留，fail-closed 拒绝接管。

    alive 缺省调用时解析 is_pid_alive（模块级 mock 可替换，测试判例）。"""
    pid = os.getpid()
    if pid <= 0:
        # fail-closed（2026-10-18 走查判例）：拿不到真 pid 宁可不启动，
        # 绝不写哨兵假值——假 pid 会让活性校验形同虚设。
        slog("lock refused: invalid pid %d, fail-closed" % pid)
        return False
    if alive is None:
        alive = is_pid_alive
    for _attempt in range(2):
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(pid).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            holder = read_lock_pid(path)
            if holder == -1:
                # pid 字段缺失/不可解析（持有者写锁中途崩溃残留，或建锁-
                # 写 pid 间隙的瞬时空读）：不可判定残留，不无条件抢——
                # 放弃本轮（rc=3），下轮窗口自愈。
                slog("stale lock: pid unreadable, undetermined, not taking over")
                return False
            if alive(holder):
                slog("lock busy: pid=%d alive, yield" % holder)
                return False
            slog("stale lock: pid=%d dead, take over" % holder)
            try:
                path.unlink()
            except OSError:
                return False
    return False


def lock_is_ours(path: Path = LOCK_PATH) -> bool:
    """分片间自检：锁还在且写的是本进程 pid（被接管/误删即失守）。"""
    return read_lock_pid(path) == os.getpid()


def release_lock(path: Path = LOCK_PATH) -> None:
    if lock_is_ours(path):
        try:
            path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- yield


def status_log_mtime() -> float | None:
    try:
        return STATUS_LOG.stat().st_mtime
    except OSError:
        return None


def should_yield(mtime: float | None, baseline: float | None, now: float) -> bool:
    """让路判定。baseline=None 表示本进程尚未写过（启动期）：
    日志在 FRESH_WRITE_SEC 内有写入即视为外部写者活跃。
    之后：mtime != 基线 → 基线之后有新写入（外部写者）。"""
    if mtime is None:
        return False  # 无共享日志，无从探测
    if baseline is None:
        return (now - mtime) < FRESH_WRITE_SEC
    return mtime != baseline


# ---------------------------------------------------------------- db status


def fetch_counts() -> dict:
    """--status 语义只读查数：source/classified/ok/degraded/pending。"""
    con = sqlite3.connect(
        "file:%s?mode=ro" % DB_PATH.as_posix(), uri=True, timeout=BUSY_TIMEOUT
    )
    try:
        src = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
        row = con.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(CASE WHEN parse_ok=1 THEN 1 ELSE 0 END),0)
               FROM xq_status_classified"""
        ).fetchone()
        classified, ok = int(row[0]), int(row[1])
        return {
            "source": src,
            "classified": classified,
            "ok": ok,
            "degraded": classified - ok,
            "pending": src - classified,
        }
    finally:
        con.close()


def log_counts(tag: str) -> dict:
    try:
        c = fetch_counts()
    except sqlite3.Error as e:
        slog("%s counts-unavailable: %s" % (tag, str(e)[:100]))
        return {}
    slog(
        "%s source=%d classified=%d ok=%d degraded=%d pending=%d"
        % (tag, c["source"], c["classified"], c["ok"], c["degraded"], c["pending"])
    )
    return c


# ---------------------------------------------------------------- main loop


def sprint(limit: int, win: tuple | None, force: bool) -> int:
    """批量主循环。win=None 表示 --force 绕过窗口（仅测试用）。"""
    if not acquire_lock(LOCK_PATH):
        return RC_LOCKED
    baseline: float | None = None
    done = 0
    yielded = 0
    try:
        log_counts("sprint start")
        while done < limit:
            now = time.time()
            if win is not None and not in_window(now, win):
                slog("window ended mid-batch, stop done=%d" % done)
                break
            if not lock_is_ours(LOCK_PATH):
                slog("lock lost (taken over or removed), abort")
                return RC_LOCKED
            mtime = status_log_mtime()
            if baseline is not None:
                # 每日循环走查补丁（2026-10-18）：基线对比的探测窗口原本只有
                # 毫秒级（基线采集→下一轮探测），外部写者在我们分片期间的
                # 写入会被基线吸收，持续并行的 automation 将永远探测不到。
                # 探测前暂停 OBSERVE_SEC 秒拉宽窗口：基线之后有新写入即外部
                # 写者活跃（本进程在窗口内不写状态日志，不会自检出）；
                # 让路 sleep 后的基线对齐同理不会再吞掉后续活跃写者。
                time.sleep(OBSERVE_SEC)
                mtime = status_log_mtime()
            if should_yield(mtime, baseline, now):
                yielded += 1
                slog(
                    "yield %d/%d: external writer active on status log"
                    % (yielded, YIELD_MAX)
                )
                if yielded >= YIELD_MAX:
                    slog("yielded out rc=4: writer persists, retry later")
                    return RC_YIELDED_OUT
                time.sleep(YIELD_SLEEP_SEC)
                baseline = status_log_mtime()  # 让路后对齐基线再观察
                continue
            yielded = 0
            shard = min(SHARD_SIZE, limit - done)
            slog("shard start done=%d shard=%d" % (done, shard))
            rc = xq_classify.run(shard)
            done += shard
            baseline = status_log_mtime()  # 记录「自己写完」的基线
            slog("shard end done=%d rc=%d" % (done, rc))
            if rc != 0:
                slog("shard rc=%d (consecutive hard-fail), stop" % rc)
                break
            counts = log_counts("shard progress")
            if counts and counts["pending"] == 0:
                slog("all caught up, stop done=%d" % done)
                break
        slog("sprint end done=%d yielded=%d" % (done, yielded))
        log_counts("sprint final")
        return RC_OK
    finally:
        release_lock(LOCK_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球分类存量追平错峰批量 runner")
    ap.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="单批总上限（分片 run(500) 循环），缺省 2000",
    )
    ap.add_argument(
        "--window",
        default=DEFAULT_WINDOW,
        help="错峰窗口 HH:MM-HH:MM（支持跨夜），缺省 %s" % DEFAULT_WINDOW,
    )
    ap.add_argument("--force", action="store_true", help="绕过窗口检查（小样/测试用）")
    a = ap.parse_args()
    if a.limit <= 0:
        slog("bad --limit %d" % a.limit)
        return RC_ERROR
    try:
        win = parse_window(a.window)
    except ValueError as e:
        slog("arg error: %s" % e)
        return RC_OUT_OF_WINDOW
    if a.force:
        slog("window bypassed by --force")
        return sprint(a.limit, None, True)
    now = time.time()
    if not in_window(now, win):
        slog("out of window %s, exit rc=2" % a.window)
        return RC_OUT_OF_WINDOW
    return sprint(a.limit, win, False)


if __name__ == "__main__":
    sys.exit(main())
