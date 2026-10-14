# -*- coding: utf-8 -*-
"""W9 批处理队列：qpzm 一年批次窗回填 + cross 一年细窗网格（2026-10-15 备妥）

用途（一次性，10-18 09:30 自动化触发，亦可手动）：
    python -X utf8 tools/w9_backfill_queue.py           # 全队列
    python -X utf8 tools/w9_backfill_queue.py --only qpzm   # 只跑 qpzm
    python -X utf8 tools/w9_backfill_queue.py --only cross  # 只跑 cross

设计要点：
- 长跑输出全部落文件（本脚本自身日志 + 子进程各自日志），不走管道（U4 判例）；
- 子进程 python -u，看门狗超时 4h → taskkill /F /T（杀树）；
- 幂等：产物已存在则跳过对应步骤（--force 可重跑）；
- 两年整窗产物 reports/cross_grid_result.json 受保护：先备份为
  cross_grid_result_2y_<d0>_<d1>.json，一年窗产物单独命名落盘后恢复主
  文件名（portfolio_sim 等读者不受影响）；
- cross_grid_backtest.py 的 HERE 指向 tools/research（脚本挪位后数据
  路径断链），队列以"根目录临时副本"方式运行，零侵入原脚本；
- Windows 计划任务/自动化均可调，退出码=失败步骤数。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # quant_backtest/
PY = sys.executable
LOG_DIR = os.path.join(HERE, "logs")
TS = time.strftime("%Y%m%d_%H%M%S")
MAIN_LOG = os.path.join(LOG_DIR, "w9_backfill_queue_%s.log" % TS)
TIMEOUT_S = 4 * 3600

QPZM_DAYS = 240
CROSS_D0, CROSS_D1 = "20251014", "20261014"
GRID_SCRIPT = os.path.join(HERE, "tools", "research", "cross_grid_backtest.py")
GRID_COPY = os.path.join(HERE, "cross_grid_backtest.py")  # 根目录临时副本
GRID_RESULT = os.path.join(HERE, "reports", "cross_grid_result.json")
GRID_RESULT_1Y = os.path.join(HERE, "reports", "cross_grid_result_1y_%s_%s.json" % (CROSS_D0, CROSS_D1))

_logf = None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    _logf.write(line + "\n")
    _logf.flush()


def kill_tree(pid):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True)


def run_step(name, args, log_fp, cwd=HERE):
    """跑子进程：-u、输出落文件、超时杀树。返回 rc。"""
    cmd = [PY, "-u"] + args
    log("STEP %s 启动: %s (cwd=%s)" % (name, " ".join(cmd), cwd))
    t0 = time.time()
    with open(log_fp, "wb") as out:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
        try:
            rc = p.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            log("STEP %s 超时 %ds，已 taskkill /T（详见 %s）" % (name, TIMEOUT_S, log_fp))
            return 124
    dt = time.time() - t0
    log("STEP %s 完成 rc=%d 耗时 %.0fs（日志 %s）" % (name, rc, dt, log_fp))
    return rc


def step_qpzm(force=False):
    log_fp = os.path.join(LOG_DIR, "w9_qpzm_backfill240_%s.log" % TS)
    log("=== qpzm --backfill %d（一年批次窗）===" % QPZM_DAYS)
    return run_step("qpzm-backfill", ["-X", "utf8", "qpzm_daily_batch.py",
                                      "--backfill", str(QPZM_DAYS)], log_fp)


def step_cross(force=False):
    log_fp = os.path.join(LOG_DIR, "w9_cross_grid_1y_%s.log" % TS)
    if os.path.exists(GRID_RESULT_1Y) and not force:
        log("cross 1y 产物已存在，跳过（--force 重跑）: %s" % GRID_RESULT_1Y)
        return 0
    # 1) 备份两年整窗产物（幂等）
    bak = GRID_RESULT.replace(".json", "_2y_20240902_20260918.json")
    if os.path.exists(GRID_RESULT) and not os.path.exists(bak):
        shutil.copy2(GRID_RESULT, bak)
        log("已备份两年窗产物 → %s" % bak)
    # 2) 根目录临时副本（修复 HERE 断链，零侵入原脚本）
    shutil.copy2(GRID_SCRIPT, GRID_COPY)
    try:
        rc = run_step("cross-grid-1y",
                      ["-X", "utf8", "cross_grid_backtest.py",
                       "--d0", CROSS_D0, "--d1", CROSS_D1],
                      log_fp)
        if rc != 0:
            return rc
        # 3) 一年窗产物改名落位，恢复两年窗主文件
        if os.path.exists(GRID_RESULT):
            shutil.move(GRID_RESULT, GRID_RESULT_1Y)
            log("一年窗产物 → %s" % GRID_RESULT_1Y)
        if os.path.exists(bak):
            shutil.copy2(bak, GRID_RESULT)
            log("两年窗主文件已恢复（读者不受影响）")
        return 0
    finally:
        if os.path.exists(GRID_COPY):
            os.remove(GRID_COPY)
            log("临时副本已清理: %s" % GRID_COPY)


def main():
    global _logf
    os.makedirs(LOG_DIR, exist_ok=True)
    _logf = open(MAIN_LOG, "a", encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["qpzm", "cross"], default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    log("=== W9 批处理队列启动 pid=%d only=%s force=%s ===" % (os.getpid(), a.only, a.force))
    fails = 0
    if a.only in (None, "qpzm"):
        fails += 1 if step_qpzm(a.force) != 0 else 0
    if a.only in (None, "cross"):
        fails += 1 if step_cross(a.force) != 0 else 0
    log("=== 队列结束 fails=%d ===" % fails)
    _logf.close()
    sys.exit(fails)


if __name__ == "__main__":
    main()
