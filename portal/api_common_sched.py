# -*- coding: utf-8 -*-
"""api_common 四组拆分：sched 组（2026-10-31 WO-COMMON-SPLIT-20261031）。

自 api_common.py 纯移动而来，函数体/常量零改动；对外仍可从
api_common 导入（兼容 re-export）。
"""
from __future__ import annotations

import threading as _th
import os
import re
import subprocess
import threading
import time

from api_common import (
    QBT_DIR,
    STATION,
    _ERROR_LOG_MAX,
    log_exc,
)


DATA_SYNC_CMDS = {
    "tdx": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
            os.path.join(QBT_DIR, "tdx_sync.py")],      # 默认增量
    "tushare": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
                os.path.join(QBT_DIR, "tushare_sync.py"), "--incremental"],
    "kg": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", os.path.join(STATION, "quant_backtest",
                                 "start_kg_sync.ps1")],
    "catalog": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
                os.path.join(QBT_DIR, "build_catalog.py")],
    "alertpush": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
                  os.path.join(STATION, "news_collector", "alert_push.py")],
}

# ── 数据底座：时点校对 + 手动同步（数据仓页按钮后端）────────
# src → 启动命令；同一 src 单实例（重复点击拒绝），日志 C:\Temp
DATA_SYNC_JOBS = {}                                  # src -> {p, log}

_DATA_LOCK = threading.Lock()

_FRESH_BG_LOCK = threading.Lock()

_FRESH_BG_ERROR = None

# SCHED_S_* 调度器信息码（0x41300~）——非故障，按语义呈现
_SCHED_INFO_CODES = {
    267008: ("就绪", "#8b949e"),      # SCHED_S_TASK_READY
    267009: ("运行中", "#d29922"),    # SCHED_S_TASK_RUNNING
    267010: ("未排程", "#8b949e"),    # SCHED_S_TASK_NOT_SCHEDULED
    267011: ("尚未运行", "#8b949e"),  # SCHED_S_TASK_HAS_NOT_RUN（新任务首跑前常态）
    267012: ("不再排程", "#8b949e"),  # SCHED_S_TASK_NO_MORE_RUNS
    267013: ("未终止", "#d29922"),    # SCHED_S_TASK_NOT_TERMINATED
    267014: ("无触发器", "#f85149"),  # SCHED_S_TASK_NO_VALID_TRIGGERS（真问题）
}

def _log_rotate(fp):
    r"""C6b 日志轮转：fp 超 50MB → 先删旧 .1 → 当前改名为 .1（单代轮转，
    保留最近两段历史，防止 C:\Temp 被无限增长日志吃满磁盘）。
    每次写入前调用；stat/remove/rename 任一失败都静默放过，绝不阻断
    业务或日志写入本身。返回 True 表示发生了轮转。"""
    try:
        if os.path.getsize(fp) <= _ERROR_LOG_MAX:
            return False
        bak = fp + ".1"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(fp, bak)
        return True
    except OSError:
        return False

def data_freshness_bg(ttl_sec=1800):
    """K56-B1 驾驶舱取数策略：缓存热同步返回；过期有旧值先返旧值并后台
    刷新；从未算过（重启后 ~100s 预热窗）也只后台算并返回 None——
    任何请求都秒回，调用方对 None 显示「预热中」。"""
    import sys as _sy
    import time as _tt
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import datamart as _dm
    c = _dm._fresh_cache
    if c["data"] is not None and _tt.time() - c["ts"] < ttl_sec:
        return c["data"]
    if _FRESH_BG_LOCK.acquire(blocking=False):
        def refresh():
            global _FRESH_BG_ERROR
            try:
                _dm.freshness_report(ttl_sec=ttl_sec)
                _FRESH_BG_ERROR = None
            except Exception as exc:
                _FRESH_BG_ERROR = str(exc)
                log_exc("data_freshness_bg")
            finally:
                _FRESH_BG_LOCK.release()
        try:
            _th.Thread(target=refresh, daemon=True).start()
        except Exception:
            _FRESH_BG_LOCK.release()
            raise
    if c["data"] is None:
        return None
    return dict(c["data"], cache_stale=True, refreshing=_FRESH_BG_LOCK.locked(),
                refresh_error=_FRESH_BG_ERROR)

