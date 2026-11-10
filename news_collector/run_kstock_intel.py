# -*- coding: utf-8 -*-
"""K-STOCK 策略中台持仓/出票标的 · 开源情报驱动采集（每日自动）。

每次运行：
  1) 动态读取 k_stock/data/paper.db 的 positions(持仓) + 近 N 天有效 BUY 订单(pending/filled)
     → 独特代码，即"策略中台更新的数据"；
  2) 分离 A股(.SZ/.SH/.BJ) / 港股(.HK) / 美股(gb_)；
  3) A股(去后缀) 按市场分流调用现有采集器（均为 requests 无头友好，计划任务可跑）：
       .SZ -> irm_collector.py         深交所互动易（官方问答维度）
       .SH -> sse_collector.py         上证 e互动（官方问答维度）
       全部A股 -> taoguba_collector.py  淘股吧（游资/短线情绪维度）
     - 雪球 xq 已由 XqTimelineDaily(07:00 全队列) 覆盖，不重复；
     - 股吧 guba_pw.py 为 Playwright headed（需桌面会话），自动模式暂跳过，手动可补；
  4) 港股/美股标的超出当前采集器覆盖，仅登记入 watchlist，不自动抓取；
  5) 刷新 news_collector/kstock_watchlist.json (asof) + 写日志。

接入现有每日更新计划：由 Windows 计划任务 KStockIntelDaily 每日 07:30 触发，
串接 XqTimelineDaily(07:00) → [本任务 07:30] → SentimentSnapshot(08:00)。
"""
import os
import sys
import json
import time
import subprocess
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
KSTOCK_DB = os.path.join(REPO, "k_stock", "data", "paper.db")
PY = r"C:\Program Files\Python311\python.exe"
WATCHLIST = os.path.join(HERE, "kstock_watchlist.json")
LOG = os.path.join(HERE, "logs", "kstock_intel_daily.log")
SSE_UIDMAP = os.path.join(REPO, "data", "sse", "uid_map.json")
RECENT_DAYS = 14  # 近 N 天内的有效 BUY 订单视为"近期出的票"

os.makedirs(os.path.dirname(LOG), exist_ok=True)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def build_watchlist():
    con = sqlite3.connect(KSTOCK_DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT DISTINCT stock FROM positions")
    held = sorted(r["stock"] for r in cur.fetchall())
    since = (datetime.now() - timedelta(days=RECENT_DAYS)).strftime("%Y%m%d")
    cur.execute(
        "SELECT DISTINCT stock FROM orders "
        "WHERE side='BUY' AND status IN ('pending','filled') AND plan_date>=?",
        (since,),
    )
    recent = sorted(r["stock"] for r in cur.fetchall())
    con.close()
    allset = sorted(set(held) | set(recent))
    ash = [c for c in allset if c.endswith((".SZ", ".SH", ".BJ"))]
    hk = [c for c in allset if c.endswith(".HK")]
    us = [c for c in allset if c.startswith("gb_")]
    return held, recent, allset, ash, hk, us


def run_collector(script, codes, extra=None):
    if not codes:
        return 0
    cmd = [PY, os.path.join(HERE, script), ",".join(codes)]
    if extra:
        cmd += extra
    log("→ %s  %s" % (script, ",".join(codes)))
    try:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=900)
        tail = (r.stdout or "").strip().splitlines()[-4:]
        for ln in tail:
            log("   | %s" % ln)
        if r.returncode != 0:
            log("   !! rc=%s  %s" % (r.returncode, (r.stderr or "")[-160:]))
        return r.returncode
    except Exception as e:  # noqa: BLE001
        log("   !! exc %s" % e)
        return -1


def main():
    if not sys.stdout.isatty():
        sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stderr = sys.stdout
    log("===== KStockIntel start %s =====" % time.strftime("%Y-%m-%d"))
    if not os.path.exists(KSTOCK_DB):
        log("!! k_stock 数据库缺失: %s" % KSTOCK_DB)
        return
    held, recent, allset, ash, hk, us = build_watchlist()
    new_recent = sorted(set(recent) - set(held))
    log("持仓 %d | 近期有效BUY %d (新增 %d) | 监控合计 %d (A股 %d / 港股 %d / 美股 %d)"
        % (len(held), len(recent), len(new_recent), len(allset), len(ash), len(hk), len(us)))

    sz = [c[:-3] for c in ash if c.endswith(".SZ")]
    sh = [c[:-3] for c in ash if c.endswith(".SH")]
    bj = [c[:-3] for c in ash if c.endswith(".BJ")]
    ash6 = sz + sh + bj

    # sse 首次需建 uid 映射（一次性，约 70 页遍历）
    if sh and not os.path.exists(SSE_UIDMAP):
        log("sse uid 映射缺失，先构建（一次性）…")
        try:
            subprocess.run([PY, os.path.join(HERE, "sse_collector.py"), "--uidmap"],
                          cwd=HERE, capture_output=True, text=True, timeout=900)
        except Exception as e:  # noqa: BLE001
            log("   !! uidmap 构建异常 %s（sse 本次可能跳过）" % e)

    run_collector("irm_collector.py", sz)
    run_collector("sse_collector.py", sh)
    # 淘股吧个股通常仅 1 页（最热帖），翻第 2 页会 404；限定 --pages 1
    run_collector("taoguba_collector.py", ash6, extra=["--pages", "1"])

    out = {
        "source": "k_stock/data/paper.db",
        "asof": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recent_days": RECENT_DAYS,
        "held": held,
        "recent_buy_valid": recent,
        "new_recent": new_recent,
        "watchlist": allset,
        "breakdown": {"ashare": ash, "hk": hk, "us": us},
        "collected_ashare": ash6,
        "coverage_note": (
            "自动覆盖 A股 irm(深交所互动易)/sse(上证e互动)/taoguba(淘股吧); "
            "股吧 guba_pw 为 Playwright headed 需桌面会话，暂跳过自动; "
            "雪球 xq 由 XqTimelineDaily(07:00 全队列) 覆盖; "
            "港股/美股无对应采集器，仅登记不抓取"
        ),
    }
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("watchlist 已刷新: %s (%d 标的)" % (WATCHLIST, len(allset)))
    log("===== KStockIntel done =====")


if __name__ == "__main__":
    main()
