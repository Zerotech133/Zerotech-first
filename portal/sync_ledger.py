# -*- coding: utf-8 -*-
"""交易台账快照生成（K-STOCK-353）：quant_backtest/positions.json → portal/data/ledger.json。

公网发布由 sync_publish.py 统一携带（ledger.html + data/ledger.json 已入其清单）。
summary 等权口径同统一日报（K-STOCK-349 判例：禁逐股收益率加总）。
计划任务 LedgerSync 每交易日 16:15 跑（盘后账本 16:00 落盘后）；可手动补跑。
用法: python sync_ledger.py
"""

import json
import os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "quant_backtest", "positions.json"))
OUT = os.path.join(HERE, "data", "ledger.json")
CST = timezone(timedelta(hours=8))


def build():
    d = json.load(open(SRC, encoding="utf-8"))
    open_ = d.get("open") or []
    pend = d.get("pending") or []
    closed = d.get("closed") or []
    wins = sum(1 for c in closed if c.get("pnl", 0) > 0)
    return {
        "ts": datetime.now(CST).strftime("%Y-%m-%d %H:%M"),
        "ledger_ts": d.get("ts"),
        "summary": {
            "open_n": len(open_),
            "pending_n": len(pend),
            "closed_n": len(closed),
            "win_rate": round(wins * 100.0 / len(closed), 1) if closed else 0,
            "avg_pnl": round(sum(e.get("pnl", 0) for e in open_) / len(open_) * 100, 2)
            if open_
            else 0,
            "closed_total": round(
                sum(c.get("pnl", 0) for c in closed) / len(closed) * 100, 2
            )
            if closed
            else 0,
        },
        "open": sorted(open_, key=lambda e: -e.get("pnl", 0)),
        "pending": pend,
        "closed": sorted(
            closed, key=lambda c: c.get("exit_date") or c.get("last_date") or ""
        ),
    }


def main():
    data = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(
        "LEDGER_SYNC %s (open=%d pending=%d closed=%d avg=%.2f%%)"
        % (
            OUT,
            data["summary"]["open_n"],
            data["summary"]["pending_n"],
            data["summary"]["closed_n"],
            data["summary"]["avg_pnl"],
        )
    )


if __name__ == "__main__":
    main()
