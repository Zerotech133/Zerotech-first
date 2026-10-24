# -*- coding: utf-8 -*-
"""night_batch.py — 夜间长跑批处理循环器（本地模型两条积压队列）。

为什么要有它
------------
2026-10-25 盘点：本地推理积压两条大队列——
  · news.db   20,016 条无 JEV 判定（近 7 天积压）
  · 雪球库    15,236 条未分类（33,182 - 17,946）
两条都用同一个本地 Ollama（9b），且服务端 NUM_PARALLEL=1 是**严格串行**的，
并行跑只会互相排队，所以这里串行轮转：一轮 JEV、一轮雪球，交替推进。

断点怎么来
----------
两个脚本本身就是增量的，不需要额外游标：
  · jev_backfill.py 取 `WHERE jev IS NULL` 的批，判完 UPDATE 写回 → 自动前进
  · xq_classify.py  取未分类行，写完 classified 表 → 自动前进
所以任何时刻被中断，重跑即可接着来，不会重复计费也不会漏。

安全边界
--------
  · 只调这两个既有脚本，不直接写库
  · 到点（默认 07:00，赶在晨报前）自动收工；也可放停止旗文件提前收
  · 连续两轮两条队列都没推进 → 判定卡死并退出，不做无效空转
  · 每轮落一行日志（含两条队列剩余量），收工写汇总

用法：
    python -B tools/night_batch.py                    # 跑到 07:00
    python -B tools/night_batch.py --until 06:30
    python -B tools/night_batch.py --dry-run          # 只看队列与计划，不执行
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
NEWS_DB = ROOT / "news_collector" / "news.db"
XQ_DB = ROOT / "quant_backtest" / "data" / "xueqiu" / "xueqiu_info.db"
JEV = ROOT / "news_collector" / "jev_backfill.py"
XQ = ROOT / "quant_backtest" / "xq_classify.py"
DEFAULT_LOG = r"C:\Temp\night_batch.log"
DEFAULT_STOP = r"C:\Temp\night_batch.stop"


def _count(db: Path, sql: str) -> int:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True, timeout=10)
        try:
            return int(con.execute(sql).fetchone()[0])
        finally:
            con.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return -1


def backlog() -> tuple[int, int]:
    """(待判 JEV 条数, 待分类雪球条数)。读不到返回 -1。"""
    jev = _count(NEWS_DB, "SELECT COUNT(*) FROM news WHERE (jev IS NULL OR jev='')")
    try:
        total = _count(XQ_DB, "SELECT COUNT(*) FROM xq_status")
        done = _count(XQ_DB, "SELECT COUNT(*) FROM xq_status_classified")
    except Exception:  # noqa: BLE001
        total = done = -1
    xq = (total - done) if (total >= 0 and done >= 0) else -1
    return jev, xq


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


def _cutoff(until: str) -> datetime:
    """今天的 HH:MM；若已过则该时刻视为明天。"""
    hh, mm = (int(x) for x in until.split(":"))
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT(%ss)" % int(timeout)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    tail = [ln for ln in (out + "\n" + err).splitlines() if ln.strip()]
    return proc.returncode, (tail[-1].strip() if tail else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="夜间长跑：JEV 回填 + 雪球分类 串行轮转")
    ap.add_argument("--until", default="07:00", help="收工时刻 HH:MM（默认 07:00）")
    ap.add_argument("--batch-jev", type=int, default=600)
    ap.add_argument("--batch-xq", type=int, default=600)
    ap.add_argument("--deadline", type=int, default=1500, help="每轮 JEV 硬超时秒")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--stop-file", default=DEFAULT_STOP)
    ap.add_argument("--max-rounds", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    log = Logger(args.log)
    cutoff = _cutoff(args.until)
    jev0, xq0 = backlog()
    log("START 收工时间=%s | 起始积压 JEV=%s 雪球=%s | 批=%d/%d"
        % (cutoff.strftime("%m-%d %H:%M"), jev0, xq0, args.batch_jev, args.batch_xq))
    if args.dry_run:
        log("DRY-RUN 不执行；预计 JEV %.1f 小时、雪球 %.1f 小时（按 2s/1s 估）"
            % (max(jev0, 0) * 2 / 3600.0, max(xq0, 0) * 1 / 3600.0))
        return 0

    stop = Path(args.stop_file)
    rounds = 0
    stall = 0
    last_jev, last_xq = jev0, xq0
    while datetime.now() < cutoff and rounds < args.max_rounds:
        if stop.exists():
            log("STOP 检测到停止旗 %s" % stop)
            break
        rounds += 1
        jev, xq = backlog()
        log("ROUND %d | 剩余 JEV=%s 雪球=%s" % (rounds, jev, xq))
        if jev == 0 and xq == 0:
            log("DONE 两条队列都空了")
            break

        if jev and jev > 0:
            t0 = time.time()
            rc, tail = _run([PY, "-X", "utf8", "-B", str(JEV),
                             "--batch", str(args.batch_jev),
                             "--deadline", str(args.deadline),
                             "--alert-threshold", "0"],
                            ROOT / "news_collector", args.deadline + 120)
            log("  JEV rc=%s %.0fs %s" % (rc, time.time() - t0, tail[:120]))

        if datetime.now() >= cutoff:
            break

        if xq and xq > 0:
            t0 = time.time()
            rc, tail = _run([PY, "-X", "utf8", "-B", str(XQ),
                             "--limit", str(args.batch_xq),
                             "--order", "asc", "--worker-id", "nightA",
                             "--segment", "B"],
                            ROOT / "quant_backtest", 1800)
            log("  XQ  rc=%s %.0fs %s" % (rc, time.time() - t0, tail[:120]))

        jev2, xq2 = backlog()
        moved = ((last_jev - jev2) if (last_jev >= 0 and jev2 >= 0) else 0) + \
                ((last_xq - xq2) if (last_xq >= 0 and xq2 >= 0) else 0)
        if moved <= 0:
            stall += 1
            if stall >= 2:
                log("STALL 连续 %d 轮无推进（JEV=%s 雪球=%s），退出避免空转"
                    % (stall, jev2, xq2))
                break
        else:
            stall = 0
        last_jev, last_xq = jev2, xq2

    jev1, xq1 = backlog()
    log("END 轮数=%d | 剩余 JEV=%s 雪球=%s | 本轮完成 JEV=%s 雪球=%s"
        % (rounds, jev1, xq1,
           (jev0 - jev1) if (jev0 >= 0 and jev1 >= 0) else "?",
           (xq0 - xq1) if (xq0 >= 0 and xq1 >= 0) else "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
