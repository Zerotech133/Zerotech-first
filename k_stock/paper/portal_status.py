# -*- coding: utf-8 -*-
"""模拟仓门户投影层（纯标准库，只读 URI，零 pandas 依赖）。

供 portal_api /api/paper_status 消费；CLI 自测：python k_stock/paper/portal_status.py
数据全部来自 mark 快照（行情依赖在快照时点固化），页面天然滞后至最近一次 mark 属预期。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"


def status() -> dict:
    if not DB.exists():
        return {"ok": False, "error": f"paper.db 不存在（模拟仓未初始化）: {DB}"}
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        marks = con.execute(
            "SELECT date,total,cash,market_value,pos_pct,pending,verify_ok "
            "FROM equity_marks ORDER BY date").fetchall()
        if not marks:
            return {"ok": False, "error": "无快照（跑 paper/cli.py mark 生成）"}
        latest = marks[-1]
        base = marks[0]["total"] or 1.0
        snap_date = latest["date"]
        positions = con.execute(
            "SELECT stock,qty,avg_cost,last,market_value,pnl,available "
            "FROM positions_snapshot WHERE date=? ORDER BY stock", (snap_date,)).fetchall()
        pending = con.execute(
            "SELECT id,ts,source,stock,side,qty,plan_date,reason FROM orders "
            "WHERE status='pending' ORDER BY id").fetchall()
        recent = con.execute(
            "SELECT id,ts,source,ref,stock,side,qty,status,plan_date,filled_price,fee,reason "
            "FROM orders ORDER BY id DESC LIMIT 20").fetchall()
        return {
            "ok": True,
            "as_of": snap_date,
            "latest": {
                "date": latest["date"], "total": latest["total"], "cash": latest["cash"],
                "market_value": latest["market_value"], "pos_pct": latest["pos_pct"],
                "pending": latest["pending"], "verify_ok": bool(latest["verify_ok"]),
                "cum_pct": round((latest["total"] / base - 1) * 100, 2),
            },
            "curve": [{"date": m["date"], "total": m["total"],
                       "cum_pct": round((m["total"] / base - 1) * 100, 2)} for m in marks],
            "positions": [dict(r) for r in positions],
            "pending_orders": [dict(r) for r in pending],
            "recent_orders": [dict(r) for r in recent],
        }
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
