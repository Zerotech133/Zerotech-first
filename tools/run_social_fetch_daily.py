# -*- coding: utf-8 -*-
"""JEV 社交快照每日盘后前向采集触发器（2026-11-10）

设计要点（与 v2 P2.2 修正口径一致）：
- **只跑真实交易日**：跳过周六日；且要求当日 daily 分片已落盘（否则视为非交易日 /
  数据未就绪，跳过，不写盘）。
- **不是历史回补**：社交接口（雪球 / 股吧）只返回「查询时刻」实时榜，--day 只控制
  选票，所以**只能前向累积**——跑今天拿今天的快照。历史回补物理不可行
  （xq_hot/guba_rank/guba_posts 都不接收日期参数，见 v2 P2.2 修正说明）。
- 用 C:\aios 宪法别名（ASCII）避免中文路径坑；统一 Python311（带 requests/pandas）。
- 同进程复用 sys.executable 调 jev_social_fetch，保证解释器一致。

用法：
    python tools/run_social_fetch_daily.py                 # 盘后定时任务入口（自动 today）
    python tools/run_social_fetch_daily.py --day 20261109  # 手动补某日（仍要求该日 daily 存在）
    python tools/run_social_fetch_daily.py --dry            # 只列清单，不请求不写盘
    python tools/run_social_fetch_daily.py --force          # 绕过交易日门控（仅手工用）
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)                                  # C:\aios
QUANT = os.path.join(STATION, "quant_backtest")
DAILY_DIR = os.path.join(QUANT, "data", "tushare", "daily")
SOCIAL_FETCH = os.path.join(HERE, "jev_social_fetch.py")
LOG_PATH = os.path.join(STATION, "tools", "social_fetch_daily.log")


def log(msg):
    line = "[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_trading_day(day):
    """交易日判定：周一~周五 且 当日 daily 分片已落盘。"""
    d = dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))
    if d.weekday() >= 5:                       # 5=Sat, 6=Sun
        return False
    return os.path.exists(os.path.join(DAILY_DIR, "d_%s.parquet" % day))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="", help="YYYYMMDD，缺省=今天")
    ap.add_argument("--dry", action="store_true", help="只列清单，不请求不写盘")
    ap.add_argument("--force", action="store_true", help="绕过交易日门控（仅手工用）")
    a = ap.parse_args()

    day = a.day or dt.date.today().strftime("%Y%m%d")
    log("=== run_social_fetch_daily day=%s force=%s dry=%s ==="
        % (day, a.force, a.dry))
    if a.force:
        daily_ok = os.path.exists(os.path.join(DAILY_DIR, "d_%s.parquet" % day))
        if not daily_ok:
            log("WARN force 绕过门控，但当日 daily 未落盘 → 选票走盘中降级"
                "(xtcloud 实时分钟)，快照口径与盘后不一致")
    elif not is_trading_day(day):
        log("%s 非交易日或 daily 未就绪，跳过（不写盘）" % day)
        return 0

    cmd = [sys.executable, SOCIAL_FETCH, "--day", day]
    if a.dry:
        cmd.append("--dry")
    log("调用: %s" % " ".join(cmd))

    # 长跑输出必须实时落文件：逐行 tee 到日志，判进度看进程 + 产物 mtime + 日志 tail。
    # 强制子进程 UTF-8 输出，避免 Windows 控制台 GBK 与日志编码打架。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace",
                            env=env, bufsize=1)
    try:
        for line in proc.stdout:
            log("  " + line.rstrip())
        rc = proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        log("INTERRUPTED: 已终止子进程")
        return 130
    log("jev_social_fetch 退出码=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
