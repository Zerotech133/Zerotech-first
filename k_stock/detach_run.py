# -*- coding: utf-8 -*-
"""K-STOCK 补跑脱离拉起器（Job Object 逃逸）。

背景（2026-10-20 三次实录）：
  计划任务实例的 Job Object 是「实例结束收割子孙」的元凶
  （同 ollama_watchdog.py v2 教训：未 breakaway 的组件存活仅 1-5 分钟，
  LastTaskResult=0x40010004）。本拉起器以 CREATE_BREAKAWAY_FROM_JOB
  拉起 run_poc，使其脱离任务实例 Job，实例回收不再殃及。

职责：
  1. 进程扫描守卫（run_poc 存活防双跑）+ 单实例锁兜底。
  2. 当日账本齐 (5/5) 时自动注销兜底任务并退出。
  3. breakaway Popen 幂等续跑 run_poc（--auto --watchlist 5）。
"""
import ctypes
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))        # .../k_stock
RUN_CWD = os.path.dirname(REPO)                           # 仓库根（-m k_stock 需要）
DATA = os.path.join(REPO, "data")
LEDGER = os.path.join(DATA, "poc_results_20261020.jsonl")
LOG = os.path.join(DATA, "poc_resume_20261020.log")
LOCK = os.path.join(DATA, "poc_run.lock")
WATCHLIST_N = 5
BOOTSTRAP_TASK = "ZeroQ-KStock-Resume-1020"


def pid_alive(pid: int) -> bool:
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            k32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def ledger_complete() -> bool:
    try:
        with open(LEDGER, "r", encoding="utf-8") as f:
            lines = [json.loads(x) for x in f if x.strip()]
        return len(lines) >= WATCHLIST_N
    except FileNotFoundError:
        return False


def run_poc_alive() -> bool:
    """进程扫描守卫：是否已有 run_poc python 在跑（防双跑）。

    拉起器自身即退、锁文件随之 stale，故不靠锁而按命令行特征查进程。
    """
    try:
        # 统一走 CIM：无第三方依赖，父 powershell 自身是 python.exe 之外的进程
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'run_poc' -and "
             "$_.ProcessId -ne $PID } | Measure-Object | "
             "Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        n = int((out.stdout or "0").strip() or 0)
        return n > 0
    except Exception:
        return False  # 查询失败宁可多查，幂等续跑兜底


def acquire_lock() -> bool:
    """单实例锁（保留作 PID 存活判据）；stale 锁自愈后重取，最多 3 次。"""
    for _ in range(3):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            try:
                old = int(open(LOCK).read().strip() or "0")
                if pid_alive(old):
                    return False  # 真实实例在跑，直接退出
            except (ValueError, OSError):
                pass
            try:
                os.remove(LOCK)
            except OSError:
                pass
            time.sleep(1)
    return False


def dereg_bootstrap():
    try:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", BOOTSTRAP_TASK, "/F"],
            capture_output=True)
    except Exception:
        pass


def main() -> int:
    if ledger_complete():
        dereg_bootstrap()
        return 0
    if run_poc_alive():
        return 0  # 已有实例在跑
    if not acquire_lock():
        return 0
    cmd = [sys.executable, "-X", "utf8", "-u", "-m", "k_stock.run_poc",
           "--auto", "--watchlist", str(WATCHLIST_N)]
    flags = (subprocess.DETACHED_PROCESS
             | subprocess.CREATE_NEW_PROCESS_GROUP
             | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    try:
        with open(LOG, "ab") as lf:
            try:
                proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=RUN_CWD,
                                        creationflags=flags, close_fds=True)
            except OSError:
                # 本 Job 不允许 breakaway 时退回普通 DETACHED（仍会被收割，但可试）
                proc = subprocess.Popen(
                    cmd, stdout=lf, stderr=lf, cwd=RUN_CWD,
                    creationflags=subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        # 持锁等待至 run_poc 退出（上限 6h），防 5 分钟重复触发竞态双跑。
        # 拉起器被任务 ExecutionTimeLimit 收割时锁变 stale，下轮自愈。
        deadline = time.time() + 6 * 3600
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(15)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