def data_sync_start(src):
    """手动触发同步（tdx/tushare/kg/catalog）。单实例 + 后台运行。"""
    if src not in DATA_SYNC_CMDS:
        return {"error": "bad src"}
    if src in {'tdx', 'tushare'}:
        # 新手动入口需先展示实际表/市场/范围；旧GET不能越过确认与持久化锁。
        return dict(started=False,pid=None,running=False,exit=None,tail='',run_id=None,
                    error='请在每日更新工作台预览范围并确认运行')
    with _DATA_LOCK:
        job = DATA_SYNC_JOBS.get(src)
        if job and job["p"].poll() is None:
            return {"error": "already running", "running": True}
        cmd = DATA_SYNC_CMDS[src]
        log = os.path.join(r"C:\Temp", "portal_data_%s.log" % src)
        try:
            with open(log, "ab") as lf:        # append：保留历史尾部
                lf.write(("\n===== %s manual sync =====\n"
                          % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
                p = subprocess.Popen(cmd, stdout=lf, stderr=lf,
                                     cwd=QBT_DIR,
                                     creationflags=getattr(
                                         subprocess, "CREATE_NO_WINDOW", 0))
            DATA_SYNC_JOBS[src] = {"p": p, "log": log}
            return {"started": True, "pid": p.pid}
        except Exception as ex:
            return {"error": str(ex)}

def _tp_row(table):
    """穿透展开行（隐藏，点 🔍 填充）：跨 colspan 由调用方包 <tr>。"""
    return ("<tr id='tpr-%s' style='display:none'>"
            "<td colspan='5' id='tpc-%s' style='padding:0 8px 8px 8px'></td>"
            "</tr>" % (table, table))

def _sched_rc_html(tstat):
    """计划任务状态串 → (显示文本, rc 判色 HTML)。

    267011 等信息码不是故障（新注册任务到点前 result 即 267011），
    误标红会把正常态当告警；last=11/30/1999 是 .NET DateTime.MinValue
    的本地化显示，规范为「尚未运行」。
    """
    if not tstat or tstat == "任务状态查询失败":
        return (tstat or "任务状态查询失败",
                "<b style='color:#8b949e'>状态未知</b>")
    if "not registered" in tstat:
        return tstat, "<b style='color:#8b949e'>未注册</b>"
    txt = tstat
    m = re.search(r"last=(\S+)(?:\s+(\S+))?", txt)
    _ym = re.search(r"(\d{4})", m.group(1)) if m else None
    if _ym and int(_ym.group(1)) < 2000:  # DateTime.MinValue 本地化假日期
        txt = txt[:m.start()] + "last=尚未运行" + txt[m.end():]
    m = re.search(r"result=(\d+)", txt)
    rc = int(m.group(1)) if m else None
    if rc == 0:
        return txt, "<b style='color:#3fb950'>正常</b>"
    if rc in _SCHED_INFO_CODES:
        label, color = _SCHED_INFO_CODES[rc]
        return txt, "<b style='color:%s'>%s</b>" % (color, label)
    return txt, ("<b style='color:#f85149'>rc=%s 异常</b>"
                 % (rc if rc is not None else "?"))

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW（run 同族随行）

def run(cmd, timeout=30):
    # K-STOCK-098 #3 防回归：10-09 曾有调用方算出 timeout=-711s 直接炸
    # TimeoutExpired（deadline-start 计算错，旧版已重构消失）。此处钳制
    # 任何调用方传入的非正 timeout，防同类回归再入。
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 30
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        log_exc("run() 子进程失败: %s" % (cmd[:2] if isinstance(cmd, list) else cmd))  # C2批1
        return ""
