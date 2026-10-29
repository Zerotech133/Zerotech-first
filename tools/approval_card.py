# -*- coding: utf-8 -*-
"""S2 审批卡构建（D2-D3，2026-10-30 WO-SUPERVISOR）。

输出 Card 2.0 JSON：标题/证据/风险 + 批准/驳回/转人工按钮。
按钮回调值走 approval_control 的 tag 协议；**不发送、不接线**。
"""
from __future__ import annotations

import argparse
import json
import sys


def build_card(task, summary, level="S2", evidence_hash="", evidence_url=""):
    task = str(task or "").strip()
    summary = str(summary or "").strip()
    body = [
        {"tag": "markdown",
         "content": "**工单**：%s\n\n**摘要**：%s\n\n**证据**：`%s`%s"
                    % (task, summary or "—", evidence_hash or "—",
                       ("\n[查看证据](%s)" % evidence_url) if evidence_url else "")},
        {"tag": "hr"},
        {"tag": "markdown",
         "content": "**级别**：%s｜批准=写入 approval 记录；驳回=退工单；转人工=升级 A0" % level},
    ]
    buttons = [
        {"tag": "button",
         "text": {"tag": "plain_text", "content": "批准"},
         "type": "primary_filled", "width": "fill",
         "behaviors": [{"type": "callback",
                        "value": {"tag": "approval:approve", "task": task,
                                  "level": level, "evidence_hash": evidence_hash}}]},
        {"tag": "button",
         "text": {"tag": "plain_text", "content": "驳回"},
         "type": "default",
         "behaviors": [{"type": "callback",
                        "value": {"tag": "approval:reject", "task": task,
                                  "level": level, "evidence_hash": evidence_hash}}]},
        {"tag": "button",
         "text": {"tag": "plain_text", "content": "转人工"},
         "type": "default",
         "behaviors": [{"type": "callback",
                        "value": {"tag": "approval:escalate", "task": task,
                                  "level": level, "evidence_hash": evidence_hash}}]},
    ]
    body.append({"tag": "column_set", "columns": [
        {"tag": "column", "width": "auto", "elements": [b]} for b in buttons]})
    return {
        "schema": "2.0",
        "config": {"width_mode": "default",
                   "summary": {"content": "S2 签字：%s" % task}},
        "header": {"title": {"tag": "plain_text", "content": "【S2 签字】%s" % task},
                   "subtitle": {"tag": "plain_text",
                                "content": "DRY-RUN · 等待人工确认"},
                   "template": "blue"},
        "body": {"elements": body},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--level", default="S2")
    ap.add_argument("--evidence-hash", default="")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    card = build_card(args.task, args.summary, args.level, args.evidence_hash)
    txt = json.dumps(card, ensure_ascii=False, indent=2)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(txt, encoding="utf-8")
        print("written", args.out)
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
