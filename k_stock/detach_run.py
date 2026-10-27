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
  4. 无进展熔断闸（P2-D4）：账本「FAIL 不记」语义 + 5 分钟兜底 =
     若某股持续 FAIL（如 GLM 持续空输出），6h 窗口内每轮零重试烧钱。
     拉起前记录账本行数，run_poc 退出后比较；无进展计数 +1（持久化
     data/poc_run_fuse.json），有进展清零；连续 3 次无进展 → 注销
     兜底任务 + 写 aborted 记录，止损等人工介入。
"""

import ctypes
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))  # .../k_stock
RUN_CWD = os.path.dirname(REPO)  # 仓库根（-m k_stock 需要）
DATA = os.path.join(REPO, "data")
LOCK = os.path.join(DATA, "poc_run.lock")
FUSE_FILE = os.path.join(DATA, "poc_run_fuse.json")
WATCHLIST_N = 5
BOOTSTRAP_TASK = "ZeroQ-KStock-Resume-1020"
FUSE_MAX_NO_PROGRESS = 3


def _load_fuse() -> dict:
    try:
        with open(FUSE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_fuse(state: dict) -> None:
    """原子写（tmp + os.replace）：防并发/中断留下半写坏 JSON。"""
    tmp = FUSE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, FUSE_FILE)


def ledger_progress() -> int:
    """进度判据：真实（非 mock、可解析）账本行数。
    与 ledger_complete 同口径——mock 行/坏行不算进展。"""
    try:
        n = 0
        with open(_ledger_path(), "r", encoding="utf-8") as f:
            for x in f:
                x = x.strip()
                if not x:
                    continue
                try:
                    rec = json.loads(x)
                except ValueError:
                    continue
                if not rec.get("mock"):
                    n += 1
        return n
    except FileNotFoundError:
        return 0


def _today() -> str:
    """当日 YYYYMMDD（审计 T-O8：原硬编码 20261020 会使次日起读错账本
    误判"已完成"——按调用时日期动态取；调用点均为每次触发的新进程，
    加载期日期即当日，无需缓存。）"""
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d")


def _ledger_path() -> str:
    return os.path.join(DATA, f"poc_results_{_today()}.jsonl")


def _log_path() -> str:
    return os.path.join(DATA, f"poc_resume_{_today()}.log")


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
    """当日账本齐 5/5 → 注销兜底任务。防御纵深（审计 20261020-A12 消费端）：
    mock 记录（rec.mock 为真）不计入——万一上游过滤失效，这里兜底。"""
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as f:
            recs = []
            for x in f:
                x = x.strip()
                if not x:
                    continue
                try:
                    recs.append(json.loads(x))
                except ValueError:
                    continue
        real = [r for r in recs if not r.get("mock")]
        return len(real) >= WATCHLIST_N
    except FileNotFoundError:
        return False


def run_poc_alive() -> bool:
    """进程扫描守卫：是否已有 run_poc python 在跑（防双跑）。

    拉起器自身即退、锁文件随之 stale，故不靠锁而按命令行特征查进程。
    """
    try:
        # 统一走 CIM：无第三方依赖，父 powershell 自身是 python.exe 之外的进程
        out = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'run_poc' -and "
                "$_.ProcessId -ne $PID } | Measure-Object | "
                "Select-Object -ExpandProperty Count",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
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
            ["schtasks", "/Delete", "/TN", BOOTSTRAP_TASK, "/F"], capture_output=True
        )
    except Exception:
        pass


def main() -> int:
    if ledger_complete():
        dereg_bootstrap()
        return 0
    state = _load_fuse()
    if state.get("aborted"):
        # 熔断已触发（兜底任务注销失败残留）：确保注销，不再拉起烧钱。
        dereg_bootstrap()
        return 0
    if run_poc_alive():
        return 0  # 已有实例在跑
    if not acquire_lock():
        return 0
    before = ledger_progress()  # 本轮拉起前账本行数（熔断判基线）
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "-u",
        "-m",
        "k_stock.run_poc",
        "--auto",
        "--watchlist",
        str(WATCHLIST_N),
    ]
    flags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_BREAKAWAY_FROM_JOB
    )
    try:
        with open(_log_path(), "ab") as lf:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=lf,
                    stderr=lf,
                    cwd=RUN_CWD,
                    creationflags=flags,
                    close_fds=True,
                )
            except OSError:
                # 本 Job 不允许 breakaway 时退回普通 DETACHED（仍会被收割，但可试）
                proc = subprocess.Popen(
                    cmd,
                    stdout=lf,
                    stderr=lf,
                    cwd=RUN_CWD,
                    creationflags=subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
        # 持锁等待至 run_poc 退出（上限 6h），防 5 分钟重复触发竞态双跑。
        # 拉起器被任务 ExecutionTimeLimit 收割时锁变 stale，下轮自愈。
        deadline = time.time() + 6 * 3600
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(15)
        # 无进展熔断闸（P2-D4）：run_poc 退出后比账本行数。
        state = _load_fuse()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if ledger_progress() > before:
            state["no_progress"] = 0
            state["last_ts"] = now
            _save_fuse(state)
        else:
            state["no_progress"] = state.get("no_progress", 0) + 1
            state["last_ts"] = now
            if state["no_progress"] >= FUSE_MAX_NO_PROGRESS:
                state["aborted"] = True
                state["aborted_ts"] = now
                _save_fuse(state)
                dereg_bootstrap()
                print(
                    "[WARN] K-STOCK fuse tripped: %d consecutive rounds "
                    "with no ledger progress; bootstrap task '%s' "
                    "deregistered, resume aborted."
                    % (state["no_progress"], BOOTSTRAP_TASK),
                    file=sys.stderr,
                )
            else:
                _save_fuse(state)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
