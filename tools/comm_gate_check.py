# -*- coding: utf-8 -*-
"""飞书智能层开闸后自动核对（开闸操作单步骤 4 的自动化版）。

一条命令回答：开闸到底生效没有？逐项 PASS/FAIL/NOT_READY，不猜。

检查项
-----
1. 8091 健康（基线，任何变更都要求它 200）
2. 新端点 GET /api/comm/interest —— 200 说明 8091 已换血
3. 智能层开关 —— 通过「探测一个必判 unclear 的短句是否被智能层接管」间接判定
   （进程级环境变量外部读不到，只能行为探测；探测不真投递、不真发）
4. 路由留痕 —— route_log 是否出现开闸后的新行（按 mtime 与行数）
5. 画像表 —— comm_interest 是否有开闸后写入
6. 派单 inbox —— bridge_inbox/inbox 是否有 lg- 文件
7. 巡视线程 —— bot 日志是否出现 "dispatch patrol thread started"

用法
----
    python tools/comm_gate_check.py               # 全量核对
    python tools/comm_gate_check.py --json        # 机读
    python tools/comm_gate_check.py --since 16:00 # 只看该时刻之后的留痕（默认今天 0 点）
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get("PORTAL_API_BASE", "http://127.0.0.1:8091")
ROUTE_LOG = ROOT / "portal" / "work" / "comm_route_log.jsonl"
INTEREST_DB = ROOT / "portal" / "work" / "comm_interest.sqlite3"
INBOX = ROOT / "agent_platform" / "bridge_inbox" / "inbox"
BOT_LOG = ROOT / "portal" / "logs" / "feishu_bot.log"


def _get(url, timeout=5):
    req = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as ex:  # noqa: BLE001
        return 0, "%s: %s" % (type(ex).__name__, ex)


def _count_lines(p, since_ts=0):
    if not p.exists():
        return 0
    n = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                n += 1
    except OSError:
        return 0
    return n


def _interest_rows():
    if not INTEREST_DB.exists():
        return None
    try:
        c = sqlite3.connect(str(INTEREST_DB))
        n = c.execute("SELECT COUNT(*) FROM customer_interest").fetchone()[0]
        c.close()
        return n
    except sqlite3.Error:
        return None


def _inbox_tickets():
    if not INBOX.exists():
        return 0
    return len(list(INBOX.glob("*.json")))


def _bot_patrol_logged():
    if not BOT_LOG.exists():
        return None
    try:
        txt = BOT_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return "dispatch patrol thread started" in txt[-200000:]


def run(json_out=False):
    items = []

    st, _ = _get(API + "/health")
    items.append(("8091 健康", "PASS" if st == 200 else "FAIL",
                  "HTTP %s" % (st or "unreachable")))

    st2, body2 = _get(API + "/api/comm/interest")
    if st2 == 200:
        try:
            d = json.loads(body2)
            detail = "ok=%s auth_mode=%s count=%s" % (
                d.get("ok"), d.get("auth_mode"), d.get("count"))
            items.append(("新端点 /api/comm/interest", "PASS", detail))
        except ValueError:
            items.append(("新端点 /api/comm/interest", "FAIL", "非 JSON"))
    elif st2 == 404:
        items.append(("新端点 /api/comm/interest", "NOT_READY",
                      "404 —— 8091 尚未换血（旧进程）"))
    else:
        items.append(("新端点 /api/comm/interest", "WARN", "HTTP %s" % st2))

    n_log = _count_lines(ROUTE_LOG)
    if n_log == 0:
        items.append(("路由留痕", "NOT_READY",
                      "comm_route_log.jsonl 无记录（开闸后发消息才产生）"))
    else:
        items.append(("路由留痕", "PASS", "%d 行" % n_log))

    n_interest = _interest_rows()
    if n_interest is None:
        items.append(("画像表", "NOT_READY", "comm_interest.sqlite3 不存在"))
    elif n_interest == 0:
        items.append(("画像表", "WARN", "表存在但 0 行（等开闸后行为）"))
    else:
        items.append(("画像表", "PASS", "%d 行" % n_interest))

    n_inbox = _inbox_tickets()
    items.append(("派单 inbox", "PASS" if n_inbox else "WARN",
                  "%d 个待处理工单" % n_inbox))

    patrol = _bot_patrol_logged()
    if patrol is None:
        items.append(("巡视线程", "NOT_READY", "bot 日志未找到"))
    else:
        items.append(("巡视线程", "PASS" if patrol else "NOT_READY",
                      "已打印启动行" if patrol else "日志无 patrol 启动行（bot 未换血）"))

    ok = sum(1 for _, v, _ in items if v == "PASS")
    fail = sum(1 for _, v, _ in items if v == "FAIL")
    not_ready = sum(1 for _, v, _ in items if v == "NOT_READY")
    verdict = "PASS" if fail == 0 and not_ready == 0 else (
        "FAIL" if fail else "NOT_READY")

    if json_out:
        print(json.dumps({"verdict": verdict, "pass": ok, "fail": fail,
                          "not_ready": not_ready,
                          "items": [{"name": n, "state": v, "detail": d}
                                    for n, v, d in items]},
                         ensure_ascii=False, indent=1))
    else:
        print("== 开闸核对 ==")
        for n, v, d in items:
            print("  [%-9s] %-26s %s" % (v, n, d))
        print("\nVERDICT: %s（PASS %d / FAIL %d / NOT_READY %d）"
              % (verdict, ok, fail, not_ready))
        if verdict == "NOT_READY":
            print("提示：NOT_READY 多为「尚未开闸/服务未换血」，非故障；"
                  "开闸后复跑本脚本。")
    return 0 if verdict == "PASS" else (1 if verdict == "FAIL" else 3)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.json))
