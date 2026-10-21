# -*- coding: utf-8 -*-
"""开闸后服务侧全链验证（打桩不发飞书，可反复跑）。

与 comm_gate_check.py 的分工
--------------------------
- `comm_gate_check.py`：看**服务状态**（进程是否换血、端点是否 200、留痕有无）
- 本脚本：看**链路行为**（一条消息进来，路由/派单/回执是否符合设计）

安全
----
- `_send_card` 全程打桩，绝不真发飞书；
- 用独立验证身份 `ou_chain_verify_*`，跑完自动清理画像/inbox 痕迹
  （清理只针对该前缀，不碰真实客户数据）；
- 不写生产 route_log（重定向到临时目录）。

用法
----
    python tools/comm_chain_verify.py
    python tools/comm_chain_verify.py --clean-only   # 只清理上次残留
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "agent_platform"))

VERIFY_OID = "ou_chain_verify_001"
ARCHIVE = Path("C:/Temp/test_residue_20261021/chain_verify")

CASES = [
    ("个股咨询", "帮我看看 605100 现在能进吗", "smart:stock_consult", "dispatch"),
    ("盯票设置", "帮我盯一下 300750，跌破 180 就提醒我", "smart:watch_setup", "dispatch"),
    ("投诉", "昨天的问题到现在没人回，太失望了", "smart:complaint", "reply"),
    ("订阅查询", "我的订阅还剩几天", "smart:subscription_qa", "reply"),
    ("大盘判断", "为什么今天大盘跌", "smart:consult", "reply"),
    ("闲聊", "早上好", "unclear", "clarified"),
]


def clean():
    """清本验证身份的痕迹（画像行 + inbox 工单）。→ (n_interest, n_inbox)"""
    import sqlite3
    n1 = 0
    db = ROOT / "portal" / "work" / "comm_interest.sqlite3"
    if db.exists():
        try:
            c = sqlite3.connect(str(db), timeout=10)
            cur = c.execute("DELETE FROM customer_interest WHERE open_id LIKE ?",
                            (VERIFY_OID[:18] + "%",))
            n1 = cur.rowcount or 0
            c.commit()
            c.close()
        except sqlite3.Error:
            n1 = -1
    n2 = 0
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for f in glob.glob(str(ROOT / "agent_platform" / "bridge_inbox"
                           / "inbox" / "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if VERIFY_OID in ((d.get("payload") or {}).get("open_id") or ""):
            shutil.move(f, str(ARCHIVE / os.path.basename(f)))
            n2 += 1
    return n1, n2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-only", action="store_true")
    a = ap.parse_args()

    if a.clean_only:
        n1, n2 = clean()
        print("清理完成：画像 %s 行 / inbox %s 张" % (n1, n2))
        return 0

    import comm_flags
    import inbound_gateway as gw

    eff = comm_flags.status()["effective"]
    print("== 开关状态 ==")
    print(" ", eff)
    if not eff.get("intent"):
        print("\nWARN: 智能层未开（flags 未开或读失败），本验证将只会看到 unclear。")

    sent, ledgers = [], []
    gw._send_card = lambda card, oid: (sent.append((card, oid)) or (True, "stub"))
    gw._append_ledger = lambda path, row: ledgers.append(row)
    work = tempfile.mkdtemp(prefix="chain_verify_")
    gw.WORK_DIR = work
    gw.LOG_PATH = os.path.join(work, "gw.log")
    os.environ["LAOTIAN_OPEN_ID"] = VERIFY_OID
    # 路由留痕重定向到临时目录（不污染生产 route_log）
    os.environ["COMM_ROUTE_LOG"] = os.path.join(work, "route.jsonl")
    os.environ["COMM_MEMORY_DIR"] = os.path.join(work, "mem")

    print("\n== 链路行为（打桩不发飞书）==")
    ok = 0
    for i, (name, text, want_route, want_status) in enumerate(CASES, 1):
        out = gw.handle_inbound(VERIFY_OID, text, "CHAIN-V%02d" % i)
        route = str(out.get("route") or "-")
        status = str(out.get("status") or "-")
        hit = (route == want_route and status == want_status)
        ok += 1 if hit else 0
        print(" %d %-8s route=%-22s status=%-10s %s"
              % (i, name, route, status, "OK" if hit else
                 "MISMATCH(期望 %s/%s)" % (want_route, want_status)))

    print("\n== 副作用 ==")
    print(" 回卡 %d 张 / 台账 %d 条 / smart 分支 %d 条"
          % (len(sent), len(ledgers),
             sum(1 for r in ledgers if str(r.get("route", "")).startswith("smart:"))))

    n1, n2 = clean()
    print(" 已清理验证痕迹：画像 %s 行 / inbox %s 张" % (n1, n2))

    print("\nVERDICT: %d/%d 链路符合设计" % (ok, len(CASES)))
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
