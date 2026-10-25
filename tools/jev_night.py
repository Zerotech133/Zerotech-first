# -*- coding: utf-8 -*-
"""jev_night.py —— 夜间 JEV 独占跑批（22:00 暂停 M3 → 07:00 恢复）。

为什么要它
----------
2026-10-25 实测：news.db 有约 2 万条待 JEV 判定。云端 typesafe 通道需要海外代理
（127.0.0.1:7890），本机没有代理进程 → 全部回落本地 9b，与 M3 结构化抢同一张
GPU（Ollama NUM_PARALLEL=1 强制串行）。实测 JEV 仅 191 条/小时 → 要 4.3 天。

独占后（M3 停、无锁竞争）预期 ~845 条/小时，一夜 9 小时约 7.6k，3 夜清完。

怎么保证 M3 夜里真的不回来
--------------------------
`k266_services.ps1` 每 5 分钟跑一次，扫到新文档（输出含 "OK d"）就会异步拉起 M3。
所以本脚本先写暂停旗 `C:\\Temp\\k266_m3.pause`，并在 k266_services.ps1 里加了闸：
旗在 → 本轮不拉 M3。收工时删旗，下一 tick 自然恢复。

纪律
----
  · 只停命令行含 `k266_m3_structure` 的 python 进程，绝不按名字一把杀
  · 全程落日志 C:\\Temp\\jev_night.log
  · 支持停止旗 C:\\Temp\\jev_night.stop 与 --dry-run

用法：
    python -B tools/jev_night.py --start 22:00 --until 07:00
    python -B tools/jev_night.py --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JEV = ROOT / "news_collector" / "jev_backfill.py"
NEWS_DB = ROOT / "news_collector" / "news.db"
PAUSE_FILE = Path(r"C:\Temp\k266_m3.pause")
DEFAULT_LOG = r"C:\Temp\jev_night.log"
DEFAULT_STOP = r"C:\Temp\jev_night.stop"
PY = sys.executable

PS_LIST_M3 = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -match 'k266_m3_structure' } | "
    "Select-Object -ExpandProperty ProcessId"
)


class Logger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _hm(text: str) -> tuple[int, int]:
    hh, mm = text.split(":")
    return int(hh), int(mm)


def window(start: str, until: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """返回 (开始, 结束)。若当前已过开始时刻，则从当前时刻起算（不回溯等待）。"""
    now = now or datetime.now()
    sh, sm = _hm(start)
    uh, um = _hm(until)
    begin = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if begin <= now:
        begin = now
    end = begin.replace(hour=uh, minute=um, second=0, microsecond=0)
    if end <= begin:
        end += timedelta(days=1)
    return begin, end


def backlog() -> int:
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=10)
        try:
            return int(con.execute(
                "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0])
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return -1


def m3_pids() -> list[int]:
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", PS_LIST_M3],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return out


def pause_m3(log: Logger, dry_run: bool = False) -> int:
    """写暂停旗并停掉 M3，返回被停的进程数。"""
    if dry_run:
        pids = m3_pids()
        log("DRY-RUN 将写暂停旗 %s 并停止 M3 进程 %s" % (PAUSE_FILE, pids or "（无）"))
        return len(pids)
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(
        "paused by tools/jev_night.py at %s\n" % datetime.now().isoformat(),
        encoding="utf-8")
    killed = 0
    for pid in m3_pids():
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Stop-Process -Id %d -Force" % pid],
                           capture_output=True, timeout=60)
            killed += 1
            log("  已停 M3 pid=%d" % pid)
        except (OSError, subprocess.TimeoutExpired):
            log("  停 M3 pid=%d 失败" % pid)
    log("M3 暂停完成（停 %d 个），暂停旗已写" % killed)
    return killed


def resume_m3(log: Logger) -> None:
    try:
        PAUSE_FILE.unlink()
        log("暂停旗已删除，M3 可被 k266_services 下一 tick 恢复")
    except FileNotFoundError:
        log("暂停旗本就不存在")
    except OSError as exc:
        log("删暂停旗失败：%s" % exc)


def run_jev(batch: int, deadline: int, log: Logger) -> tuple[int, str]:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [PY, "-X", "utf8", "-B", str(JEV), "--batch", str(batch),
             "--deadline", str(deadline), "--alert-threshold", "0"],
            cwd=str(ROOT / "news_collector"), capture_output=True,
            timeout=deadline + 300)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT(%ss)" % (deadline + 300)
    tail_lines = ((proc.stdout or b"") + (proc.stderr or b"")).decode(
        "utf-8", "replace").strip().splitlines()
    tail = tail_lines[-1] if tail_lines else ""
    log("  JEV rc=%d %.0fs %s" % (proc.returncode, time.time() - t0, tail[:140]))
    return proc.returncode, tail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="夜间 JEV 独占跑批（暂停 M3）")
    ap.add_argument("--start", default="22:00")
    ap.add_argument("--until", default="07:00")
    ap.add_argument("--batch", type=int, default=600)
    ap.add_argument("--deadline", type=int, default=1500)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--stop-file", default=DEFAULT_STOP)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    log = Logger(args.log)
    begin, end = window(args.start, args.until)
    left0 = backlog()
    log("START 计划窗口 %s → %s | 待判 JEV=%s | 批=%d 截止=%ds"
        % (begin.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"),
           left0, args.batch, args.deadline))

    if args.dry_run:
        log("DRY-RUN 不执行。将：① 等到开始时刻 ②写暂停旗+停 M3 ③循环 JEV 到结束 ④删旗恢复")
        return 0

    stop = Path(args.stop_file)

    # ① 等到开始时刻（分片睡眠，可被停止旗打断）
    while datetime.now() < begin:
        if stop.exists():
            log("STOP 等待期检测到停止旗，退出")
            resume_m3(log)
            return 0
        remain = (begin - datetime.now()).total_seconds()
        time.sleep(min(300.0, max(1.0, remain)))
        if datetime.now() < begin:
            log("等待中... 距开始 %.0f 分钟" % ((begin - datetime.now()).total_seconds() / 60))

    # ② 暂停 M3
    pause_m3(log)

    # ③ 循环 JEV 直到结束/清空/停止旗
    rounds = 0
    try:
        while datetime.now() < end:
            if stop.exists():
                log("STOP 检测到停止旗")
                break
            left = backlog()
            if left == 0:
                log("DONE 队列已清空")
                break
            rounds += 1
            log("ROUND %d | 剩余 JEV=%s" % (rounds, left))
            rc, _ = run_jev(args.batch, args.deadline, log)
            if rc != 0:
                time.sleep(60)
    finally:
        left1 = backlog()
        resume_m3(log)
        log("END 轮数=%d | 剩余 JEV=%s | 本轮完成=%s"
            % (rounds, left1, (left0 - left1) if (left0 >= 0 and left1 >= 0) else "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
