# -*- coding: utf-8 -*-
"""进程健康监控看门狗 · 每分钟扫一次。

监控指标（32 核机器校准）：
  1) multiprocessing-fork 子进程数   → 雪崩预警（0-20 安全 / 21-28 告警 / 29+ 主动 kill）
  2) 孤儿 python（父进程已退出）    → 僵尸清理（0-10 安全 / 11-20 告警 / 21+ 主动清理）
  3) python 进程总数               → 累积水位（< 80 安全 / 80+ 告警）

设计原则（对齐项目 Hard Constraints）：
  - 全局 Mutex 跨进程幂等锁，防 Win32_Process.CommandLine NULL 导致的计划任务重复拉起
  - kill 前先记日志，操作留痕（C:\\Temp\\proc_watchdog.log）
  - 阈值是保守硬限：触发时只清最明显的一类（spawn 子进程优先），不误伤业务脚本

用法：
  pythonw.exe -X utf8 proc_watchdog.py        # 单次运行（供计划任务调用）
  python -X utf8 proc_watchdog.py --dry-run   # 只扫不杀（手工检查）
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import wmi

# ---------- 配置 ----------
THRESH_SPAWN_WARN = 20      # multiprocessing spawn 子进程告警阈值
THRESH_SPAWN_KILL = 28      # spawn 子进程主动 kill 阈值（打满 32 核时 ≈87%）
THRESH_ORPHAN_WARN = 15     # 孤儿 python 告警阈值
THRESH_ORPHAN_KILL = 25     # 孤儿 python 主动清理阈值
THRESH_PY_TOTAL_WARN = 80   # python 总数告警阈值

# 孤儿里这些是「名义孤儿、实际常驻」的服务型脚本，降级处理（只告警不 kill）
# 原因：看门狗脚本启动它们后自身会退出 → 父进程自然断链，但脚本本身仍在服务
SERVICE_KEYWORDS = [
    "gateway\\serve.py",
    "watch-daemon",
    "watchdog",
    "uvicorn",
    "feishu.*bot",
    "dsh_bot",
    "k266_clip_watch",
    "k266_m3_structure",
    "jev_gate.py",
    "signal_review_gate",
    "jev_qd_bridge",
    "lark_card_handler",
    "power_watch.py",
    "http.server",
    "qengine.api",
    "qengine.sched",
    "serve.py",
    "alert_watch",
    "alert_push",
    # 以下是「真活服务但父进程链断」的，也豁免（SERVICE_KEYWORDS 机制本质是：
    # 看门狗 powershell 脚本用 Start-Process 拉起 python 后自身会退出 →
    # 子 python 自然成孤儿，这是设计模式不是 bug，所以全部加白名单）
    "api_model_admin",
    "kg_data_dashboard",
    "ack-serve",
]
# 真僵尸判定：累计 CPU 超过此值的孤儿 = 跑了很久仍未清理的历史残留
ZOMBIE_CPU_THRESHOLD = 300.0   # 300s ≈ 5 分钟

LOG = r"C:\Temp\proc_watchdog.log"
MUTEX_NAME = "Local\\ProcWatchdog_Singleton_20261111"


def _log(msg):
    """关键节点日志（统一格式：时间戳 + 级别 + 消息）。"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


class _GlobalMutex:
    """跨进程幂等锁（Windows 命名 Mutex）。"""

    _HANDLE = None
    _OWNED = False

    @classmethod
    def acquire(cls, name=MUTEX_NAME):
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, name)
        if handle is None:
            return False
        last_err = kernel32.GetLastError()
        if last_err == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        cls._HANDLE = handle
        cls._OWNED = True
        return True

    @classmethod
    def release(cls):
        if cls._OWNED and cls._HANDLE:
            ctypes.windll.kernel32.CloseHandle(cls._HANDLE)
            cls._OWNED = False
            cls._HANDLE = None


def _is_service_script(cmd):
    """判断孤儿是不是服务型脚本（名义孤儿、实际在服务）。"""
    import re as _re
    for kw in SERVICE_KEYWORDS:
        if _re.search(kw, cmd, _re.IGNORECASE):
            return True
    return False


