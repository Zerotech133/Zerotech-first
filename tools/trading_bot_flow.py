# -*- coding: utf-8 -*-
"""交易机器人人工确认 CLI（WO-BOT-CONFIRM）。

paper-only：trigger 只创建 pending；approve 才走 paper_book；reject 不执行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from agent_platform import trading_bot_flow as flow  # noqa: E402


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description="盯盘触发→人工确认→模拟执行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("trigger")
    p.add_argument("--symbol", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--action", default="BUY", choices=["BUY", "SELL", "WATCH"])
    p.add_argument("--price", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--confidence", type=float, default=0.6)
    p.add_argument("--reason", default="盯盘触发")
    p.add_argument("--source", default="WA1")
    p.add_argument("--report-id", default="")

    p = sub.add_parser("list")
    p.add_argument("--status", default="pending")

    p = sub.add_parser("show")
    p.add_argument("confirmation_id")

    p = sub.add_parser("approve")
    p.add_argument("confirmation_id")
    p.add_argument("--by", default="老田")
    p.add_argument("--note", default="")
    p.add_argument("--price", type=float)
    p.add_argument("--state")
    p.add_argument("--ledger")
    p.add_argument("--config")

    p = sub.add_parser("reject")
    p.add_argument("confirmation_id")
    p.add_argument("--by", default="老田")
    p.add_argument("--note", default="")

    p = sub.add_parser("card")
    p.add_argument("confirmation_id")
    p.add_argument("--out")

    args = ap.parse_args(argv)
    if args.cmd == "trigger":
        r = flow.create_confirmation(
            args.symbol, args.name, args.action, args.price, args.stop,
            confidence=args.confidence, reason=args.reason, source=args.source,
            source_report_id=args.report_id)
        _print({"status": "pending", "confirmation_id": r["confirmation_id"],
                "symbol": r["symbol"], "action": r["action"]})
        return 0
    if args.cmd == "list":
        _print({"items": flow.list_confirmations(args.status)})
        return 0
    if args.cmd == "show":
        rec = flow._load(args.confirmation_id)  # noqa: SLF001 CLI 内部
        _print(rec)
        return 0
    if args.cmd == "approve":
        r = flow.decide(args.confirmation_id, "approve", by=args.by, note=args.note,
                        price=args.price, paper_state_path=args.state,
                        paper_ledger_path=args.ledger, paper_config_path=args.config)
        _print({"status": r["status"], "confirmation_id": r["confirmation_id"],
                "execution": r.get("execution")})
        return 0 if r["status"] in ("executed", "blocked") else 1
    if args.cmd == "reject":
        r = flow.decide(args.confirmation_id, "reject", by=args.by, note=args.note)
        _print({"status": r["status"], "confirmation_id": r["confirmation_id"]})
        return 0
    if args.cmd == "card":
        rec = flow._load(args.confirmation_id)  # noqa: SLF001
        card = flow.build_feishu_card(rec)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
            print(args.out)
        else:
            _print(card)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
