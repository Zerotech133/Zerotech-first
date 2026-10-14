# -*- coding: utf-8 -*-
"""开发指挥台 · 飞书卡型（K-STOCK-114 M2）——离屏遥控三卡。

  进度摘要卡 summary : 今日四类计数 + 最近 3 条动态 + 下钻按钮（整点/手动）
  合并回执卡 merge   : 工单号 + 合并动态（merge_done 触发）
  卡壳告警卡 blocked : 工单 + 原因（blocked 触发，红色头）

  用法:
    python tools/notify_devops.py summary
    python tools/notify_devops.py merge --task K-STOCK-114 --detail "三守卫全绿"
    python tools/notify_devops.py blocked --task K-STOCK-114b --detail "凭证缺失"
  通道: 复用 portal/feishu_card_v2.send_card（同 send_feishu 凭证链，实测 SEND_OK）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "portal"))
from feishu_card_v2 import send_card  # noqa: E402  复用凭证链与 OpenAPI 通道

sys.path.insert(0, HERE)
from events import today_summary  # noqa: E402

PORTAL_BASE = "http://127.0.0.1:8734"  # K-STOCK-113 立宪唯一入口；K-STOCK-140 判例：8080 随 052 收敛死链禁用（PC 端飞书直达；手机未入 Tailscale 属已知判例死路）

_KD = {"task_start": ("🚀 建单", "blue"), "commit": ("📝 提交", "grey"),
       "blocked": ("⛔ 卡壳", "red"), "merge_done": ("✅ 合并", "green"),
       "guard_fail": ("🛑 守卫", "orange")}


def _recent_md(n: int = 3) -> str:
    s = today_summary()
    rows = []
    for t, st in sorted(s["tasks"].items(),
                        key=lambda kv: kv[1]["last"], reverse=True)[:n]:
        kd = _KD.get(st["last_kind"], (st["last_kind"], "grey"))[0]
        rows.append("- **%s** %s · %s" % (t, kd, (st.get("last_detail") or "")[:60]))
    return "\n".join(rows) if rows else "- 今日暂无开发事件"


def card_summary() -> dict:
    s = today_summary()
    bk = s["by_kind"]
    agents = "、".join("%s×%d" % (a, c) for a, c in
                       sorted(s["agents"].items(), key=lambda kv: -kv[1])) or "—"
    md = ("**建单 %d · 提交 %d · 合并 %d · 卡壳 %d**\n活跃 Agent：%s\n---\n%s"
          % (bk.get("task_start", 0), bk.get("commit", 0),
             bk.get("merge_done", 0), bk.get("blocked", 0),
             agents, _recent_md()))
    return {
        "schema": "2.0", "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text",
                             "content": "🏭 开发指挥台 · 今日进度"},
                   "template": "blue"},
        "body": {"elements": [
            {"tag": "markdown", "content": md},
            {"tag": "hr"},
            {"tag": "button", "type": "primary", "size": "medium",
             "text": {"tag": "plain_text", "content": "下钻指挥台"},
             "behaviors": [{"type": "open_url",
                            "default_url": "%s/devops" % PORTAL_BASE}]},
            {"tag": "markdown", "content":
                "<font color='grey'>零界开发事件层 · 自动汇总 · %s</font>"
                % s["day"]},
        ]}}


def card_event(kind: str, task: str, detail: str) -> dict:
    if kind == "merge":
        title, tmpl = "✅ 合并回执 · %s" % task, "green"
        md = "**%s 已合并 master**\n%s" % (task, detail or "三守卫全绿")
    elif kind == "new":
        title, tmpl = "📋 新工单 · %s" % task, "blue"
        md = ("**%s 已建单**\n%s\n---\n领单纪律：DEVTOOL_AGENT/DEVTOOL_IDE/"
              "DEVTOOL_MODEL 三环境变量带齐再动工" % (task, detail or ""))
    else:
        title, tmpl = "⛔ 开发卡壳 · %s" % task, "red"
        md = "**%s 卡壳**\n%s\n---\n请拍板：继续 / 换人 / 放弃" % (task, detail or "原因未填")
    return {
        "schema": "2.0", "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title},
                   "template": tmpl},
        "body": {"elements": [
            {"tag": "markdown", "content": md},
            {"tag": "hr"},
            {"tag": "button", "type": "primary", "size": "medium",
             "text": {"tag": "plain_text", "content": "看工单动态"},
             "behaviors": [{"type": "open_url",
                            "default_url": "%s/devops" % PORTAL_BASE}]},
        ]}}


def main() -> None:
    ap = argparse.ArgumentParser(description="开发指挥台飞书卡")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary", help="今日进度摘要卡")
    p1 = sub.add_parser("merge", help="合并回执卡")
    p1.add_argument("--task", required=True)
    p1.add_argument("--detail", default="")
    pn = sub.add_parser("new", help="建单通知卡（蓝）")
    pn.add_argument("--task", required=True)
    pn.add_argument("--detail", default="")
    p2 = sub.add_parser("blocked", help="卡壳告警卡")
    p2.add_argument("--task", required=True)
    p2.add_argument("--detail", default="")
    a = ap.parse_args()
    card = (card_summary() if a.cmd == "summary"
            else card_event(a.cmd, a.task, a.detail))
    ok, msg = send_card(card)
    print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))


if __name__ == "__main__":
    main()