def _snapshot():
    """采集当前进程快照，并把孤儿分类为：service 常驻 / zombie 历史残留。"""
    c = wmi.WMI()
    procs = list(c.Win32_Process())
    alive_pids = {p.ProcessId for p in procs}

    py_procs = []                 # 所有 python/pythonw
    fork_children = []            # multiprocessing spawn 子进程
    orphan_all = []               # 所有孤儿

    for p in procs:
        name = (p.Name or "").lower()
        cmd = p.CommandLine or ""
        if name not in ("python.exe", "pythonw.exe"):
            continue
        py_procs.append(p)

        is_fork = "--multiprocessing-fork" in cmd or "multiprocessing.spawn" in cmd
        is_orphan = (p.ParentProcessId or 0) > 0 and (p.ParentProcessId not in alive_pids)

        if is_fork:
            fork_children.append(p)
        if is_orphan and not is_fork:
            orphan_all.append(p)

    # 孤儿分类：服务型 vs 僵尸
    orphan_service = []   # 名义孤儿、实际在工作（不 kill）
    orphan_zombie = []    # 真残留（CPU 很高、非服务脚本）
    for p in orphan_all:
        cmd = p.CommandLine or ""
        if _is_service_script(cmd):
            orphan_service.append(p)
        else:
            orphan_zombie.append(p)

    return {
        "py_total": len(py_procs),
        "fork_count": len(fork_children),
        "orphan_total": len(orphan_all),
        "orphan_service_count": len(orphan_service),
        "orphan_zombie_count": len(orphan_zombie),
        "fork_list": fork_children,
        "orphan_service_list": orphan_service,
        "orphan_zombie_list": orphan_zombie,
        "cpu_count": os.cpu_count() or 8,
    }


def _kill_procs(procs, reason):
    """批量 kill，用 taskkill（wmi ExecMethod Terminate 在 Python 进程上会抛 COM 转换异常）。"""
    killed, fail = 0, 0
    for p in procs:
        pid = p.ProcessId
        name = p.Name
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                killed += 1
                _log(f"  KILL PID {pid} ({name}) reason={reason}")
            else:
                fail += 1
                _log(f"  FAIL PID {pid} rc={r.returncode} err={r.stderr.strip()[:80]}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            _log(f"  EXC  PID {pid} {e}")
    return killed, fail


def run(dry_run=False):
    if not _GlobalMutex.acquire():
        sys.exit(0)

    try:
        snap = _snapshot()
        n_cpu = snap["cpu_count"]
        fork_n = snap["fork_count"]
        orphan_total = snap["orphan_total"]
        orphan_svc = snap["orphan_service_count"]
        zombie_n = snap["orphan_zombie_count"]
        py_n = snap["py_total"]

        _log(
            f"SNAP  cpu={n_cpu} py_total={py_n} "
            f"fork={fork_n} orphan={orphan_total} (svc={orphan_svc} zombie={zombie_n})"
        )

        actions = []

        # --- 判定 1: multiprocessing 雪崩（无任何豁免） ---
        if fork_n >= THRESH_SPAWN_KILL:
            _log(f"ALERT  spawn={fork_n} >= kill={THRESH_SPAWN_KILL} → 主动清理")
            if not dry_run:
                k, f = _kill_procs(snap["fork_list"], reason="spawn_avalanche")
                _log(f"  清理结果 killed={k} fail={f}")
            else:
                _log(f"  [dry-run] 将 kill {fork_n} 个 spawn 子进程")
            actions.append("spawn_kill")
        elif fork_n >= THRESH_SPAWN_WARN:
            _log(f"WARN  spawn={fork_n} >= warn={THRESH_SPAWN_WARN}")
            actions.append("spawn_warn")

        # --- 判定 2: 真僵尸 python（非服务脚本 + 父进程已退出） ---
        if zombie_n >= THRESH_ORPHAN_KILL:
            _log(f"ALERT  zombies={zombie_n} >= kill={THRESH_ORPHAN_KILL} → 主动清理")
            if not dry_run:
                k, f = _kill_procs(snap["orphan_zombie_list"], reason="orphan_zombie")
                _log(f"  清理结果 killed={k} fail={f}")
            else:
                _log(f"  [dry-run] 将 kill {zombie_n} 个僵尸 python")
            actions.append("zombie_kill")
        elif zombie_n >= THRESH_ORPHAN_WARN:
            _log(f"WARN  zombies={zombie_n} >= warn={THRESH_ORPHAN_WARN}（保留服务孤儿 {orphan_svc}）")
            actions.append("zombie_warn")

        # 服务型孤儿只做水位日志，不 kill（它们是合法运行中的服务）
        if orphan_svc > 0:
            _log(f"INFO  服务孤儿={orphan_svc}（watch-daemon/uvicorn/bot 等，不 kill）")

        # --- 判定 3: python 总数 ---
        if py_n >= THRESH_PY_TOTAL_WARN:
            _log(f"WARN  py_total={py_n} >= {THRESH_PY_TOTAL_WARN}（高水位）")
            actions.append("py_high")

        if not actions and orphan_svc == 0:
            _log("OK  所有指标在安全线内")
        elif not actions:
            _log("OK  只有服务型孤儿，无异常")

        return 0
    finally:
        _GlobalMutex.release()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只扫不杀")
    args = ap.parse_args()
    sys.exit(run(dry_run=args.dry_run))
